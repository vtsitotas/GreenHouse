"""World's-largest-greenhouse study: R = 100 ranks × W = 50 sensors/rank (5 000 nodes).
Fixed network, search the firmware/hardware knobs for the lowest energy that meets
every hard constraint, per cycle (15′/30′) and phase assumption. Run from sim/."""
import itertools
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from meshsim import calculator, config, optimize, params, runlog

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
R, W = 100, 50
PDR = 0.95
cat = params.build()
BASE = {"scheme.technique": "T1-ladder", "scheme.t1_hop_ack": "l2", "net.ranks": R, "net.per_rank": W,
        "scheme.max_ttl": 128,          # firmware 64 cannot reach rank 100 (ceiling 65)
        "scheme.relay_buffer": 121,     # rank 1 carries 99 frames; RTC holds at most 121
        "hw.sun": "athens_winter", "hw.solar": "6v_2w", "req.pdr": PDR}
FILL_MAX = 0.8                      # keep 20 % headroom in every window
PROFILES = {
    "today": ({"hw.climate_sensor": "dht22"},
              {"scheme.t1_slot_s": [2.3, 2.5], "radio.jitter_s": [0.1, 0.3, 0.5],
               "sync.g_max_rule": ["cart_v2", "bias_wander"], "sync.margin_k": [1.5, 2.0, 3.0, 4.0],
               "scheme.rank1_flush_s": [None, 5.0, 8.0]}),
    "today+gateway": ({"hw.climate_sensor": "dht22", "bridge.baud": 921600, "bridge.framing": "binary",
                       "pi.process_s": 0.005},
                      {"scheme.t1_slot_s": [2.3, 2.5], "radio.jitter_s": [0.1, 0.3, 0.5],
                       "sync.g_max_rule": ["cart_v2", "bias_wander"], "sync.margin_k": [1.5, 2.0, 3.0, 4.0],
                       "scheme.rank1_flush_s": [None, 5.0]}),
    "best": ({"hw.climate_sensor": "sht40", "hw.divider": "switched", "bridge.baud": 921600,
              "bridge.framing": "binary", "pi.process_s": 0.005},
             {"scheme.t1_slot_s": [0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.5], "radio.jitter_s": [0.05, 0.1, 0.2],
              "sync.g_max_rule": ["cart_v2", "bias_wander"], "sync.margin_k": [1.5, 2.0, 3.0, 4.0],
              "scheme.rank1_flush_s": [None, 3.0]}),
}
rows = []
t0 = time.time()
for (pname, (fixed, grid)), T, phase in itertools.product(PROFILES.items(), (900, 1800), (False, True)):
    best, n_ok = None, 0
    keys = list(grid)
    for combo in itertools.product(*grid.values()):
        knobs = dict(zip(keys, combo))
        if knobs["radio.jitter_s"] >= knobs["scheme.t1_slot_s"]:
            continue
        if knobs.get("scheme.rank1_flush_s") and knobs["scheme.rank1_flush_s"] <= knobs["scheme.t1_slot_s"]:
            continue
        over = dict(BASE, **fixed, **knobs, **{"timing.T_s": T, "req.latency_s": T, "net.phase_sync": phase})
        cfg = config.resolve(cat, None, over)
        res = calculator.compute(cfg, cat)
        if not optimize.feasible(res, PDR) or res["scheme"]["slot_fill"] > FILL_MAX:
            continue
        n_ok += 1
        if best is None or res["summary"]["worst_mah_day"] < best[1]["summary"]["worst_mah_day"]:
            best = (knobs, res, cfg)
    row = {"profile": pname, "T_s": T, "phase_sync": phase, "feasible_configs": n_ok}
    if best:
        knobs, res, cfg = best
        s, per = res["summary"], res["per_rank"]
        row.update(knobs)
        row.update({"worst_mah_day": s["worst_mah_day"], "leaf_mah_day": s["leaf_mah_day"],
                    "autonomy_dark_days": s["worst_autonomy_dark_days"],
                    "solar_margin_min_x": min(x["solar_margin_x"] for x in per),
                    "pdr_ontime_mean": s["pdr_ontime_mean"], "pdr_ontime_min": min(x["p_ontime"] for x in per),
                    "latency_max_s": s["latency_max_s"], "max_awake_s": s["max_awake_s"],
                    "slot_fill": res["scheme"]["slot_fill"], "channel_util": res["scheme"]["channel_util"],
                    "ladder_span_s": res["scheme"]["ladder_span_s"], "sync_miss": res["sync"]["miss"],
                    "guard_mean_s": res["sync"]["guard_mean"], "soft_failed": s["checks_failed"]})
    else:
        # show what fails for the default-ish config
        over = dict(BASE, **fixed, **{k: v[0] for k, v in grid.items()},
                    **{"timing.T_s": T, "req.latency_s": T, "net.phase_sync": phase})
        res = calculator.compute(config.resolve(cat, None, over), cat)
        row["why_infeasible"] = res["summary"]["checks_failed"]
    rows.append(row)
    print(row)
out = Path(__file__).parent / "runs" / f"{datetime.now():%Y%m%d-%H%M%S}_world_greenhouse_100x50"
out.mkdir(parents=True, exist_ok=True)
(out / "rows.json").write_text(json.dumps(runlog.sanitize(rows), ensure_ascii=False, indent=1), encoding="utf-8")
print(f"done in {time.time() - t0:.0f} s → {out}")
