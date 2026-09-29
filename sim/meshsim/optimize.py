"""Capacity / efficiency study: for each cycle length and network depth, the
largest number of sensors per rank that passes every hard constraint and the
reliability target, and among the settings that reach it the one that uses
the least energy. Firmware-ladder model (T1, fixed window, L2 custody).

    python run_optimize.py                # from sim/: both profiles, T = 900 and 1800 s
"""
import itertools

from . import calculator, config

HARD = {"ttl_depth", "awake_cap", "relay_buffer", "rtc_capacity", "ladder_span", "slot_capacity",
        "slot_covers_sensor", "channel_util", "gateway_rate", "energy_neutral", "latency_req", "tx_burst"}
# Soft (reported, not limiting): dedup_ring / neighbor_slots only cause duplicate
# frames the Pi de-duplicates or extra beacons; lifetime_req is the dark-battery goal.

PROFILES = {
    "today": {
        "_doc": "Σημερινό hardware: DHT22, γέφυρα 115200 hex, Pi 20 ms/frame· αλλάζουν μόνο ρυθμίσεις firmware",
        "fixed": {"hw.climate_sensor": "dht22"},
        "grid": {"scheme.t1_slot_s": [2.3, 2.5], "radio.jitter_s": [0.1, 0.3, 0.5],
                 "sync.g_max_rule": ["cart_v2", "bias_wander"], "sync.margin_k": [1.5, 2.0, 3.0]},
    },
    "best": {
        "_doc": "Βέλτιστο: SHT40, switched divider, γέφυρα 921600 binary, Pi 5 ms/frame, relay buffer 121",
        "fixed": {"hw.climate_sensor": "sht40", "hw.divider": "switched", "bridge.baud": 921600,
                  "bridge.framing": "binary", "pi.process_s": 0.005, "scheme.relay_buffer": 121},
        "grid": {"scheme.t1_slot_s": [0.4, 0.6, 0.8, 1.0, 1.5], "radio.jitter_s": [0.1, 0.2, 0.3],
                 "sync.g_max_rule": ["bias_wander", "cart_v2"], "sync.margin_k": [1.5, 2.0, 3.0]},
    },
}
DEPTHS = [1, 2, 3, 4, 5, 8, 10, 15, 20, 30, 50]


def _base(T, R, phase_sync, pdr_target):
    return {"scheme.technique": "T1-ladder", "scheme.t1_hop_ack": "l2", "scheme.max_ttl": 64,
            "net.ranks": R, "timing.T_s": T, "req.latency_s": T, "req.pdr": pdr_target,
            "net.phase_sync": phase_sync, "hw.sun": "athens_winter", "hw.solar": "6v_2w"}


def feasible(res, pdr_target):
    s = res["summary"]
    failed = set(s["checks_failed"]) & HARD
    reliable = s["pdr_ontime_mean"] >= pdr_target and s["undelivered_nodes"] == 0
    return not failed and reliable


def evaluate(cat, over):
    cfg = config.resolve(cat, None, over)
    return cfg, calculator.compute(cfg, cat)


def max_w(cat, over, pdr_target, w_hi=2000):
    """Largest W with a feasible config (feasibility falls monotonically with W)."""
    ok = lambda w: feasible(evaluate(cat, dict(over, **{"net.per_rank": w}))[1], pdr_target)
    if not ok(1):
        return 0
    lo, hi = 1, w_hi
    if ok(hi):
        return hi
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if ok(mid) else (lo, mid)
    return lo


def best_for(cat, profile, T, R, phase_sync, pdr_target):
    prof = PROFILES[profile]
    keys = list(prof["grid"])
    best = None
    for combo in itertools.product(*prof["grid"].values()):
        over = dict(_base(T, R, phase_sync, pdr_target), **prof["fixed"], **dict(zip(keys, combo)))
        if over["radio.jitter_s"] >= over["scheme.t1_slot_s"]:
            continue
        w = max_w(cat, over, pdr_target)
        if w == 0:
            continue
        cfg, res = evaluate(cat, dict(over, **{"net.per_rank": w}))
        key = (w, -res["summary"]["worst_mah_day"])
        if best is None or key > best[0]:
            best = (key, w, cfg, res, dict(zip(keys, combo)))
    return best


def study(cat, Ts=(900, 1800), depths=DEPTHS, pdr_target=0.90, profiles=("today", "best"),
          phases=(False, True), progress=print):
    rows = []
    for profile, phase_sync, T, R in itertools.product(profiles, phases, Ts, depths):
        b = best_for(cat, profile, T, R, phase_sync, pdr_target)
        if b is None:
            rows.append({"profile": profile, "phase_sync": phase_sync, "T_s": T, "ranks": R, "max_per_rank": 0})
            progress(f"  {profile:5} {'sync' if phase_sync else 'rand'} T={T} R={R}: none")
            continue
        _, w, cfg, res, knobs = b
        s, per = res["summary"], res["per_rank"]
        row = {
            "profile": profile, "phase_sync": phase_sync, "T_s": T, "ranks": R, "max_per_rank": w,
            "nodes": R * w, **knobs,
            "worst_mah_day": s["worst_mah_day"], "leaf_mah_day": s["leaf_mah_day"],
            "worst_autonomy_dark_days": s["worst_autonomy_dark_days"],
            "solar_margin_min_x": min(x["solar_margin_x"] for x in per),
            "pdr_ontime_mean": s["pdr_ontime_mean"], "pdr_ontime_min": min(x["p_ontime"] for x in per),
            "latency_max_s": s["latency_max_s"], "max_awake_s": s["max_awake_s"],
            "slot_fill": res["scheme"].get("slot_fill"), "binding": _binding(cat, cfg, w, pdr_target),
            "sync_miss": res["sync"].get("miss"), "soft_failed": sorted(set(s["checks_failed"]) - HARD),
        }
        rows.append(row)
        progress(f"  {profile:5} {'sync' if phase_sync else 'rand'} T={T} R={R}: W={w} N={R * w} "
                 f"{s['worst_mah_day']} mAh/d, {s['worst_autonomy_dark_days']} d, PDR {s['pdr_ontime_mean']}")
    return rows


def _binding(cat, cfg, w, pdr_target):
    """What stops W from growing by one."""
    over = {k: v for k, v in cfg.items() if not k.startswith("_")}
    over["net.per_rank"] = w + 1
    _, res = evaluate(cat, over)
    failed = sorted(set(res["summary"]["checks_failed"]) & HARD)
    if res["summary"]["pdr_ontime_mean"] < pdr_target:
        failed.append("pdr_target")
    return ", ".join(failed) or ("W ≥ 2000 (όριο αναζήτησης)" if w >= 2000 else "—")
