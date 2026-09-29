"""Deterministic calculator: one config in, every derived number out.

For each rank it builds the per-cycle timeline of one node — which state the
node is in, for how long, at what current — and integrates it. Network-level
quantities (slot lengths, windows, gateway pipeline, latency, buffers) come
from the same timing primitives. Stochastic effects (sync misses, link loss,
collisions) enter as expected values; the DES engine checks them.

Topology: balanced layered tree — `per_rank` nodes on every rank, one child
per parent, so a rank-r node carries a subtree of (R − r + 1) readings per
communication cycle. Everything is in seconds, mA and mAs unless named.
"""
import math

from . import analytic as an
from . import clock
from . import hardware as hw

STATES = ("boot", "sensor", "cpu", "radio_init", "sync_listen", "rx_window", "tx_air",
          "radio_other", "sample_wake")


class Timeline:
    def __init__(self):
        self.items = []          # (state, seconds, mA)

    def add(self, state, seconds, ma):
        if seconds > 0:
            self.items.append((state, seconds, ma))

    @property
    def awake_s(self):
        return sum(s for _, s, _ in self.items)

    def mas_by_state(self):
        out = {}
        for state, s, ma in self.items:
            out[state] = out.get(state, 0.0) + s * ma
        return out

    def seconds_by_state(self):
        out = {}
        for state, s, _ in self.items:
            out[state] = out.get(state, 0.0) + s
        return out


# ── primitives ──────────────────────────────────────────────────────────────
def _radio(cfg, cat):
    """Frame/exchange timings in seconds and the expected MAC effort."""
    k = cfg["timing.report_every"]
    pkt = cat["MESH_PACKET_LEN"] + 6 * (k - 1)          # +int16 T,H,S per extra reading
    ack = cat["sizeof(MeshAck)"]
    backoff = an.mean_backoff_slots(cfg["radio.cw"]) * an.SLOT_US
    ctrl = an.DIFS_US + an.SIFS_US + an.ack_airtime_us()
    t_air = an.airtime_us(pkt) / 1e6
    p_att = an.frame_error_rate(cat["RX_SENSITIVITY_1M_DBM"] + cfg["radio.link_margin_db"], pkt)
    r = cfg["radio.mac_retry"]
    e_att = (1 - p_att ** (r + 1)) / (1 - p_att) if p_att < 1 else r + 1
    t_exch = (ctrl + backoff) / 1e6 + t_air
    t_ack_air = an.airtime_us(ack) / 1e6
    return {
        "packet_b": pkt, "t_air": t_air, "t_exch": t_exch, "attempts_mean": e_att,
        "t_frame": t_exch * e_att + cfg["radio.hop_proc_s"],
        "t_ack_air": t_ack_air,
        "t_ack_exch": (ctrl + backoff) / 1e6 + t_ack_air,
        "t_ack_bcast": (an.DIFS_US + backoff) / 1e6 + t_ack_air,
        "t_beacon_air": an.airtime_us(cat["sizeof(MeshBeacon)"]) / 1e6,
        "per_attempt": p_att, "p_link_fail": p_att ** (r + 1),
    }


def _gateway(cfg, cat, rad):
    baud = cfg["bridge.baud"]
    if cfg["bridge.framing"] == "hex_json":
        line = an.bridge_frame_line_bytes(cat["BRIDGE_FRAME_FORMAT"], rad["packet_b"], True)
        ack_line = an.pi_ack_line_bytes(65535, ok=True, ttl=64)
    else:  # binary framing, firmware mesh_uart.h: A5 5A type len payload CRC-16
        line = rad["packet_b"] + cat["MESH_UART_OVERHEAD"]
        ack_line = cat["MESH_UART_ACK_LEN"] + cat["MESH_UART_OVERHEAD"]
    t_uart = line * an.uart_byte_time_s(baud)
    t_uart_ack = ack_line * an.uart_byte_time_s(baud)
    t_gw = max(t_uart, cfg["pi.process_s"], t_uart_ack)
    return {"line_b": line, "t_uart": t_uart, "ack_line_b": ack_line, "t_uart_ack": t_uart_ack,
            "t_gw": t_gw, "max_frames_s": 1 / t_gw,
            "ack_rtt": t_uart + cfg["pi.process_s"] + t_uart_ack + rad["t_ack_bcast"]}


def _currents(cfg):
    lumped = cfg["energy.model"] == "lumped"
    I = {"tx": cfg["energy.i_tx_ma"], "rx": cfg["energy.i_rx_ma"], "cpu": cfg["energy.i_cpu_ma"],
         "cpu_idle": cfg["energy.i_cpu_idle_ma"], "light": cfg["energy.i_light_sleep_ma"]}
    if lumped:
        I = {k: cfg["energy.i_active_lumped_ma"] for k in I}
    sleep = (hw.BOARDS[cfg["hw.board"]]["sleep_ma"] + hw.DIVIDERS[cfg["hw.divider"]]["sleep_ma"]
             + hw.RTC_CLOCKS[cfg["hw.rtc_clock"]]["sleep_ma"])
    I["sleep"] = sleep
    return I


