"""Geometric model: where the sensors actually are, and what the radio can do there.

The layered model (calculator/DES) assumes a balanced tree whose every node
hears 3·W neighbours. This module drops that assumption for a real greenhouse:

  layout    rectangle length × width, plants at `geo.plant_density_m2`, one
            sensor every `geo.plants_per_sensor` plants (jittered square grid),
            `geo.bridges` bridges along a wall or the centre line.
  channel   mean path loss = two-ray ground reflection (antenna heights) +
            Weissberger foliage loss over `geo.foliage_frac` of the path,
            × `geo.wet_factor`; per-link log-normal shadowing (σ, fixed per link,
            symmetric); per-frame Rician fading (K) averaged into the PER.
            RSSI = P_tx + 2·G_ant − PL + X_σ; PER from the same DBPSK model as
            everything else (analytic.frame_error_rate).
  routing   rank = hops to the nearest bridge over links with fading-averaged
            PER ≤ `geo.max_link_per`. A node chooses among at most
            `geo.parent_candidates_max` rank−1 candidates it can hear (its
            neighbour table): `rssi` = best RSSI (firmware today) or `balanced` =
            least-loaded (a firmware change: advertise subtree load in beacons).
  per node  subtree size → frames sent per cycle, relay buffer, the parent's
            window load, channel occupancy of everything it hears on its channel,
            and energy through calculator.fw_node_timeline (same timeline as the
            layered model).

Pure stdlib (runs in Pyodide). Hearing/utilisation use a cell aggregate with
P(hear | d) = Φ((RSSI_mean(d) − threshold)/σ), so 25 000-node layouts stay fast.
See docs/simulator/MODEL.md §18.
"""
import math
import random
from collections import defaultdict

from . import analytic as an
from . import calculator
from . import clock

C_LIGHT = 299_792_458.0


# ── channel ────────────────────────────────────────────────────────────────────
def fspl_db(d, f_hz):
    return 20 * math.log10(4 * math.pi * max(d, 0.1) * f_hz / C_LIGHT)


def two_ray_db(d, f_hz, h_t, h_r):
    """Free space up to the breakpoint d_bp = 4·h_t·h_r/λ, then 40·log10(d) (Rappaport §4.6)."""
    lam = C_LIGHT / f_hz
    d_bp = 4 * h_t * h_r / lam
    if d <= d_bp:
        return fspl_db(d, f_hz)
    return fspl_db(d_bp, f_hz) + 40 * math.log10(d / d_bp)


def weissberger_db(d_foliage, f_ghz):
    """Modified exponential decay model (Weissberger 1982), foliage depth in metres."""
    if d_foliage <= 0:
        return 0.0
    if d_foliage <= 14:
        return 0.45 * f_ghz ** 0.284 * d_foliage
    return 1.33 * f_ghz ** 0.284 * min(d_foliage, 400) ** 0.588


class Channel:
    def __init__(self, cfg, cat):
        self.cfg, self.cat = cfg, cat
        self.f_hz = 2.412e9 + 5e6 * (cat["MESH_FIXED_CHANNEL"] - 1)
        self.p_tx = cat["CONFIG_ESP_PHY_MAX_WIFI_TX_POWER"]
        self.sens = cat["RX_SENSITIVITY_1M_DBM"]
        self.sigma = cfg["geo.shadow_sigma_db"]
        self.pkt = cat["MESH_PACKET_LEN"]
        k_lin = 10 ** (cfg["geo.rician_k_db"] / 10) if cfg["geo.rician_k_db"] is not None else 0.0
        rng = random.Random(12345)
        los, nlos = math.sqrt(k_lin / (k_lin + 1)), math.sqrt(1 / (k_lin + 1))
        self.fade_db = []                       # per-frame power gain samples, E[g] = 1
        for _ in range(400):
            x, y = rng.gauss(0, 1) / math.sqrt(2), rng.gauss(0, 1) / math.sqrt(2)
            g = (los + nlos * x) ** 2 + (nlos * y) ** 2
            self.fade_db.append(10 * math.log10(max(g, 1e-9)))
        self._per = {}

    def mean_rssi(self, d, h_a, h_b):
        c = self.cfg
        pl = two_ray_db(d, self.f_hz, h_a, h_b)
        pl += c["geo.wet_factor"] * weissberger_db(d * c["geo.foliage_frac"], self.f_hz / 1e9)
        return self.p_tx + 2 * c["geo.ant_gain_dbi"] - pl

    def per_avg(self, rssi):
        """Fading-averaged PER of one data frame at a given mean RSSI (0.5 dB grid)."""
        key = round(rssi * 2) / 2
        if key not in self._per:
            self._per[key] = sum(an.frame_error_rate(key + g, self.pkt, self.sens)
                                 for g in self.fade_db) / len(self.fade_db)
        return self._per[key]

    def range_m(self, per_max, h_a, h_b, extra_db=0.0):
        """Longest distance whose (mean + extra) RSSI still gives PER ≤ per_max."""
        d = 1.0
        while d < 2000 and self.per_avg(self.mean_rssi(d, h_a, h_b) + extra_db) <= per_max:
            d += 0.5
        return d - 0.5


