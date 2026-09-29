"""Geometric study: does "one sensor per plant" hold up in a real greenhouse?
Positions, distance, antenna height, foliage, shadowing and fading decide the
hop length, depth, neighbours, channel load and relay load (meshsim.geometry).
Run from sim/:  python run_world_geometry.py  → sim/runs/<ts>_world_geometry/"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from meshsim import config, geometry, params, runlog

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
cat = params.build()
# the recommended 15-min firmware (docs/simulator/WORLD_GREENHOUSE_100x50.md)
BASE = {"scheme.technique": "T1-ladder", "scheme.t1_hop_ack": "l2", "timing.T_s": 900,
        "scheme.t1_slot_s": 0.8, "radio.jitter_s": 0.05, "hw.climate_sensor": "sht40",
        "hw.divider": "switched", "sync.g_max_rule": "bias_wander", "sync.margin_k": 2.5,
        "pi.process_s": 0.005, "hw.sun": "athens_winter", "hw.solar": "6v_2w"}
HA1 = {"geo.length_m": 200, "geo.width_m": 50}          # 1 ha
HA5 = {"geo.length_m": 500, "geo.width_m": 100}         # 5 ha
HA20 = {"geo.length_m": 1000, "geo.width_m": 200}       # 20 ha, a very large glasshouse
CASES = [
    # A. 1 ha: sensor density and propagation conditions
    ("A 1ha 1/plant", {**HA1, "geo.plants_per_sensor": 1}),
    ("A 1ha 1/10", {**HA1, "geo.plants_per_sensor": 10}),
    ("A 1ha 1/100", {**HA1, "geo.plants_per_sensor": 100}),
    ("A 1ha 1/plant wet", {**HA1, "geo.plants_per_sensor": 1, "geo.wet_factor": 1.3}),
    ("A 1ha 1/plant Rayleigh", {**HA1, "geo.plants_per_sensor": 1, "geo.rician_k_db": None}),
    ("A 1ha 1/plant aisles", {**HA1, "geo.plants_per_sensor": 1, "geo.foliage_frac": 0.5}),
    ("A 1ha 1/plant ant 0dBi", {**HA1, "geo.plants_per_sensor": 1, "geo.ant_gain_dbi": 0.0}),
    # B. 5 ha, one sensor per plant: bridges and channels
    ("B 5ha 1/plant 1br", {**HA5, "geo.plants_per_sensor": 1}),
    ("B 5ha 1/plant 4br", {**HA5, "geo.plants_per_sensor": 1, "geo.bridges": 4, "geo.bridge_layout": "center"}),
    ("B 5ha 1/plant 4br 3ch", {**HA5, "geo.plants_per_sensor": 1, "geo.bridges": 4,
                               "geo.bridge_layout": "center", "geo.channels": 3}),
    # C. 20 ha, one sensor per 10 plants: bridges and the parent policy
    ("C 20ha 1/10 1br rssi", {**HA20, "geo.plants_per_sensor": 10}),
    ("C 20ha 1/10 1br balanced", {**HA20, "geo.plants_per_sensor": 10, "geo.parent_policy": "balanced"}),
    ("C 20ha 1/10 4br rssi", {**HA20, "geo.plants_per_sensor": 10, "geo.bridges": 4, "geo.bridge_layout": "center"}),
    ("C 20ha 1/10 4br balanced", {**HA20, "geo.plants_per_sensor": 10, "geo.bridges": 4,
                                  "geo.bridge_layout": "center", "geo.parent_policy": "balanced"}),
    ("C 20ha 1/10 8br balanced", {**HA20, "geo.plants_per_sensor": 10, "geo.bridges": 8,
                                  "geo.bridge_layout": "center", "geo.parent_policy": "balanced"}),
    ("C 20ha 1/10 8br rssi", {**HA20, "geo.plants_per_sensor": 10, "geo.bridges": 8, "geo.bridge_layout": "center"}),
    # D. one sensor per plant: what brings the channel load under 30 %
    ("D 5ha 1/plant 4br beacon250", {**HA5, "geo.plants_per_sensor": 1, "geo.bridges": 4,
                                     "geo.bridge_layout": "center", "scheme.rx_beacon_period_s": 0.25}),
    ("D 5ha 1/plant 8br", {**HA5, "geo.plants_per_sensor": 1, "geo.bridges": 8, "geo.bridge_layout": "center"}),
    ("D 5ha 1/plant 4br DHT22", {**HA5, "geo.plants_per_sensor": 1, "geo.bridges": 4, "geo.bridge_layout": "center",
                                 "hw.climate_sensor": "dht22", "hw.divider": "220k_x2", "scheme.t1_slot_s": 2.3,
                                 "radio.jitter_s": 0.1}),
]
KEYS = ("sensors", "bridges", "channels", "range_node_node_m", "range_node_bridge_m", "max_rank",
        "max_relay_in_frames", "slot_fill_max", "neighbors_heard_max", "channel_util_max",
        "worst_mah_day", "median_mah_day", "autonomy_dark_days", "latency_max_s", "checks_failed", "soft_failed")
only = sys.argv[1] if len(sys.argv) > 1 else ""
rows = []
for name, over in CASES:
    if only and not name.startswith(only):
        continue
    t0 = time.time()
    res = geometry.evaluate(config.resolve(cat, None, dict(BASE, **over)), cat)
    row = {"case": name, "seconds": round(time.time() - t0, 1), **{k: res[k] for k in KEYS}}
    rows.append(row)
    print(row)
out = Path(__file__).parent / "runs" / f"{datetime.now():%Y%m%d-%H%M%S}_world_geometry"
out.mkdir(parents=True, exist_ok=True)
(out / "rows.json").write_text(json.dumps(runlog.sanitize(rows), ensure_ascii=False, indent=1), encoding="utf-8")
print(f"→ {out}")