def _sensor_phase(tl, cfg, cat, I, radio_needed):
    """Boot, sensors, and radio bring-up, as the firmware (or a variant) does it."""
    clim = hw.CLIMATE_SENSORS[cfg["hw.climate_sensor"]]
    soil = hw.SOIL_SENSORS[cfg["hw.soil_sensor"]]
    settle = max(clim["settle_s"], soil["settle_s"])
    i_sens = clim["active_ma"] + soil["active_ma"]
    adc = cat["BATT_ADC_SAMPLES"] * cat["BATT_ADC_SAMPLE_DELAY_MS"] / 1000
    mode = cfg["hw.warmup_mode"]
    tl.add("boot", cfg["timing.t_boot_s"], I["cpu"])
    mcu = {"radio_on": I["rx"], "cpu_idle": I["cpu_idle"], "light_sleep": I["light"]}[mode]
    tl.add("sensor", settle, mcu + i_sens)
    tl.add("cpu", clim["read_s"], I["cpu"] + i_sens)
    tl.add("cpu", adc, I["cpu"])
    if radio_needed:
        init = cfg["timing.radio_init_s"]
        hidden = settle if mode == "radio_on" else 0.0   # firmware overlaps init with warm-up
        tl.add("radio_init", max(0.0, init - hidden), I["rx"])


def _sample_only_wake(cfg, cat, I):
    """A wake that only samples and stores (batching: report_every > 1)."""
    tl = Timeline()
    _sensor_phase(tl, cfg, cat, I, radio_needed=False)
    return tl


def _add_tx(tl, n_frames, rad, I):
    """n unicast data exchanges: airtime at TX current, the rest radio-on."""
    air = n_frames * rad["t_air"] * rad["attempts_mean"]
    total = n_frames * rad["t_frame"]
    tl.add("tx_air", air, I["tx"])
    tl.add("radio_other", total - air, I["rx"])


def _sync(cfg, cat, Tc):
    bias = cfg["sync.bias"]
    step = clock.step_for(Tc, cfg["sync.step_per_300s"])
    g_min = cfg["sync.g_min_s"]
    g_max = cfg["sync.g_max_s"] or clock.g_max_rule(cfg["sync.g_max_rule"], bias, step, Tc,
                                                     cat["G_MAX_FACTOR"], g_min, cfg["sync.z"],
                                                     cfg["sync.g_cap_s"])
    st = clock.pair_stats(Tc, bias, step, cfg["sync.policy"], g_min, g_max, cfg["sync.cycles"],
                          k=cfg["sync.margin_k"])
    return dict(st, step=step, g_max=g_max, g_min=g_min, bias=bias,
                wander_std=clock.wander_std_s(step, Tc), rule=cfg["sync.g_max_rule"])


# ── schemes ─────────────────────────────────────────────────────────────────
def _scheme_phase1(cfg, cat, R, rad, gw, I):
    """Today's firmware: sleepy nodes never become parents → only rank 1 routes."""
    rows = []
    for r in range(1, R + 1):
        tl = Timeline()
        _sensor_phase(tl, cfg, cat, I, radio_needed=True)
        tl.add("tx_air", rad["t_beacon_air"], I["tx"])                  # wake beacon
        if r == 1:
            _add_tx(tl, 1, rad, I)
            tl.add("rx_window", gw["ack_rtt"], I["rx"])                  # app-ACK wait (returns early)
            routed, latency = True, tl.awake_s + gw["t_gw"]
        else:
            tl.add("sync_listen", cat["MESH_WAKE_DISCOVERY_MS"] / 1000, I["rx"])  # no parent found
            routed, latency = False, math.inf
        if cfg["energy.model"] == "lumped" and r == 1:
            tl = Timeline()
            tl.add("radio_other", cfg["energy.phase1_lumped_awake_s"], I["rx"])
        rows.append({"rank": r, "tl": tl, "routed": routed, "latency_s": latency,
                     "buffer_peak": 0 if routed else cat["MESH_DATA_BUFFER_SIZE"],
                     "sync_hops": 0, "rx_frames": 0, "tx_frames": 1 if routed else 0})
    return rows, {"note": "Phase 1: sleepy κόμβος δεν γίνεται parent → μόνο το rank 1 έχει δρομολόγηση"}


