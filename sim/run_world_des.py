"""DES check of the recommended 100 × 50 configurations (run from sim/)."""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from meshsim import config, des, params, runlog

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
cat = params.build()
COMMON = {"scheme.technique": "T1-ladder", "scheme.t1_hop_ack": "l2", "net.ranks": 100, "net.per_rank": 50,
          "scheme.max_ttl": 128, "scheme.relay_buffer": 121, "timing.T_s": 900, "req.latency_s": 900,
          "sync.g_max_rule": "bias_wander", "sync.margin_k": 2.5, "bridge.baud": 921600,
          "bridge.framing": "binary", "pi.process_s": 0.005, "hw.divider": "switched",
          "des.cycles": 3, "des.trace_cycles": 0}
CASES = {
    "best (SHT40, slot 0.8 s)": {"hw.climate_sensor": "sht40", "scheme.t1_slot_s": 0.8, "radio.jitter_s": 0.05},
    "today+gateway (DHT22, slot 2.3 s)": {"hw.climate_sensor": "dht22", "scheme.t1_slot_s": 2.3, "radio.jitter_s": 0.1},
}
out_rows = []
for name, over in CASES.items():
    t0 = time.time()
    cfg = config.resolve(cat, None, dict(COMMON, **over))
    res = des.simulate(cfg, cat)
    s, p = res["summary"], res["plan"]["summary"]
    row = {"case": name, "seconds": round(time.time() - t0, 1),
           "des_pdr_settled": s["pdr_settled"], "des_pdr": s["pdr"], "des_collision": s["p_collision_per_attempt"],
           "des_worst_mah_day": s["worst_mah_day"], "des_leaf_mah_day": s["leaf_mah_day"],
           "des_latency_median_s": s["latency_median_s"], "des_latency_p95_s": s["latency_p95_s"],
           "bridge_queue_max": s["bridge_queue_max"], "bridge_rejects": s["bridge_rejects"],
           "duplicates": s["duplicates_at_pi"], "tx_attempts": s["tx_attempts"],
           "calc_worst_mah_day": p["worst_mah_day"], "calc_pdr_ontime": p["pdr_ontime_mean"],
           "relay_buffer_max": max(x["relay_buffer_max"] for x in res["per_rank"]),
           "drops": {k: sum(x["drops"][k] for x in res["per_rank"]) for k in res["per_rank"][0]["drops"]}}
    out_rows.append(row)
    print(row)
out = Path(__file__).parent / "runs" / f"{datetime.now():%Y%m%d-%H%M%S}_world_greenhouse_des"
out.mkdir(parents=True, exist_ok=True)
(out / "rows.json").write_text(json.dumps(runlog.sanitize(out_rows), ensure_ascii=False, indent=1), encoding="utf-8")
print(f"→ {out}")