def _gauss(a, b, seed):
    """Deterministic N(0,1) per unordered pair (splitmix64 → Box–Muller)."""
    if a > b:
        a, b = b, a
    x = (a * 0x9E3779B97F4A7C15 + b * 0xBF58476D1CE4E5B9 + seed * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF

    def mix(z):
        z = (z + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
        return z ^ (z >> 31)
    u1 = (mix(x) >> 11) / 9007199254740992.0 or 1e-12
    u2 = (mix(x ^ 0x5555) >> 11) / 9007199254740992.0
    return math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)


# ── layout ─────────────────────────────────────────────────────────────────────
def layout(cfg):
    """→ (sensor positions [(x, y)], bridge positions [(x, y)])."""
    L, Wd = cfg["geo.length_m"], cfg["geo.width_m"]
    spacing = math.sqrt(cfg["geo.plants_per_sensor"] / cfg["geo.plant_density_m2"])
    rng = random.Random(cfg["geo.seed"])
    nx, ny = max(1, int(L / spacing)), max(1, int(Wd / spacing))
    pts = []
    for i in range(nx):
        for j in range(ny):
            x = (i + 0.5) * L / nx + rng.uniform(-0.25, 0.25) * spacing
            y = (j + 0.5) * Wd / ny + rng.uniform(-0.25, 0.25) * spacing
            pts.append((min(max(x, 0.0), L), min(max(y, 0.0), Wd)))
    nb = cfg["geo.bridges"]
    yb = 0.0 if cfg["geo.bridge_layout"] == "wall" else Wd / 2
    bridges = [((k + 0.5) * L / nb, yb) for k in range(nb)]
    return pts, bridges


# ── routing ────────────────────────────────────────────────────────────────────
def build_tree(cfg, cat, ch, pts, bridges):
    """Rank by hop count to the nearest bridge; parent by the chosen policy.
    Node ids 0..N−1 are sensors; −1−b is bridge b."""
    h, hb = cfg["geo.antenna_h_m"], cfg["geo.bridge_h_m"]
    per_max, seed, sigma = cfg["geo.max_link_per"], cfg["geo.seed"], ch.sigma
    cmax = cfg["geo.parent_candidates_max"]
    r_nn = ch.range_m(per_max, h, h, 3 * sigma)        # beyond this no link, even with +3σ shadowing
    r_nb = ch.range_m(per_max, h, hb, 3 * sigma)
    cell = max(r_nn, r_nb) / 2 or 1.0
    grid = defaultdict(list)
    for i, (x, y) in enumerate(pts):
        grid[(int(x // cell), int(y // cell))].append(i)
    rng = random.Random(seed)

    def pos(n):
        return pts[n] if n >= 0 else bridges[-1 - n]

    def link(a, b):
        (xa, ya), (xb, yb) = pos(a), pos(b)
        d = math.hypot(xa - xb, ya - yb)
        ha, hb_ = (hb if a < 0 else h), (hb if b < 0 else h)
        rssi = ch.mean_rssi(d, ha, hb_) + sigma * _gauss(a + 10 ** 7, b + 10 ** 7, seed)
        return rssi, ch.per_avg(rssi)

    rank = [None] * len(pts)
    cands = [[] for _ in pts]                          # (parent, rssi) with usable PER
    frontier = [-1 - b for b in range(len(bridges))]
    r = 0
    while frontier:
        r += 1
        fgrid = defaultdict(list)
        for f in frontier:
            x, y = pos(f)
            fgrid[(int(x // cell), int(y // cell))].append(f)
        near = set()
        for (cx, cy) in fgrid:
            for dx in (-2, -1, 0, 1, 2):
                for dy in (-2, -1, 0, 1, 2):
                    near.update(n for n in grid.get((cx + dx, cy + dy), ()) if rank[n] is None)
        new = []
        for n in near:
            x, y = pts[n]
            cx, cy = int(x // cell), int(y // cell)
            pool = [f for dx in (-2, -1, 0, 1, 2) for dy in (-2, -1, 0, 1, 2)
                    for f in fgrid.get((cx + dx, cy + dy), ())]
            if len(pool) > 4 * cmax:
                pool = rng.sample(pool, 4 * cmax)
            heard = []
            for f in pool:
                rssi, per = link(n, f)
                if per <= per_max:
                    heard.append((f, rssi))
            if heard:
                # the firmware neighbour table is an LRU of whoever it heard, not the loudest
                if len(heard) > cmax:
                    heard = rng.sample(heard, cmax)
                heard.sort(key=lambda t: -t[1])
                cands[n] = heard
                new.append(n)
        for n in new:
            rank[n] = r
        frontier = new
    # parents, deepest rank first so subtree sizes are known when balancing
    parent = [None] * len(pts)
    subtree = [1 if rank[n] is not None else 0 for n in range(len(pts))]
    load = defaultdict(int)
    order = sorted((n for n in range(len(pts)) if rank[n] is not None), key=lambda n: -rank[n])
    by_rank = defaultdict(list)
    for n in order:
        by_rank[rank[n]].append(n)
    for rr in sorted(by_rank, reverse=True):
        nodes = sorted(by_rank[rr], key=lambda n: -subtree[n])
        for n in nodes:
            if cfg["geo.parent_policy"] == "balanced":
                p = min(cands[n], key=lambda t: (load[t[0]], -t[1]))[0]
            else:
                p = cands[n][0][0]
            parent[n] = p
            load[p] += subtree[n]
            if p >= 0:
                subtree[p] += subtree[n]
    return {"rank": rank, "parent": parent, "subtree": subtree, "cell": cell,
            "range_nn_m": ch.range_m(per_max, h, h), "range_nb_m": ch.range_m(per_max, h, hb)}


def _bridge_of(n, parent):
    while n >= 0:
        n = parent[n]
    return -1 - n


def _phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


# ── the study ──────────────────────────────────────────────────────────────────
def evaluate(cfg, cat):
    """Everything for one layout: coverage, depth, relay load, channel, energy, checks."""
    ch = Channel(cfg, cat)
    pts, bridges = layout(cfg)
    tree = build_tree(cfg, cat, ch, pts, bridges)
    rank, parent, subtree = tree["rank"], tree["parent"], tree["subtree"]
    N = len(pts)
    routed = [n for n in range(N) if rank[n] is not None]
    nch = max(1, cfg["geo.channels"])
    zone = {n: _bridge_of(n, parent) for n in routed}
    chan = {n: zone[n] % nch for n in routed}

    Tc = cfg["timing.T_s"] * cfg["timing.report_every"]
    slot, J = cfg["scheme.t1_slot_s"], cfg["radio.jitter_s"]
    I = calculator._currents(cfg)
    rad = calculator._radio(cfg, cat)
    gw = calculator._gateway(cfg, cat, rad)
    sync = calculator._sync(cfg, cat, Tc)
    per_frame = rad["t_frame"]
    beacon_air = calculator.window_beacon_air(cfg, cat, rad)
    t_exch = rad["t_exch"]

    # channel occupancy per node per cycle: its window beacons + the frames it sends
    air = {n: beacon_air + subtree[n] * t_exch for n in routed}
    # hearing: cell aggregate, P(hear | d) from the mean RSSI and shadowing σ
    cs = cfg["geo.cs_dbm"] if cfg["geo.cs_dbm"] is not None else ch.sens
    h = cfg["geo.antenna_h_m"]
    hear_r = 1.0                                         # beyond it even +3σ shadowing stays below cs
    while hear_r < 2000 and ch.mean_rssi(hear_r, h, h) + 3 * ch.sigma >= cs:
        hear_r += 1.0
    cell = max(2.0, min(tree["cell"], 10.0))
    agg = defaultdict(lambda: [0.0, 0, 0.0, 0.0])       # air, count, Σx, Σy per (cell, channel)
    for n in routed:
        x, y = pts[n]
        a = agg[(int(x // cell), int(y // cell), chan[n])]
        a[0] += air[n]; a[1] += 1; a[2] += x; a[3] += y
    reach = int(hear_r // cell) + 2
    p_cache = {}

    def p_hear(d):
        k = round(d)
        if k not in p_cache:
            p_cache[k] = _phi((ch.mean_rssi(max(k, 0.5), h, h) - cs) / ch.sigma)
        return p_cache[k]

    def local(n):
        """(expected neighbours heard, channel utilisation around n)."""
        x, y = pts[n]
        cx, cy, c = int(x // cell), int(y // cell), chan[n]
        nb = util = 0.0
        for dx in range(-reach, reach + 1):
            for dy in range(-reach, reach + 1):
                a = agg.get((cx + dx, cy + dy, c))
                if not a:
                    continue
                d = math.hypot(a[2] / a[1] - x, a[3] / a[1] - y)
                p = p_hear(d)
                nb += p * a[1]
                util += p * a[0]
        return nb - 1, util / Tc

    # evaluate the heavy spots exactly, the rest by sampling
    rng = random.Random(cfg["geo.seed"] + 1)
    sample = set(rng.sample(routed, min(len(routed), cfg["geo.sample_nodes"])))
    sample.update(n for n in routed if rank[n] <= 2)
    loc = {n: local(n) for n in sample}
    util_default = sum(u for _, u in loc.values()) / len(loc) if loc else 0.0

    # per-node energy and window load
    g = sync["guard_mean"]
    extra = max(0.0, max(0.0, g / 2 - slot) + sync["listen"] - g / 2)
    in_frames = defaultdict(int)
    for n in routed:
        if parent[n] >= 0:
            in_frames[parent[n]] += subtree[n]
    r1_by_bridge, r1_frames = defaultdict(int), defaultdict(int)
    for n in routed:
        if rank[n] == 1:
            r1_by_bridge[zone[n]] += 1
            r1_frames[zone[n]] += subtree[n]
    r1_budget = cfg.get("scheme.rank1_flush_s") or slot
    rows, need_max, need_at = [], 0.0, None
    for n in routed:
        u = loc.get(n, (None, util_default))[1]
        pf = per_frame / max(0.05, 1 - min(u, 0.95))     # CSMA: busy medium stretches each exchange
        s = subtree[n]
        if parent[n] < 0:                                # rank 1 → the always-on bridge
            # Rank-1 nodes free-run, so ~n_r1·2·budget/Tc others flush to the same
            # bridge while we do; the bridge/Pi serves their frames and ours at t_gw each.
            b = zone[n]
            others = (r1_by_bridge[b] - 1) * min(1.0, 2 * r1_budget / Tc)
            s_mean = r1_frames[b] / r1_by_bridge[b]
            gw_busy = (others * s_mean + s) * gw["t_gw"]
            need = max(s * pf, gw_busy) + J
            wait = max(0.0, gw_busy - s * pf) / 2
            budget = r1_budget
            tl = calculator.fw_node_timeline(cfg, cat, I, rad, s, None, wait)
        else:
            p = parent[n]
            wait = 0.5 * (in_frames[p] * pf + J) - s * pf
            tl = calculator.fw_node_timeline(cfg, cat, I, rad, s, extra, wait)
            need, budget = 0.0, slot
        if in_frames[n]:
            need = max(need, in_frames[n] * pf + J)
        fill = need / budget if budget else 0.0
        if fill > need_max:
            need_max, need_at = fill, n
        mas = sum(tl.mas_by_state().values()) + max(0.0, Tc - tl.awake_s) * I["sleep"]
        mah_day = mas * 86400 / Tc / 3600 + (sync["drops_per_year"] / 365 * Tc * I["rx"] / 3600
                                             if parent[n] >= 0 else 0.0)
        rows.append((n, mah_day, tl.awake_s))

    worst = max(rows, key=lambda t: t[1]) if rows else (None, 0.0, 0.0)
    ceiling = an.depth_ceiling(cfg["scheme.ttl_margin"], cfg["scheme.max_ttl"], cfg["scheme.ttl_margin"],
                               cfg["scheme.max_ttl"], cfg["scheme.max_ttl"])
    max_rank = max((rank[n] for n in routed), default=0)
    max_in = max(in_frames.values(), default=0)
    per_bridge = defaultdict(int)
    for n in routed:
        per_bridge[zone[n]] += 1
    nb_max = max((v[0] for v in loc.values()), default=0.0)
    util_max = max((v[1] for v in loc.values()), default=0.0)
    from . import hardware as hwlib
    bat = hwlib.BATTERIES[cfg["hw.battery"]]
    mah = cfg["hw.battery_mah"] or bat["mah"]
    self_dis = mah * bat["self_discharge_pct_month"] / 100 / 30.44
    harvest = (hwlib.SOLAR_PANELS[cfg["hw.solar"]]["w"] * hwlib.SUN_PSH[cfg["hw.sun"]]["psh"]
               * cfg["hw.solar_derate"] * cfg["hw.charger_eff"] / bat["v"] * 1000)

    checks = {
        "coverage": N - len(routed) == 0,
        "ttl_depth": max_rank <= ceiling,
        "relay_buffer": max_in <= cfg["scheme.relay_buffer"],
        "slot_capacity": need_max <= cfg["geo.fill_max"],
        "channel_util": util_max <= cfg["radio.max_util"],
        "gateway_rate": max(per_bridge.values(), default=0) * gw["t_gw"] <= Tc,
        "awake_cap": worst[2] <= cfg["timing.awake_cap_s"],
        "energy_neutral": harvest >= worst[1] + self_dis,
    }
    soft = {"neighbor_slots": nb_max <= cat["MESH_NEIGHBOR_SLOTS"],
            "dedup_ring": max_in <= cat["MESH_DEDUP_CACHE_SIZE"]}
    energies = sorted(t[1] for t in rows)
    return {
        "sensors": N, "routed": len(routed), "unrouted": N - len(routed),
        "area_m2": cfg["geo.length_m"] * cfg["geo.width_m"],
        "sensors_per_m2": N / (cfg["geo.length_m"] * cfg["geo.width_m"]),
        "bridges": len(bridges), "channels": nch,
        "range_node_node_m": tree["range_nn_m"], "range_node_bridge_m": tree["range_nb_m"],
        "hear_range_m": hear_r,
        "max_rank": max_rank, "ttl_ceiling": ceiling,
        "rank1_nodes": dict(r1_by_bridge),
        "nodes_per_bridge_max": max(per_bridge.values(), default=0),
        "max_relay_in_frames": max_in, "relay_buffer": cfg["scheme.relay_buffer"],
        "slot_fill_max": round(need_max, 3),
        "neighbors_heard_max": round(nb_max, 1),
        "neighbors_heard_mean": round(sum(v[0] for v in loc.values()) / max(1, len(loc)), 1),
        "channel_util_max": round(util_max, 4), "channel_util_mean": round(util_default, 4),
        "worst_mah_day": round(worst[1], 3), "worst_rank": rank[worst[0]] if worst[0] is not None else None,
        "median_mah_day": round(energies[len(energies) // 2], 3) if energies else None,
        "autonomy_dark_days": round((mah * bat["dod"]) / (worst[1] + self_dis), 1) if rows else None,
        "latency_max_s": round(max_rank * slot + gw["t_gw"], 1),
        "checks_failed": [k for k, ok in checks.items() if not ok],
        "soft_failed": [k for k, ok in soft.items() if not ok],
    }