def _t1_slots(cfg, R, W, rad, gw):
    """Ladder slot lengths D_r (rank r transmits upward during slot r)."""
    frames_per_node = lambda r: R - r + 1
    hop_ack = rad["t_ack_exch"] if cfg["scheme.t1_hop_ack"] == "per_frame" else 0.0
    per_frame = rad["t_frame"] + hop_ack
    N = R * W
    D = {}
    for r in range(1, R + 1):
        row_channel = W * frames_per_node(r) * per_frame
        if cfg["scheme.t1_hop_ack"] == "batch":
            row_channel += W * rad["t_ack_exch"]
        if r == 1:
            gw_time = max(0, N - cfg["bridge.ingress_queue"]) * gw["t_gw"]
            D[r] = max(row_channel, gw_time) + cfg["radio.jitter_s"]
        else:
            D[r] = row_channel + cfg["radio.jitter_s"]
    return D, per_frame


def window_beacon_air(cfg, cat, rad):
    """Air time of the RX_OPEN beacons one node sends while its window is open."""
    slot = cfg["scheme.t1_slot_s"]
    beacons = slot / cfg["scheme.rx_beacon_period_s"]
    return beacons * (rad["t_beacon_air"] + (an.DIFS_US + an.mean_backoff_slots(cfg["radio.cw"]) * an.SLOT_US) / 1e6)


def fw_node_timeline(cfg, cat, I, rad, s_out, sync_extra, wait_s):
    """One node's cycle on the firmware ladder: boot, a receive window of SLOT
    with the sensors warming up inside it, [catching a sleeping parent:
    sync_extra, None if the parent is the always-on bridge], waiting for its
    turn in the parent's window, then s_out data frames. Shared by the layered
    calculator and the geometric model."""
    slot = cfg["scheme.t1_slot_s"]
    clim = hw.CLIMATE_SENSORS[cfg["hw.climate_sensor"]]
    soil = hw.SOIL_SENSORS[cfg["hw.soil_sensor"]]
    settle = max(clim["settle_s"], soil["settle_s"])
    read = clim["read_s"] + cat["BATT_ADC_SAMPLES"] * cat["BATT_ADC_SAMPLE_DELAY_MS"] / 1000
    i_sens = clim["active_ma"] + soil["active_ma"]
    tl = Timeline()
    tl.add("boot", cfg["timing.t_boot_s"], I["cpu"])
    tl.add("sensor", min(settle, slot), I["rx"] + i_sens)          # warm-up inside the window
    tl.add("cpu", read, I["rx"] + i_sens)
    tl.add("rx_window", max(0.0, slot - settle - read), I["rx"])
    if sync_extra is not None:
        tl.add("sync_listen", sync_extra, I["rx"])
    tl.add("radio_other", max(0.0, wait_s), I["rx"])
    _add_tx(tl, s_out, rad, I)
    return tl


def _scheme_t1_firmware(cfg, cat, R, W, rad, gw, I, sync):
    """The ladder exactly as firmware/libraries/GreenhouseMesh/mesh_cart.h runs it:
    every node opens a fixed receive window of SLOT (sensors warm up inside it),
    then catches its parent's window and forwards own + relayed frames, custody
    = the parent's L2 ACK. Rows are assumed to be in phase (worst case for
    contention: every column's rank-r nodes use the same slot)."""
    slot, J = cfg["scheme.t1_slot_s"], cfg["radio.jitter_s"]
    per_frame = rad["t_frame"] + (rad["t_ack_exch"] if cfg["scheme.t1_hop_ack"] == "per_frame" else 0.0)
    clim = hw.CLIMATE_SENSORS[cfg["hw.climate_sensor"]]
    soil = hw.SOIL_SENSORS[cfg["hw.soil_sensor"]]
    settle = max(clim["settle_s"], soil["settle_s"])
    read = clim["read_s"] + cat["BATT_ADC_SAMPLES"] * cat["BATT_ADC_SAMPLE_DELAY_MS"] / 1000
    i_sens = clim["active_ma"] + soil["active_ma"]
    # How many columns (branches under different rank-1 nodes) share a slot:
    # in phase = all W (worst case); firmware reality = rank-1 nodes free-run on
    # their own clocks, so another column's window overlaps ours (ranks r−1..r+1
    # all hear us) with probability ≈ 3·2·slot/T.
    Tc = cfg["timing.T_s"] * cfg["timing.report_every"]
    k = W if cfg["net.phase_sync"] else 1 + (W - 1) * min(1.0, 6 * slot / Tc)
    need = {}
    # Rank 1 sends to the always-on bridge: its flush budget can exceed the slot
    # (firmware today: SLOT; `scheme.rank1_flush_s` models a longer budget).
    r1_budget = cfg.get("scheme.rank1_flush_s") or slot
    for r in range(1, R + 1):
        row = k * (R - r + 1) * per_frame + J
        if r == 1:                         # rank-1 flushes land on the one gateway
            row = max(row, k * R * gw["t_gw"]) * slot / r1_budget   # normalised to the slot
        need[r] = row
    g = sync["guard_mean"]
    early = max(0.0, g / 2 - slot)
    extra = max(0.0, early + sync["listen"] - g / 2)   # awake beyond our own window to catch the parent
    share = (k + 1) / (2 * k)
    # Channel occupancy of one hearing domain (ranks r−1, r, r+1 × W nodes) over a
    # cycle: every node's RX_OPEN beacons for the whole window + its data frames.
    per_node_air = window_beacon_air(cfg, cat, rad)
    busiest = max(range(1, R + 1), key=lambda r: sum(R - j + 1 for j in range(max(1, r - 1), min(R, r + 1) + 1)))
    frames = sum(R - j + 1 for j in range(max(1, busiest - 1), min(R, busiest + 1) + 1))
    util = W * (min(3, R) * per_node_air + frames * per_frame) / Tc
    rows = []
    for r in range(1, R + 1):
        s = R - r + 1
        horizon = r1_budget if r == 1 else slot
        wait = min(need[r] * horizon / slot, horizon) * share - s * per_frame
        tl = fw_node_timeline(cfg, cat, I, rad, s, extra if r >= 2 else None, wait)
        rows.append({"rank": r, "tl": tl, "routed": True, "latency_s": r * slot + gw["t_gw"],
                     "buffer_peak": R - r, "sync_hops": max(0, r - 1),
                     "rx_frames": R - r, "tx_frames": s})
    return rows, {"slots_s": {r: slot for r in range(1, R + 1)}, "ladder_span_s": R * slot,
                  "per_frame_s": per_frame, "slot_need_s": max(need.values()), "columns_in_slot": k,
                  "channel_util": util,
                  "slot_fill": max(need.values()) / slot, "sensor_settle_s": settle + read,
                  "note": "σκάλα όπως στο firmware: σταθερό παράθυρο, custody με L2 ACK"}


