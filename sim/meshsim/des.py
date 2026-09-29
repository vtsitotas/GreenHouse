"""Discrete-event simulation of the mesh: every frame, backoff, collision,
retry, buffer slot and wake-up is an event in time.

Model (see the spec for the reasoning behind each choice)
  topology  balanced layered tree: node (r, j) → parent (r−1, j); bridge = rank 0.
            Nodes hear ranks r−1, r, r+1; a fraction `net.hidden_frac` of same-rank
            pairs cannot hear each other (hidden terminals). Ranks r−1 and r+1 never
            hear each other, so hidden terminals also appear across a receiver.
  link      RSSI = sensitivity + link_margin + N(0, shadow σ), fixed per link; the
            parent link is the best of `des.parent_candidates` draws (firmware keeps the
            best-RSSI parent among those it hears, and re-parents after 3 tx fails);
            per-attempt PER from the same SINR→BER(DBPSK)→PER model as the catalogue.
  MAC       CSMA/CA: DIFS + uniform backoff in [0, CW] slots, CW doubling per retry,
            carrier sense of transmitters in range, unicast L2 ACK (SIFS + 304 µs),
            `radio.mac_retry` retransmissions; broadcasts are never ACKed.
            A reception fails if any overlapping transmitter is in range of the
            receiver, or the receiver itself is transmitting (half duplex).
  clock     each node runs its own copy of the CART pair model against its parent
            (OU drift + margin guard policy, 3 misses → orphan sweep).
  gateway   bridge RX queue (`bridge.ingress_queue`) → UART line → Pi → ACK line,
            each a FIFO single server; a full RX queue means no L2 ACK.
  schemes   phase1 (today's firmware), T1-ladder (hop-by-hop custody, stop-and-wait
            hop-ACK, relay buffer B), T2-window (common window, cut-through relay,
            ACK flood / reverse unicast / aggregate).
Energy per node = per-wake boot+sensor cost (same as the calculator) + radio-on
time at I_RX + airtime at I_TX + sleep + orphan sweeps.

Known simplifications: the tree is static (no re-parenting around a relay that
missed its sync — with one node per rank that relay cuts its whole subtree for
the cycle); ACK/hop-ACK frames sent by the bridge bypass the channel model;
L2 ACK frames occupy the medium but are never corrupted; trickle beacons and
CART RX_OPEN beacons (~0.6 % occupancy) are not simulated.
"""
import heapq
import math
import random
from collections import deque

from . import analytic as an
from . import calculator
from . import clock as clk

BRIDGE = -1


class PairClock:
    """One node's timing relation to its parent — cart_sim.run_policy, one cycle at a time."""

    def __init__(self, rng, T, bias, step, g_min, g_max, k=1.5, window=16, pad=0.05):
        self.rng, self.T, self.bias, self.step = rng, T, bias, step
        self.g_min, self.g_max, self.k, self.pad = g_min, g_max, k, pad
        self.w = self.est = self.carry = 0.0
        self.est_known = False
        self.g = g_max
        self.run_len = 0
        self.recent = deque(maxlen=window)

    def next_cycle(self):
        """→ (hit, listen_s, swept)"""
        self.w = 0.98 * self.w + self.rng.gauss(0, self.step)
        offset = self.carry + self.T * (self.bias + self.w) + self.rng.gauss(0, 0.020)
        err = offset - self.est
        g = self.g
        if abs(err) <= g / 2:
            if self.carry == 0:
                self.est = offset if not self.est_known else 0.7 * self.est + 0.3 * offset
                self.est_known = True
            self.carry = 0.0
            self.recent.append(abs(err))
            self.run_len = 0
            if len(self.recent) == self.recent.maxlen:
                self.g = min(self.g_max, max(self.g_min, 2 * self.k * max(self.recent) + self.pad))
            return True, err + g / 2, False
        self.carry = err
        self.run_len += 1
        self.g = min(self.g_max, g * 2)
        swept = False
        if self.run_len >= 3:
            swept = True
            self.run_len = 0
            self.carry = self.est = 0.0
            self.est_known = False
            self.g = self.g_max
        return False, g, swept


class Tx:
    __slots__ = ("src", "dst", "frame", "start", "data_end", "end", "overlaps")

    def __init__(self, src, dst, frame, start, data_end, end):
        self.src, self.dst, self.frame = src, dst, frame
        self.start, self.data_end, self.end = start, data_end, end
        self.overlaps = set()


class Level:
    """Time-weighted occupancy of a buffer."""

    def __init__(self, cap):
        self.cap, self.n, self.t, self.area, self.max = cap, 0, 0.0, 0.0, 0

    def set(self, now, n):
        self.area += self.n * (now - self.t)
        self.t, self.n = now, n
        self.max = max(self.max, n)

    def mean(self, now):
        area = self.area + self.n * (now - self.t)
        return area / now if now > 0 else 0.0