def _scheme_t1(cfg, cat, R, W, rad, gw, I, sync):
    if cfg.get("scheme.t1_slot_s"):
        return _scheme_t1_firmware(cfg, cat, R, W, rad, gw, I, sync)
    # A child wakes G/2 early by its own clock and listens *before* the parent's slot
    # starts, catching the parent's RX_OPEN beacon at the true start — so slots need no
    # guard, and a relay's pre-listen overlaps the receive window it is holding for
    # its own children (it is already awake).
    D, per_frame = _t1_slots(cfg, R, W, rad, gw)
    rows = []
    for r in range(1, R + 1):
        tl = Timeline()
        _sensor_phase(tl, cfg, cat, I, radio_needed=True)
        f_in, f_out = R - r, R - r + 1
        if r < R:                                          # receive slot = child's slot
            n_acks = f_in if cfg["scheme.t1_hop_ack"] == "per_frame" else 1
            ack_air = n_acks * rad["t_ack_air"]
            tl.add("rx_window", D[r + 1] - ack_air, I["rx"])
            tl.add("tx_air", ack_air, I["tx"])            # hop-ACKs sent to the child
        if r >= 2:
            extra = sync["listen"] if r == R else max(0.0, sync["listen"] - D[r + 1])
            tl.add("sync_listen", extra, I["rx"])          # catch the parent's RX_OPEN
        share = (W + 1) / (2 * W)                          # expected position in the row
        wait = D[r] * share - f_out * per_frame
        tl.add("radio_other", max(0.0, wait), I["rx"])
        _add_tx(tl, f_out, rad, I)
        if cfg["scheme.t1_hop_ack"] == "per_frame":
            tl.add("radio_other", f_out * rad["t_ack_exch"], I["rx"])
        latency = sum(D[j] for j in range(1, r + 1)) + gw["t_gw"]
        rows.append({"rank": r, "tl": tl, "routed": True, "latency_s": latency,
                     "buffer_peak": f_out, "sync_hops": max(0, r - 1),
                     "rx_frames": f_in, "tx_frames": f_out})
    span = sum(D.values())
    return rows, {"slots_s": {r: round(D[r], 4) for r in D}, "ladder_span_s": span,
                  "per_frame_s": per_frame,
                  "note": "custody transfer: το origin δεν παίρνει end-to-end επιβεβαίωση"}


def _scheme_t2(cfg, cat, R, W, rad, gw, I, sync, ceiling):
    N = R * W
    s = lambda r: R - r + 1
    # channel demand around the busiest 3-rank collision domain (uplink)
    up_domain = max(sum(W * s(i) for i in range(max(1, j - 1), min(R, j + 1) + 1))
                    for j in range(1, R + 1)) * rad["t_frame"]
    fill = R * (rad["t_frame"])
    up_time = max(N * gw["t_gw"], up_domain, fill)
    delivered = sum(W for r in range(1, R + 1) if r <= ceiling)
    mode = cfg["scheme.t2_ack"]
    m, mx = cfg["scheme.ttl_margin"], cfg["scheme.max_ttl"]

    def floods_by(k):
        """ACKs a rank-k node re-broadcasts: targets whose flood reaches it with ttl > 0
        (meshHandleAck, CART gate !sleepy || rxOpen — the firmware gate would block all)."""
        n = 0
        for rt in range(1, min(R, ceiling) + 1):
            t = an.ack_received_ttl(an.ack_ttl0(rt, m, mx, mx), k)
            n += W if (t is not None and t > 0) else 0
        return n

    if mode == "flood":
        down_domain = max(sum(W * floods_by(i) for i in range(max(1, j - 1), min(R, j + 1) + 1))
                          for j in range(1, R + 1)) * rad["t_ack_bcast"]
        n_ack_tx = an.flood_rebroadcasts(R, W, m, mx, m, mx, mx)
        down_tail = R * rad["t_ack_bcast"]
    elif mode == "unicast":
        down_domain = max(sum(W * s(i) for i in range(max(1, j - 1), min(R, j + 1) + 1))
                          for j in range(1, R + 1)) * rad["t_ack_exch"]
        n_ack_tx = sum(W * r for r in range(1, R + 1) if r <= ceiling)
        down_tail = R * (rad["t_ack_exch"] + cfg["radio.hop_proc_s"])
    else:  # aggregate: one bitmap ACK per link after the uplink completes
        agg = lambda r: an.airtime_us(cat["sizeof(MeshAck)"] + 2 * s(r)) / 1e6 + (an.DIFS_US + an.SIFS_US + an.ack_airtime_us()) / 1e6
        down_domain = 3 * W * agg(1)
        n_ack_tx = N
        down_tail = sum(agg(r) + cfg["radio.hop_proc_s"] for r in range(1, R + 1))
    down_time = max(down_domain, N * gw["t_uart_ack"] if mode != "aggregate" else gw["t_uart_ack"] * W) + down_tail
    spread = 2 * cfg["sync.z"] * clock.accumulated_offset_std(sync["err_std"], R)
    core = up_time + cfg["pi.process_s"] + down_time
    window = core + spread
    rows = []
    for r in range(1, R + 1):
        tl = Timeline()
        _sensor_phase(tl, cfg, cat, I, radio_needed=True)
        if r >= 2:                                         # rank 1's parent is the always-on bridge
            tl.add("sync_listen", sync["listen"], I["rx"])
        f_in = R - r
        tx_frames = s(r)                                   # own + forwarded (cut-through)
        _add_tx(tl, tx_frames, rad, I)
        acks_tx = {"flood": floods_by(r) if mode == "flood" else 0,
                   "unicast": f_in, "aggregate": 1 if r < R else 0}[mode]
        tl.add("tx_air", acks_tx * rad["t_ack_air"], I["tx"])
        rest = window - tx_frames * rad["t_frame"] - acks_tx * rad["t_ack_air"]
        tl.add("rx_window", max(0.0, rest), I["rx"])
        latency = up_time + gw["t_gw"]
        rows.append({"rank": r, "tl": tl, "routed": True, "latency_s": latency,
                     "buffer_peak": 1, "sync_hops": r - 1, "rx_frames": f_in, "tx_frames": tx_frames})
    return rows, {"window_s": window, "window_core_s": core, "window_spread_s": spread,
                  "up_time_s": up_time, "down_time_s": down_time, "ack_mode": mode,
                  "ack_transmissions": n_ack_tx, "up_domain_s": up_domain}