class Node:
    def __init__(self, nid, rank, col, cfg):
        self.id, self.rank, self.col = nid, rank, col
        self.awake = False
        self.txq = deque()
        self.transmitting = False
        self.retries = 0
        self.seq = 0
        self.own = deque()                       # own readings awaiting delivery (RTC buffer)
        self.relay = deque()                     # T1 custody buffer
        self.inflight = {}                       # seq → frame (T2/phase1, awaiting app ACK)
        self.dedup = deque(maxlen=32)            # (origin, seq, t)
        self.ack_dedup = deque(maxlen=8)
        self.lvl_own = Level(cfg["scheme.own_buffer"])
        self.lvl_relay = Level(cfg["scheme.relay_buffer"])
        self.awaiting_hopack = None
        self.st = {k: 0 for k in ("gen", "tx_attempts", "tx_coll", "tx_per", "tx_final_fail",
                                  "ttl_drop", "own_overflow", "relay_reject", "relay_lost",
                                  "dedup_drop", "sync_miss", "sweeps", "wakes", "hopack_timeout")}
        self.airtime = 0.0
        self.radio_on = 0.0
        self.listen = 0.0
        self.wake_t = None


class DES:
    def __init__(self, cfg, cat, seed=None, plan=None):
        self.cfg, self.cat = cfg, cat
        self.rng = random.Random(cfg["des.seed"] if seed is None else seed)
        self.plan = plan or calculator.compute(cfg, cat)
        self.q, self.seq, self.now = [], 0, 0.0
        R, W = cfg["net.ranks"], cfg["net.per_rank"]
        self.R, self.W, self.N = R, W, R * W
        self.tech = cfg["scheme.technique"]
        self.Tc = cfg["timing.T_s"] * cfg["timing.report_every"]
        self.rad = calculator._radio(cfg, cat)
        self.gw = calculator._gateway(cfg, cat, self.rad)
        self.I = calculator._currents(cfg)
        tl = calculator.Timeline()
        calculator._sensor_phase(tl, cfg, cat, self.I, radio_needed=True)
        self.wake_s, self.wake_mas = tl.awake_s, sum(tl.mas_by_state().values())
        # Firmware ladder: sensors warm up inside the receive window, so the radio-on
        # time is already the awake interval; only boot + sensor current are extra.
        self.fw_slot = cfg.get("scheme.t1_slot_s") if self.tech == "T1-ladder" else None
        self.l2_custody = self.tech == "T1-ladder" and cfg["scheme.t1_hop_ack"] == "l2"
        if self.fw_slot:
            from . import hardware as hwlib
            clim = hwlib.CLIMATE_SENSORS[cfg["hw.climate_sensor"]]
            soil = hwlib.SOIL_SENSORS[cfg["hw.soil_sensor"]]
            i_sens = clim["active_ma"] + soil["active_ma"]
            settle = max(clim["settle_s"], soil["settle_s"]) + clim["read_s"]
            self.wake_s = cfg["timing.t_boot_s"]
            self.wake_mas = cfg["timing.t_boot_s"] * self.I["cpu"] + min(settle, self.fw_slot) * i_sens
        self.nodes = [Node((r - 1) * W + j, r, j, cfg) for r in range(1, R + 1) for j in range(W)]
        self.max_ttl, self.margin = cfg["scheme.max_ttl"], cfg["scheme.ttl_margin"]
        # hearing: |Δrank| ≤ 1, except hidden same-rank pairs
        self.hidden = set()
        for r in range(1, R + 1):
            for a in range(W):
                for b in range(a + 1, W):
                    if self.rng.random() < cfg["net.hidden_frac"]:
                        self.hidden.add(((r - 1) * W + a, (r - 1) * W + b))
        sens = cat["RX_SENSITIVITY_1M_DBM"]
        self._rssi = {}
        self._sens, self._margin, self._sigma = sens, cfg["radio.link_margin_db"], cfg["des.shadow_sigma_db"]
        self._per_cache = {}
        self.active = []
        # gateway
        self.bq = deque()                        # bridge RX queue (frames waiting for UART)
        self.bq_lvl = Level(cfg["bridge.ingress_queue"])
        self.uart_busy = self.pi_busy = self.ackline_busy = False
        self.pi_q, self.ackline_q = deque(), deque()
        self.pi_seen = {}                        # (origin, seq) → first delivery time
        self.duplicates = 0
        self.bridge_rejects = 0
        self.latencies = []
        self.gen_log = {}                        # (origin, seq) → generation time
        # clocks
        g_min = cfg["sync.g_min_s"]
        step = clk.step_for(self.Tc, cfg["sync.step_per_300s"])
        g_max = cfg["sync.g_max_s"] or clk.g_max_rule(cfg["sync.g_max_rule"], cfg["sync.bias"], step, self.Tc,
                                                      cat["G_MAX_FACTOR"], g_min, cfg["sync.z"],
                                                      cfg["sync.g_cap_s"])
        self.clocks = [PairClock(random.Random(self.rng.random()), self.Tc, cfg["sync.bias"], step, g_min, g_max,
                                 k=cfg["sync.margin_k"])
                       for _ in self.nodes]
        for ck in self.clocks:                       # start every pair in steady state
            for _ in range(cfg["des.clock_burnin"]):
                ck.next_cycle()
        # trace
        self.trace, self.trace_until = [], cfg["des.trace_cycles"] * self.Tc
        self.trace_nodes = set(n.id for n in self.nodes) if self.N <= 60 else \
            {n.id for n in self.nodes if n.col == 0}
        self.series = {"bridge_queue": [], "relay_rank1": []}

    # ── event kernel ─────────────────────────────────────────────────────
    def at(self, t, fn, *args):
        heapq.heappush(self.q, (t, self.seq, fn, args))
        self.seq += 1

    def run(self):
        cycles = self.cfg["des.cycles"]
        for c in range(cycles):
            self.at(c * self.Tc, self.start_cycle, c)
        end = cycles * self.Tc
        while self.q and self.q[0][0] <= end:
            t, _, fn, args = heapq.heappop(self.q)
            self.now = t
            fn(*args)
        self.now = end
        for n in self.nodes:
            if n.awake:
                self.sleep(n)
        return self.results(end)

    # ── radio primitives ─────────────────────────────────────────────────
    def rank_of(self, nid):
        return 0 if nid == BRIDGE else self.nodes[nid].rank

    def hears(self, a, b):
        if a == b:
            return True
        ra, rb = self.rank_of(a), self.rank_of(b)
        if abs(ra - rb) > 1:
            return False
        if ra == rb and ra > 0 and (min(a, b), max(a, b)) in self.hidden:
            return False
        return True

    def per(self, src, dst, length):
        key = (min(src, dst), max(src, dst))
        if key not in self._rssi:
            # firmware parent choice: lowest rank, then best RSSI among the candidates it hears
            c = self.cfg["des.parent_candidates"] if self.is_parent_link(src, dst) else 1
            self._rssi[key] = self._sens + self._margin + max(self.rng.gauss(0, self._sigma)
                                                              for _ in range(max(1, c)))
        rssi = round(self._rssi[key], 1)
        k2 = (rssi, length)
        if k2 not in self._per_cache:
            self._per_cache[k2] = an.frame_error_rate(rssi, length, self._sens)
        return self._per_cache[k2]

    def is_parent_link(self, a, b):
        for x, y in ((a, b), (b, a)):
            if x != BRIDGE and self.parent(self.nodes[x]) == y:
                return True
        return False

    def parent(self, n):
        return BRIDGE if n.rank == 1 else n.id - self.W

    def child(self, n):
        return n.id + self.W if n.rank < self.R else None

    def frame_len(self, frame):
        kind = frame["kind"]
        if kind == "data":
            return self.rad["packet_b"]
        if kind == "agg":
            return self.cat["sizeof(MeshAck)"] + 2 * len(frame["seqs"])
        return self.cat["sizeof(MeshAck)"]

    def send(self, n, dst, frame, front=False):
        (n.txq.appendleft if front else n.txq.append)((dst, frame))
        if not n.transmitting and len(n.txq) == 1:
            self.attempt(n)

    def attempt(self, n):
        if not n.awake or n.transmitting or not n.txq:
            return
        cw = min((self.cfg["radio.cw"] + 1) * 2 ** n.retries - 1, an.CW_MAX)
        wait = an.DIFS_US / 1e6 + self.rng.randint(0, cw) * an.SLOT_US / 1e6
        self.at(self.now + wait, self.sense, n)

    def busy_until(self, n):
        until = None
        for tx in self.active:
            if tx.start > self.now - 1e-6 or tx.end <= self.now:
                continue
            if self.hears(n.id, tx.src) or (tx.dst != BRIDGE and tx.dst is not None and
                                            self.now >= tx.data_end and self.hears(n.id, tx.dst)):
                until = max(until or 0, tx.end)
        return until

    def sense(self, n):
        if not n.awake or n.transmitting or not n.txq:
            return
        busy = self.busy_until(n)
        if busy is not None:
            self.at(busy, self.attempt, n)
            return
        dst, frame = n.txq[0]
        air = an.airtime_us(self.frame_len(frame)) / 1e6
        ack = 0.0 if dst is None else (an.SIFS_US + an.ack_airtime_us()) / 1e6
        tx = Tx(n.id, dst, frame, self.now, self.now + air, self.now + air + ack)
        for o in self.active:
            if o.data_end > self.now:
                o.overlaps.add(n.id)
                tx.overlaps.add(o.src)
        self.active.append(tx)
        n.transmitting = True
        n.st["tx_attempts"] += 1
        n.airtime += air
        if self.now < self.trace_until and n.id in self.trace_nodes:
            self.trace.append((n.id, round(self.now, 6), round(self.now + air, 6), "tx"))
        self.at(tx.data_end, self.tx_data_end, n, tx)

    def _rx_ok(self, tx, rid):
        """Did receiver rid get tx's data? (collision by any overlap it hears, half duplex, PER)"""
        if rid != BRIDGE:
            r = self.nodes[rid]
            if not r.awake:
                return False, "asleep"
            if rid in tx.overlaps:
                return False, "coll"
        if any(self.hears(rid, s) for s in tx.overlaps if s != tx.src):
            return False, "coll"
        if self.rng.random() < self.per(tx.src, rid, self.frame_len(tx.frame)):
            return False, "per"
        return True, ""

    def tx_data_end(self, n, tx):
        if tx.dst is None:                                   # broadcast
            self.active.remove(tx)
            n.transmitting = False
            if n.txq:
                n.txq.popleft()
            for nb in self._neighbors(n.id):
                ok, _ = self._rx_ok(tx, nb)
                if ok:
                    self.on_receive(nb, n.id, tx.frame)
            self.attempt(n)
            return
        ok, why = self._rx_ok(tx, tx.dst)
        if ok and tx.dst == BRIDGE and tx.frame["kind"] == "data" and \
                len(self.bq) >= self.cfg["bridge.ingress_queue"]:
            ok, why = False, "full"
            self.bridge_rejects += 1
        if ok:
            self.on_receive(tx.dst, n.id, tx.frame)
        self.at(tx.end, self.tx_done, n, tx, ok, why)

    def tx_done(self, n, tx, ok, why):
        self.active.remove(tx)
        n.transmitting = False
        if not n.txq:                        # went to sleep mid-exchange: queue already cleared
            return
        if ok:
            n.txq.popleft()
            n.retries = 0
            self.on_sent(n, tx.dst, tx.frame)
        else:
            if why == "coll":
                n.st["tx_coll"] += 1
            elif why == "per":
                n.st["tx_per"] += 1
            n.retries += 1
            if n.retries > self.cfg["radio.mac_retry"]:
                n.txq.popleft()
                n.retries = 0
                n.st["tx_final_fail"] += 1
                self.on_fail(n, tx.dst, tx.frame)
        self.attempt(n)

    def _neighbors(self, nid):
        r = self.rank_of(nid)
        out = [BRIDGE] if r == 1 and nid != BRIDGE else []
        for rr in (r - 1, r, r + 1):
            if 1 <= rr <= self.R:
                for j in range(self.W):
                    m = (rr - 1) * self.W + j
                    if m != nid and self.hears(nid, m):
                        out.append(m)
        return out

    # ── power states ─────────────────────────────────────────────────────
    def wake(self, n, listen=0.0):
        if n.awake:
            return
        n.awake = True
        n.wake_t = self.now
        n.listen += listen
        n.st["wakes"] += 1

    def sleep(self, n):
        if not n.awake:
            return
        n.awake = False
        n.radio_on += self.now - n.wake_t
        if n.wake_t < self.trace_until and n.id in self.trace_nodes:
            self.trace.append((n.id, round(n.wake_t, 6), round(self.now, 6), "awake"))
        n.txq.clear()
        n.transmitting = False
        n.retries = 0

    # ── readings ─────────────────────────────────────────────────────────
    def new_reading(self, n):
        n.seq += 1
        n.st["gen"] += 1
        f = {"kind": "data", "origin": n.id, "seq": n.seq, "gen": self.now, "rank": n.rank}
        self.gen_log[(n.id, n.seq)] = self.now
        n.own.append(f)
        if len(n.own) > self.cfg["scheme.own_buffer"]:
            n.own.popleft()
            n.st["own_overflow"] += 1
        n.lvl_own.set(self.now, len(n.own))

    def tx_ttl(self, n):
        return min(n.rank + self.margin, self.max_ttl)

    def dedup_seen(self, n, key):
        horizon = self.now - self.cat["MESH_DEDUP_WINDOW_MS"] / 1000
        for k, t in n.dedup:
            if k == key and t >= horizon:
                return True
        n.dedup.append((key, self.now))
        return False

    # ── gateway pipeline ─────────────────────────────────────────────────
    def bridge_rx(self, frame):
        self.bq.append(frame)
        self.bq_lvl.set(self.now, len(self.bq))
        self.series["bridge_queue"].append((round(self.now, 4), len(self.bq)))
        if not self.uart_busy:
            self.uart_next()

    def uart_next(self):
        if not self.bq:
            self.uart_busy = False
            return
        self.uart_busy = True
        f = self.bq.popleft()
        self.bq_lvl.set(self.now, len(self.bq))
        self.at(self.now + self.gw["t_uart"], self.uart_done, f)

    def uart_done(self, f):
        self.pi_q.append(f)
        if not self.pi_busy:
            self.pi_next()
        self.uart_next()

    def pi_next(self):
        if not self.pi_q:
            self.pi_busy = False
            return
        self.pi_busy = True
        f = self.pi_q.popleft()
        self.at(self.now + self.cfg["pi.process_s"], self.pi_done, f)

    def pi_done(self, f):
        key = (f["origin"], f["seq"])
        if key in self.pi_seen:
            self.duplicates += 1
        else:
            self.pi_seen[key] = self.now
            self.latencies.append((self.nodes[f["origin"]].rank, self.now - f["gen"]))
        if self.tech != "T1-ladder":
            self.ackline_q.append(f)
            if not self.ackline_busy:
                self.ackline_next()
        self.pi_next()

    def ackline_next(self):
        if not self.ackline_q:
            self.ackline_busy = False
            return
        self.ackline_busy = True
        f = self.ackline_q.popleft()
        self.at(self.now + self.gw["t_uart_ack"], self.ack_ready, f)

    def ack_ready(self, f):
        self.ackline_next()
        mode = self.cfg["scheme.t2_ack"] if self.tech == "T2-window" else "flood"
        target = self.nodes[f["origin"]]
        if mode == "aggregate":
            self.agg_pending.setdefault(target.col, []).append((f["origin"], f["seq"]))
            if self.now >= self.agg_after and not self.ackline_q:   # late ACKs: flush as they come
                self.send_aggregates()
            return
        ttl0 = an.ack_ttl0(target.rank, self.margin, self.max_ttl, self.max_ttl)
        ack = {"kind": "ack", "target": f["origin"], "seq": f["seq"], "ttl": ttl0}
        if mode == "flood":
            self.bridge_bcast(ack)
        else:
            self.bridge_ucast(self.nodes[target.col], ack)   # rank-1 node on the target's column

    def bridge_bcast(self, frame):
        tx = Tx(BRIDGE, None, frame, self.now, self.now + an.airtime_us(self.frame_len(frame)) / 1e6,
                self.now + an.airtime_us(self.frame_len(frame)) / 1e6)
        for o in self.active:
            if o.data_end > self.now:
                o.overlaps.add(BRIDGE)
                tx.overlaps.add(o.src)
        self.active.append(tx)
        self.at(tx.data_end, self._bridge_bcast_end, tx)

    def _bridge_bcast_end(self, tx):
        self.active.remove(tx)
        for j in range(self.W):
            ok, _ = self._rx_ok(tx, j)
            if ok:
                self.on_receive(j, BRIDGE, tx.frame)

    def bridge_ucast(self, dst_node, frame):
        ok = dst_node.awake and self.rng.random() >= self.per(BRIDGE, dst_node.id, self.frame_len(frame))
        air = an.airtime_us(self.frame_len(frame)) / 1e6
        if ok:
            self.at(self.now + air, self.on_receive, dst_node.id, BRIDGE, frame)

    # ── scheme dispatch ──────────────────────────────────────────────────
    def start_cycle(self, c):
        if self.tech == "phase1":
            self.cycle_phase1(c)
        elif self.tech == "T1-ladder":
            self.cycle_t1(c)
        else:
            self.cycle_t2(c)
        self.series["relay_rank1"].append((round(self.now, 2),
                                           sum(len(self.nodes[j].relay) for j in range(self.W))))

    def sync_step(self, n):
        if n.rank == 1:
            return True, 0.0
        hit, listen, swept = self.clocks[n.id].next_cycle()
        if not hit:
            n.st["sync_miss"] += 1
        if swept:
            n.st["sweeps"] += 1
        return hit, listen

    # phase1 ──────────────────────────────────────────────────────────────
    def cycle_phase1(self, c):
        for n in self.nodes:
            t = self.now + self.rng.uniform(0, self.Tc * 0.9)
            self.at(t, self._p1_wake, n)

    def _p1_wake(self, n):
        self.wake(n)
        self.new_reading(n)
        if n.rank == 1:
            self.flush_own(n, self.cfg["timing.app_ack_wait_s"] + self.rad["t_frame"] * 12)
        else:
            self.at(self.now + self.cat["MESH_WAKE_DISCOVERY_MS"] / 1000, self.sleep, n)

    def flush_own(self, n, stay_s):
        """Send own buffer + new reading (≤ in-flight max) to the parent, await app ACKs."""
        n.inflight = {}
        batch = list(n.own)[-self.cat["MESH_INFLIGHT_MAX"]:]
        for f in batch:
            g = dict(f, ttl=self.tx_ttl(n))
            n.inflight[f["seq"]] = f
            self.send(n, self.parent(n), g)
        self.at(self.now + stay_s, self.settle, n)

    def settle(self, n):
        acked = {s for s, f in n.inflight.items() if f.get("acked")}
        n.own = deque(f for f in n.own if f["seq"] not in acked)
        n.lvl_own.set(self.now, len(n.own))
        n.inflight = {}
        if self.tech != "T2-window":
            self.sleep(n)

    # T2 ──────────────────────────────────────────────────────────────────
    def cycle_t2(self, c):
        window = self.cfg["des.window_s"] or self.plan["scheme"]["window_s"]
        self.agg_pending = {}
        t0 = self.now
        self.agg_after = t0 + self.plan["scheme"].get("up_time_s", 0) + self.cfg["pi.process_s"]
        for n in self.nodes:
            self.new_reading(n)
            hit, listen = self.sync_step(n)
            if not hit:
                n.listen += listen                   # listened a whole guard, caught nothing
                continue
            self.wake(n, listen)
            self.at(t0 + self.rng.uniform(0, self.cfg["radio.jitter_s"]), self.flush_own, n, window * 0.999)
            self.at(t0 + window, self.sleep, n)
        if self.cfg["scheme.t2_ack"] == "aggregate":
            self.at(self.agg_after, self.send_aggregates)

    def send_aggregates(self):
        for col, items in self.agg_pending.items():
            self.bridge_ucast(self.nodes[col], {"kind": "agg", "seqs": items})
        self.agg_pending = {}

    # T1 ──────────────────────────────────────────────────────────────────
    def cycle_t1(self, c):
        D = {int(k): v for k, v in self.plan["scheme"]["slots_s"].items()}
        start, t = {}, self.now + (D[self.R] if self.fw_slot else 0.0)
        for r in range(self.R, 0, -1):
            start[r] = t
            t += D[r]
        self.t1_start, self.t1_D = start, D
        # Columns = branches under different rank-1 nodes. In phase (worst case) or,
        # like the firmware, each rank-1 node free-running with its own fixed phase.
        if not hasattr(self, "col_phase"):
            span = t - self.now
            room = max(0.0, self.Tc - span)
            self.col_phase = [0.0 if self.cfg["net.phase_sync"] else self.rng.uniform(0, room)
                              for _ in range(self.W)]
        for n in self.nodes:
            ph = self.col_phase[n.col]
            if n.rank < self.R:
                wake_at = start[n.rank + 1]
            else:                            # firmware: even a leaf opens its window
                wake_at = start[n.rank] - (D[n.rank] if self.fw_slot else 0.0)
            self.at(wake_at + ph, self.t1_wake, n)
            self.at(start[n.rank] + ph, self.t1_tx_slot, n)
            self.at(start[n.rank] + D[n.rank] + ph, self.t1_slot_end, n)

    def t1_wake(self, n):
        self.wake(n)
        self.new_reading(n)

    def t1_tx_slot(self, n):
        """The child woke G/2 early and listened *before* this instant; a relay was
        already awake for its own children, so only listening beyond that costs extra."""
        g = self.clocks[n.id].g
        hit, listen = self.sync_step(n)
        if n.rank >= 2 and self.fw_slot:     # awake beyond our own window: early + lateness
            early = max(0.0, g / 2 - self.fw_slot)
            n.listen += max(0.0, early + listen - g / 2)
        elif n.rank >= 2:
            held = self.t1_D[n.rank + 1] if n.rank < self.R else 0.0
            n.listen += max(0.0, listen - held)
        if not hit:
            self.sleep(n)                    # guard expired without the parent's beacon
            return
        self.at(self.now + self.rng.uniform(0, self.cfg["radio.jitter_s"]), self.t1_send_next, n)

    def t1_queue(self, n):
        return list(n.own) + list(n.relay)

    def t1_send_next(self, n):
        if not n.awake or n.awaiting_hopack is not None:
            return
        q = self.t1_queue(n)
        if not q:
            self.sleep(n)
            return
        f = q[0]
        par = self.parent(n)
        if par != BRIDGE and len(self.nodes[par].relay) >= self.cfg["scheme.relay_buffer"]:
            return                           # parent beacons BUF_FULL: hold for next cycle
        g = dict(f, ttl=f.get("ttl_left", self.tx_ttl(n)) if f["origin"] != n.id else self.tx_ttl(n))
        n.awaiting_hopack = (f["origin"], f["seq"])
        self.send(n, par, g)
        if not self.l2_custody:
            self.at(self.now + 0.05, self.t1_hopack_timeout, n, n.awaiting_hopack)

    def t1_hopack_timeout(self, n, key):
        if n.awaiting_hopack == key:
            n.awaiting_hopack = None
            n.st["hopack_timeout"] += 1
            self.t1_send_next(n)

    def t1_slot_end(self, n):
        self.sleep(n)
        n.awaiting_hopack = None

    def t1_release(self, n, key):
        """Custody handed over: drop the frame from own or relay buffer."""
        for buf, lvl in ((n.own, n.lvl_own), (n.relay, n.lvl_relay)):
            for f in buf:
                if (f["origin"], f["seq"]) == key:
                    buf.remove(f)
                    lvl.set(self.now, len(buf))
                    break
        n.awaiting_hopack = None
        self.t1_send_next(n)

    # ── MAC callbacks ────────────────────────────────────────────────────
    def on_sent(self, n, dst, frame):
        # Firmware custody: the parent's L2 ACK hands the frame over.
        if self.l2_custody and frame["kind"] == "data" and \
                n.awaiting_hopack == (frame["origin"], frame["seq"]):
            self.t1_release(n, n.awaiting_hopack)

    def on_fail(self, n, dst, frame):
        if frame["kind"] == "data" and frame["origin"] != n.id and self.tech != "T1-ladder":
            n.st["relay_lost"] += 1           # cut-through relay: nothing keeps it
        if self.l2_custody and frame["kind"] == "data" and \
                n.awaiting_hopack == (frame["origin"], frame["seq"]):
            n.awaiting_hopack = None          # keep the frame; retry after a jitter
            self.at(self.now + self.rng.uniform(0, self.cfg["radio.jitter_s"]), self.t1_send_next, n)

    def on_receive(self, rid, src, frame):
        kind = frame["kind"]
        if rid == BRIDGE:
            if kind == "data":
                if self.tech == "T1-ladder":
                    self.bridge_rx(frame)
                    if not self.l2_custody:
                        self.bridge_ucast(self.nodes[src], {"kind": "hopack", "key": (frame["origin"], frame["seq"])})
                else:
                    self.bridge_rx(frame)
            return
        n = self.nodes[rid]
        if kind == "data":
            key = (frame["origin"], frame["seq"])
            if self.tech == "T1-ladder":
                if any((f["origin"], f["seq"]) == key for f in n.relay):
                    if not self.l2_custody:
                        self.send(n, src, {"kind": "hopack", "key": key}, front=True)   # re-ACK a resend
                    return
                if frame["ttl"] == 0:
                    n.st["ttl_drop"] += 1
                    return
                if len(n.relay) >= self.cfg["scheme.relay_buffer"]:
                    n.st["relay_reject"] += 1
                    return
                n.relay.append(dict(frame, ttl_left=frame["ttl"] - 1))
                n.lvl_relay.set(self.now, len(n.relay))
                if not self.l2_custody:
                    self.send(n, src, {"kind": "hopack", "key": key}, front=True)
                return
            if self.dedup_seen(n, key):
                n.st["dedup_drop"] += 1
                return
            if frame["ttl"] == 0 or frame["ttl"] > self.max_ttl:
                n.st["ttl_drop"] += 1
                return
            self.send(n, self.parent(n), dict(frame, ttl=frame["ttl"] - 1))
        elif kind == "hopack":
            if n.awaiting_hopack == frame["key"]:
                self.t1_release(n, frame["key"])
        elif kind == "ack":
            key = (frame["target"], frame["seq"])
            if key in n.ack_dedup:
                return
            n.ack_dedup.append(key)
            if frame["target"] == n.id and frame["seq"] in n.inflight:
                n.inflight[frame["seq"]]["acked"] = True
                if self.tech == "phase1" and all(f.get("acked") for f in n.inflight.values()):
                    self.settle(n)                   # firmware leaves the ACK wait early
            mode = self.cfg["scheme.t2_ack"] if self.tech == "T2-window" else "flood"
            if mode == "flood":
                # phase1: sleepy nodes never re-flood (firmware gate); T2 uses the CART gate
                if frame["ttl"] > 0 and self.tech == "T2-window":
                    self.send(n, None, dict(frame, ttl=frame["ttl"] - 1))
            elif frame["target"] != n.id:
                ch = self.child(n)
                if ch is not None:
                    self.send(n, ch, frame)
        elif kind == "agg":
            mine = [s for (o, s) in frame["seqs"] if o == n.id]
            for s in mine:
                if s in n.inflight:
                    n.inflight[s]["acked"] = True
            rest = [(o, s) for (o, s) in frame["seqs"] if o != n.id]
            ch = self.child(n)
            if rest and ch is not None:
                self.send(n, ch, {"kind": "agg", "seqs": rest})

    # ── results ──────────────────────────────────────────────────────────
    def results(self, end):
        I, Tc, days = self.I, self.Tc, end / 86400
        per_rank = []
        for r in range(1, self.R + 1):
            ns = [n for n in self.nodes if n.rank == r]
            agg = {k: sum(n.st[k] for n in ns) for k in ns[0].st}
            radio_on = sum(n.radio_on for n in ns) / len(ns)
            listen = sum(n.listen for n in ns) / len(ns)
            air = sum(n.airtime for n in ns) / len(ns)
            wakes = sum(n.st["wakes"] for n in ns) / len(ns)
            gen_wakes = self.cfg["des.cycles"]
            awake_total = gen_wakes * self.wake_s + radio_on + listen
            mas = (gen_wakes * self.wake_mas + (radio_on + listen) * I["rx"] + air * (I["tx"] - I["rx"])
                   + max(0.0, end - awake_total) * I["sleep"])
            # Orphan sweeps are rare (~1 per tens of cycles); a short run cannot sample them,
            # so energy uses the pair model's expected rate unless des.sweep_energy=observed.
            if self.cfg["des.sweep_energy"] == "observed" or self.tech == "phase1" or r == 1:
                sweeps = agg["sweeps"] / len(ns)
            else:
                sweeps = self.plan["sync"]["drops_per_year"] / 365 * days
            mas += sweeps * Tc * I["rx"]
            mah_day = mas / 3600 / days
            gen = agg["gen"]
            deliv = sum(1 for (o, s) in self.pi_seen if self.nodes[o].rank == r)
            lat = sorted(l for rr, l in self.latencies if rr == r)
            attempts = agg["tx_attempts"] or 1
            per_rank.append({
                "rank": r, "gen": gen, "delivered": deliv,
                "pdr": round(deliv / gen, 4) if gen else 0.0,
                "latency_mean_s": round(sum(lat) / len(lat), 3) if lat else math.inf,
                "latency_p95_s": round(lat[int(0.95 * (len(lat) - 1))], 3) if lat else math.inf,
                "mah_day": round(mah_day, 3), "awake_s_per_cycle": round(awake_total / gen_wakes, 4),
                "time_on_air_ms_per_cycle": round(1000 * air / gen_wakes, 3),
                "p_collision_per_attempt": round(agg["tx_coll"] / attempts, 5),
                "p_per_per_attempt": round(agg["tx_per"] / attempts, 5),
                "mac_final_fail": agg["tx_final_fail"],
                "sync_miss_rate": round(agg["sync_miss"] / (gen or 1), 4) if r >= 2 else 0.0,
                "sweeps": agg["sweeps"],
                "own_buffer_mean": round(sum(n.lvl_own.mean(end) for n in ns) / len(ns), 3),
                "own_buffer_max": max(n.lvl_own.max for n in ns),
                "relay_buffer_mean": round(sum(n.lvl_relay.mean(end) for n in ns) / len(ns), 3),
                "relay_buffer_max": max(n.lvl_relay.max for n in ns),
                "relay_buffer_util_max": round(max(n.lvl_relay.max for n in ns) / self.cfg["scheme.relay_buffer"], 3),
                "drops": {k: agg[k] for k in ("ttl_drop", "own_overflow", "relay_reject", "relay_lost",
                                                "dedup_drop", "hopack_timeout")},
            })
        gen = sum(x["gen"] for x in per_rank)
        deliv = len(self.pi_seen)
        # readings from the last cycle may still be in buffers when the run stops:
        # "settled" PDR counts only readings generated before the last cycle began
        last_start = (self.cfg["des.cycles"] - 1) * Tc
        settled = [k for k, t in self.gen_log.items() if t < last_start]
        pdr_settled = (sum(1 for k in settled if k in self.pi_seen) / len(settled)) if settled else None
        attempts = sum(n.st["tx_attempts"] for n in self.nodes) or 1
        all_lat = sorted(l for _, l in self.latencies)
        worst = max(per_rank, key=lambda x: x["mah_day"])
        return {
            "summary": {
                "technique": self.tech, "nodes": self.N, "ranks": self.R, "cycles": self.cfg["des.cycles"],
                "generated": gen, "delivered_unique": deliv, "pdr": round(deliv / gen, 4) if gen else 0.0,
                "pdr_settled": round(pdr_settled, 4) if pdr_settled is not None else None,
                "latency_median_s": round(all_lat[len(all_lat) // 2], 3) if all_lat else math.inf,
                "duplicates_at_pi": self.duplicates, "bridge_rejects": self.bridge_rejects,
                "bridge_queue_max": self.bq_lvl.max,
                "latency_mean_s": round(sum(all_lat) / len(all_lat), 3) if all_lat else math.inf,
                "latency_p95_s": round(all_lat[int(0.95 * (len(all_lat) - 1))], 3) if all_lat else math.inf,
                "p_collision_per_attempt": round(sum(n.st["tx_coll"] for n in self.nodes) / attempts, 5),
                "tx_attempts": attempts,
                "worst_rank": worst["rank"], "worst_mah_day": worst["mah_day"],
                "leaf_mah_day": per_rank[-1]["mah_day"],
            },
            "per_rank": per_rank,
            "trace": [{"node": a, "rank": self.rank_of(a), "t0": t0, "t1": t1, "state": s}
                      for a, t0, t1, s in self.trace],
            "series": {k: v[:5000] for k, v in self.series.items()},
        }


def simulate(cfg, cat, seeds=None):
    """Run the DES over one or several seeds; with several, add mean ± 95 % CI of key metrics."""
    seeds = seeds or [cfg["des.seed"] + i for i in range(max(1, cfg["des.seeds"]))]
    plan = calculator.compute(cfg, cat)
    runs = [DES(cfg, cat, seed=s, plan=plan).run() for s in seeds]
    out = runs[0]
    out["plan"] = {"summary": plan["summary"], "scheme": plan["scheme"], "per_rank": plan["per_rank"],
                   "checks": plan["checks"]}
    b = plan["battery"]
    for x in out["per_rank"]:
        x["autonomy_dark_days"] = round(b["usable_mah"] / (x["mah_day"] + b["self_discharge_mah_day"]), 1)
    s = out["summary"]
    worst = max(out["per_rank"], key=lambda x: x["mah_day"])
    s["worst_autonomy_dark_days"] = worst["autonomy_dark_days"]
    s["pdr_ontime_mean"] = s["pdr_settled"] if s["pdr_settled"] is not None else s["pdr"]  # index.csv column
    s["latency_max_s"] = s["latency_p95_s"]
    s["checks_failed"] = plan["summary"]["checks_failed"]
    if len(runs) > 1:
        ci = {}
        for key in ("pdr", "worst_mah_day", "leaf_mah_day", "latency_mean_s", "p_collision_per_attempt"):
            xs = [r["summary"][key] for r in runs if not math.isinf(r["summary"][key])]
            if len(xs) > 1:
                m = sum(xs) / len(xs)
                sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
                ci[key] = {"mean": round(m, 5), "ci95": round(1.96 * sd / math.sqrt(len(xs)), 5), "n": len(xs)}
        out["ci"] = ci
    return out