# ── main entry ──────────────────────────────────────────────────────────────
def compute(cfg, cat):
    R, W = cfg["net.ranks"], cfg["net.per_rank"]
    N = R * W
    T, k = cfg["timing.T_s"], cfg["timing.report_every"]
    Tc = T * k
    tech = cfg["scheme.technique"]
    I = _currents(cfg)
    rad = _radio(cfg, cat)
    gw = _gateway(cfg, cat, rad)
    max_ttl = cfg["scheme.max_ttl"]
    ceiling = an.depth_ceiling(cfg["scheme.ttl_margin"], max_ttl, cfg["scheme.ttl_margin"], max_ttl, max_ttl)
    sync = _sync(cfg, cat, Tc) if tech != "phase1" else None

    if tech == "phase1":
        rows, scheme = _scheme_phase1(cfg, cat, R, rad, gw, I)
    elif tech == "T1-ladder":
        rows, scheme = _scheme_t1(cfg, cat, R, W, rad, gw, I, sync)
    else:
        rows, scheme = _scheme_t2(cfg, cat, R, W, rad, gw, I, sync, ceiling)

    sample_tl = _sample_only_wake(cfg, cat, I)
    bat = hw.BATTERIES[cfg["hw.battery"]]
    mah = cfg["hw.battery_mah"] or bat["mah"]
    usable = mah * bat["dod"]
    self_dis = mah * bat["self_discharge_pct_month"] / 100 / 30.44
    panel = hw.SOLAR_PANELS[cfg["hw.solar"]]["w"]
    harvest = panel * hw.SUN_PSH[cfg["hw.sun"]]["psh"] * cfg["hw.solar_derate"] * cfg["hw.charger_eff"] / bat["v"] * 1000
    cycles_day = 86400 / Tc
    # Collisions at the most loaded parent (net.max_children contenders released together):
    # in-range siblings collide only in the CSMA first slot and the MAC retry resolves it;
    # hidden siblings overlap like ALOHA over the jitter J: P = 2t/J − (t/J)² per pair.
    c = cfg["net.max_children"]
    x = min(1.0, rad["t_air"] / cfg["radio.jitter_s"])
    p_pair = 2 * x - x * x
    p_hidden_attempt = 1 - (1 - cfg["net.hidden_frac"] * p_pair) ** max(0, c - 1)
    p_coll_fail = p_hidden_attempt ** cfg["radio.attempts"]
    p_csma_first_slot = an.csma_first_slot_collision(c, cfg["radio.cw"] + 1) if c >= 2 else 0.0

    per_rank = []
    for row in rows:
        tl, r = row["tl"], row["rank"]
        awake = tl.awake_s + (k - 1) * sample_tl.awake_s
        sleep_s = max(0.0, Tc - awake)
        mas = tl.mas_by_state()
        if k > 1:
            mas["sample_wake"] = (k - 1) * sum(sample_tl.mas_by_state().values())
        mas["sleep"] = sleep_s * I["sleep"]
        sweep_mah_day = 0.0
        if sync and row["sync_hops"] > 0:
            sweep_mah_day = sync["drops_per_year"] / 365 * Tc * I["rx"] / 3600
        mah_day = sum(mas.values()) * cycles_day / 3600 + sweep_mah_day
        delivered = row["routed"] and r <= ceiling and \
            (tech != "T1-ladder" or row["buffer_peak"] <= cfg["scheme.relay_buffer"])
        hops = r
        p_ontime = 0.0
        if delivered:
            miss = sync["miss"] if sync else 0.0
            p_ontime = ((1 - miss) ** row["sync_hops"]) * ((1 - rad["p_link_fail"]) ** hops) \
                * ((1 - p_coll_fail) ** hops)
        per_rank.append({
            "rank": r,
            "subtree": R - r + 1,
            "awake_s": round(awake, 4),
            "duty_pct": round(100 * awake / Tc, 4),
            "time_on_air_ms": round(1000 * sum(s for st, s, _ in tl.items if st == "tx_air"), 3),
            "mah_day": round(mah_day, 3),
            "avg_ua": round(mah_day / 24 * 1000, 2),
            "mas_by_state": {s_: round(v, 2) for s_, v in mas.items()},
            "sweep_mah_day": round(sweep_mah_day, 4),
            "autonomy_dark_days": round(usable / (mah_day + self_dis), 1),
            "solar_margin_x": round(harvest / (mah_day + self_dis), 2) if harvest else 0.0,
            "energy_neutral": harvest >= mah_day + self_dis,
            "latency_s": row["latency_s"] if delivered else math.inf,
            "delivered": delivered,
            "p_ontime": round(p_ontime, 6),
            "buffer_peak": row["buffer_peak"],
            "buffer_util": round(row["buffer_peak"] / cfg["scheme.relay_buffer"], 3),
            "rx_frames": row["rx_frames"], "tx_frames": row["tx_frames"],
        })

    worst = max(per_rank, key=lambda x: x["mah_day"])
    leaf = per_rank[-1]
    n_delivered = sum(W for x in per_rank if x["delivered"])
    lat = [x["latency_s"] for x in per_rank if x["delivered"]]
    checks = _checks(cfg, cat, R, W, N, Tc, tech, per_rank, scheme, sync, gw, ceiling, worst)
    limits = _limits(cfg, cat, rad, gw, Tc, I, usable, self_dis)
    return {
        "summary": {
            "technique": tech, "nodes": N, "ranks": R, "T_s": T, "comm_period_s": Tc,
            "worst_rank": worst["rank"], "worst_mah_day": worst["mah_day"],
            "worst_autonomy_dark_days": worst["autonomy_dark_days"],
            "leaf_mah_day": leaf["mah_day"], "leaf_autonomy_dark_days": leaf["autonomy_dark_days"],
            "network_lifetime_dark_days": worst["autonomy_dark_days"],
            "all_energy_neutral": all(x["energy_neutral"] for x in per_rank),
            "harvest_mah_day": round(harvest, 1),
            "delivered_nodes": n_delivered, "undelivered_nodes": N - n_delivered,
            "pdr_ontime_mean": round(sum(x["p_ontime"] for x in per_rank) / R, 6),
            "latency_max_s": round(max(lat), 3) if lat else math.inf,
            "max_awake_s": max(x["awake_s"] for x in per_rank),
            "checks_failed": [c["name"] for c in checks if not c["ok"]],
        },
        "per_rank": per_rank,
        "scheme": _clean(scheme),
        "radio": {k_: _r(v) for k_, v in rad.items()},
        "gateway": {k_: _r(v) for k_, v in gw.items()},
        "sync": {k_: _r(v) for k_, v in (sync or {}).items()},
        "currents_ma": {k_: _r(v) for k_, v in I.items()},
        "battery": {"mah": mah, "usable_mah": usable, "self_discharge_mah_day": round(self_dis, 3),
                    "harvest_mah_day": round(harvest, 1)},
        "ttl_depth_ceiling": ceiling,
        "collisions": {"contenders": c, "p_csma_first_slot": round(p_csma_first_slot, 5),
                       "p_hidden_per_attempt": round(p_hidden_attempt, 6),
                       "p_fail_after_attempts": p_coll_fail,
                       "note": "εκτίμηση κλειστού τύπου· ο DES δίνει την ακριβή τιμή"},
        "checks": checks,
        "limits": limits,
    }


def _checks(cfg, cat, R, W, N, Tc, tech, per_rank, scheme, sync, gw, ceiling, worst):
    C = []

    def chk(name, ok, detail):
        C.append({"name": name, "ok": bool(ok), "detail": detail})

    chk("ttl_depth", R <= ceiling,
        f"βάθος {R} vs ταβάνι TTL {ceiling} (MAX_TTL={cfg['scheme.max_ttl']})")
    longest = max(per_rank, key=lambda x: x["awake_s"])
    chk("awake_cap", longest["awake_s"] <= cfg["timing.awake_cap_s"],
        f"μέγιστος ξύπνιος {longest['awake_s']:.2f} s (rank {longest['rank']}) vs όριο "
        f"{cfg['timing.awake_cap_s']} s — το firmware θα κόψει τον κόμβο")
    if tech == "phase1":
        chk("multihop_possible", R == 1, "Phase 1: sleepy κόμβοι δεν γίνονται parent → μόνο rank 1")
    if tech == "T1-ladder":
        peak = max(x["buffer_peak"] for x in per_rank)
        chk("relay_buffer", peak <= cfg["scheme.relay_buffer"],
            f"μέγιστο φορτίο {peak} frames vs buffer {cfg['scheme.relay_buffer']}")
        chk("rtc_capacity", cfg["scheme.relay_buffer"] <= cat["RELAY_BUFFER_MAX_UPPER_BOUND"],
            f"buffer {cfg['scheme.relay_buffer']} vs RTC άνω όριο {cat['RELAY_BUFFER_MAX_UPPER_BOUND']}")
        chk("ladder_span", scheme["ladder_span_s"] <= Tc,
            f"σκάλα {scheme['ladder_span_s']:.1f} s vs κύκλος {Tc} s")
        chk("tx_burst", max(x["tx_frames"] for x in per_rank) <= cat["CONFIG_ESP_WIFI_DYNAMIC_TX_BUFFER_NUM"]
            or cfg["scheme.t1_hop_ack"] in ("per_frame", "l2"),
            "ριπή χωρίς αναμονή callback > 32 dyn TX buffers → ESP_ERR_ESPNOW_NO_MEM")
        if "slot_need_s" in scheme:
            slot = cfg["scheme.t1_slot_s"]
            chk("slot_capacity", scheme["slot_need_s"] <= slot,
                f"η πιο φορτωμένη σειρά θέλει {scheme['slot_need_s']:.2f} s μέσα σε παράθυρο {slot} s")
            chk("channel_util", scheme["channel_util"] <= cfg["radio.max_util"],
                f"κανάλι ανά περιοχή ακρόασης {100 * scheme['channel_util']:.1f} % (beacons RX_OPEN + δεδομένα) "
                f"vs όριο {100 * cfg['radio.max_util']:.0f} %")
            chk("slot_covers_sensor", scheme["sensor_settle_s"] + 0.2 <= slot,
                f"warm-up + ανάγνωση {scheme['sensor_settle_s']:.2f} s (+0,2) μέσα σε παράθυρο {slot} s")
    if tech == "T2-window":
        chk("app_ack_wait", scheme["window_core_s"] <= cfg["timing.app_ack_wait_s"],
            f"το ACK φτάνει σε ~{scheme['window_core_s']:.2f} s vs αναμονή firmware {cfg['timing.app_ack_wait_s']} s "
            "(αλλιώς ξαναστέλνει → διπλότυπα)")
        chk("window_fits", scheme["window_s"] <= cfg["timing.awake_cap_s"],
            f"παράθυρο {scheme['window_s']:.2f} s vs όριο {cfg['timing.awake_cap_s']} s")
    relay_frames = 0 if tech == "phase1" else R - 1     # rank-1 relay, all within one window/slot
    chk("dedup_ring", relay_frames <= cat["MESH_DEDUP_CACHE_SIZE"],
        f"~{relay_frames} διαφορετικά frames μέσα σε <30 s στον πιο φορτωμένο relay vs ring "
        f"{cat['MESH_DEDUP_CACHE_SIZE']} (η γέφυρα βλέπει {N}· το Pi κάνει δικό του dedup)")
    chk("neighbor_slots", 3 * W - 1 <= cat["MESH_NEIGHBOR_SLOTS"],
        f"~{3 * W - 1} γείτονες vs {cat['MESH_NEIGHBOR_SLOTS']} θέσεις (thrash → άσκοπα trickle resets)")
    lat = [x["latency_s"] for x in per_rank if x["delivered"]]
    chk("latency_req", bool(lat) and max(lat) <= cfg["req.latency_s"],
        f"μέγιστη καθυστέρηση {max(lat) if lat else math.inf:.1f} s vs απαίτηση {cfg['req.latency_s']} s")
    pdr = min((x["p_ontime"] for x in per_rank), default=0)
    chk("pdr_req", pdr >= cfg["req.pdr"], f"χειρότερο on-time PDR {pdr:.4f} vs απαίτηση {cfg['req.pdr']}")
    chk("lifetime_req", worst["autonomy_dark_days"] >= cfg["req.lifetime_days"],
        f"χειρότερη αυτονομία χωρίς ήλιο {worst['autonomy_dark_days']} d vs στόχος {cfg['req.lifetime_days']} d")
    chk("energy_neutral", all(x["energy_neutral"] for x in per_rank),
        "όλοι οι κόμβοι ενεργειακά αυτάρκεις με το πάνελ/ηλιοφάνεια της ρύθμισης")
    chk("gateway_rate", N * gw["t_gw"] <= Tc,
        f"{N} frames × {gw['t_gw'] * 1000:.1f} ms = {N * gw['t_gw']:.1f} s ανά κύκλο στη γέφυρα/Pi")
    chk("usb_echo", not cfg["bridge.usb_echo"],
        "USB echo: με συνδεδεμένο host που δεν διαβάζει, κάθε γραμμή μπλοκάρει έως ~2 s (HWCDC.cpp:582)")
    return C


def _limits(cfg, cat, rad, gw, Tc, I, usable, self_dis):
    """Theoretical bounds: the best any node could do with ideal parts."""
    ideal = dict(cfg)
    ideal.update({"hw.board": "bare_chip_ideal", "hw.divider": "none", "hw.rtc_clock": "rc136k",
                  "hw.climate_sensor": "sht40", "hw.soil_sensor": "none",
                  "hw.warmup_mode": "light_sleep", "timing.t_boot_s": 0.14, "energy.model": "per_state"})
    Ii = _currents(ideal)
    tl = Timeline()
    _sensor_phase(tl, ideal, cat, Ii, radio_needed=True)
    tl.add("sync_listen", cfg["sync.g_min_s"] / 2, Ii["rx"])
    _add_tx(tl, 1, rad, Ii)
    mas = sum(tl.mas_by_state().values()) + (Tc - tl.awake_s) * Ii["sleep"]
    ideal_mah_day = mas * 86400 / Tc / 3600
    comm_mas = rad["t_air"] * Ii["tx"] + (rad["t_exch"] - rad["t_air"]) * Ii["rx"]
    return {
        "ideal_leaf_mah_day": round(ideal_mah_day, 4),
        "ideal_leaf_autonomy_dark_days": round(usable / (ideal_mah_day + self_dis), 1),
        "ideal_leaf_awake_s": round(tl.awake_s, 4),
        "self_discharge_floor_days": round(usable / self_dis, 1) if self_dis else math.inf,
        "one_frame_energy_mas": round(comm_mas, 4),
        "gateway_max_frames_s": round(gw["max_frames_s"], 1),
        "max_nodes_per_window": int((cfg["timing.awake_cap_s"] - 1.0) / gw["t_gw"]),
    }


def _r(v):
    return round(v, 6) if isinstance(v, float) else v


def _clean(d):
    return {k: (_clean(v) if isinstance(v, dict) else _r(v)) for k, v in d.items()}
