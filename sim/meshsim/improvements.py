"""What-if catalogue: every hardware / firmware / gateway improvement we know,
evaluated one at a time against a baseline config and all together."""
from . import calculator

# (id, label, category, overrides, caveat)
IMPROVEMENTS = [
    ("sensor_sht40", "SHT40 αντί DHT22", "hardware",
     {"hw.climate_sensor": "sht40"}, "I²C, ίδια pins τροφοδοσίας· αλλαγή driver στο firmware"),
    ("sensor_bme280", "BME280 αντί DHT22 (+πίεση)", "hardware",
     {"hw.climate_sensor": "bme280"}, "δίνει και βαρομετρική πίεση στο weather.py"),
    ("warmup_light_sleep", "light sleep στο warm-up αισθητήρων", "firmware",
     {"hw.warmup_mode": "light_sleep"}, "χρειάζεται gpio_hold στα pins τροφοδοσίας αισθητήρων"),
    ("warmup_cpu_idle", "radio κλειστό στο warm-up", "firmware",
     {"hw.warmup_mode": "cpu_idle"}, "απλούστερο από light sleep"),
    ("divider_switched", "switched divider μπαταρίας", "hardware",
     {"hw.divider": "switched"}, "+1 P-MOSFET"),
    ("divider_1m", "divider 2×1 MΩ", "hardware",
     {"hw.divider": "1M_x2"}, "+100 nF στο ADC"),
    ("board_ldo_bypass", "LiFePO4 κατευθείαν στο 3V3 (χωρίς LDO)", "hardware",
     {"hw.board": "supermini_ldo_bypass"}, "ΜΗ μετρημένο floor — μέτρηση με PPK2 πρώτα"),
    ("battery_26650", "LiFePO4 26650 3000 mAh", "hardware",
     {"hw.battery": "lifepo4_26650_3000"}, "μεγαλύτερη θήκη, ίδιο firmware"),
    ("rtc_fast_clock", "RTC από RC_FAST/256 (+5 µA)", "firmware",
     {"hw.rtc_clock": "rc_fast_d256"}, "εδώ φαίνεται μόνο το κόστος· το όφελος drift θα το μετρήσει το Gate 0 run 2"),
    ("max_ttl_64", "MESH_MAX_TTL 16 → 64", "firmware",
     {"scheme.max_ttl": 64}, "3 σημεία (firmware, Pi, γέφυρα) + reflash όλων μαζί"),
    ("bridge_fast_uart", "γέφυρα 921600 baud + binary framing", "gateway",
     {"bridge.baud": 921600, "bridge.framing": "binary"}, "Pi: PL011 στο UART (dtoverlay=disable-bt)"),
    ("pi_fast", "Pi 5 ms/frame (cache nodes.json, batch publish)", "gateway",
     {"pi.process_s": 0.005}, "ο σημερινός χρόνος δεν έχει μετρηθεί"),
    ("t2_ack_aggregate", "T2: aggregated ACK (bitmap ανά relay)", "firmware",
     {"scheme.t2_ack": "aggregate"}, "νέο μήνυμα ACK"),
    ("t1_batch_ack", "T1: ένα hop-ACK ανά ριπή", "firmware",
     {"scheme.t1_hop_ack": "batch"}, "προσοχή στα 32 dyn TX buffers"),
    ("gmax_x2", "διπλάσιο G_max (λιγότερα sweeps)", "firmware",
     {"_gmax_factor": 2.0}, "περισσότερο listen ανά χαμένο wake, πολύ λιγότερα orphan sweeps"),
    ("gmax_bias_wander", "κανόνας G_max: bias + περιπλάνηση θερμοκρασίας", "firmware",
     {"sync.g_max_rule": "bias_wander"}, "διόρθωση του κανόνα του CART v2 §3.3 (εύρημα του simulator)"),
    ("T_1800", "κύκλος 30′", "config",
     {"timing.T_s": 1800}, "αραιότερα δεδομένα"),
    ("report_every_2", "αποστολή ανά 2 μετρήσεις (batching)", "firmware",
     {"timing.report_every": 2}, "νέα μορφή πακέτου· διπλάσια καθυστέρηση"),
]

# Everything that costs no data freshness: the "free" combined upgrade.
FREE_BUNDLE = ["sensor_sht40", "warmup_light_sleep", "divider_switched", "max_ttl_64",
               "bridge_fast_uart", "pi_fast", "t2_ack_aggregate", "gmax_bias_wander"]


def _apply(cat, base_cfg, overrides):
    over = dict(overrides)
    factor = over.pop("_gmax_factor", None)
    cfg = dict(base_cfg)
    for k, v in over.items():
        if cfg.get(k) == v:
            continue
        cfg[k] = v
    if factor:
        from . import clock
        Tc = cfg["timing.T_s"] * cfg["timing.report_every"]
        step = clock.step_for(Tc, cfg["sync.step_per_300s"])
        g = cfg["sync.g_max_s"] or clock.g_max_rule(cfg["sync.g_max_rule"], cfg["sync.bias"], step, Tc,
                                                    cat["G_MAX_FACTOR"], cfg["sync.g_min_s"], cfg["sync.z"])
        cfg["sync.g_max_s"] = g * factor
    return cfg


def _row(id_, label, category, caveat, res, base):
    s, b = res["summary"], base["summary"]
    pct = lambda new, old: round(100 * (new - old) / old, 1) if old else 0.0
    return {
        "id": id_, "label": label, "category": category, "caveat": caveat,
        "worst_mah_day": s["worst_mah_day"], "worst_delta_pct": pct(s["worst_mah_day"], b["worst_mah_day"]),
        "leaf_mah_day": s["leaf_mah_day"], "leaf_delta_pct": pct(s["leaf_mah_day"], b["leaf_mah_day"]),
        "lifetime_dark_days": s["network_lifetime_dark_days"],
        "latency_max_s": s["latency_max_s"], "pdr_ontime_mean": s["pdr_ontime_mean"],
        "delivered_nodes": s["delivered_nodes"], "checks_failed": len(s["checks_failed"]),
        "max_awake_s": s["max_awake_s"],
    }


def evaluate(cat, base_cfg):
    base = calculator.compute(base_cfg, cat)
    rows = [_row("baseline", "βάση (τρέχουσα ρύθμιση)", "—", "", base, base)]
    by_id = {i[0]: i for i in IMPROVEMENTS}
    for id_, label, category, over, caveat in IMPROVEMENTS:
        cfg = _apply(cat, base_cfg, over)
        if cfg == base_cfg:
            continue
        if id_.startswith("t2_") and base_cfg["scheme.technique"] != "T2-window":
            continue
        if id_.startswith("t1_") and base_cfg["scheme.technique"] != "T1-ladder":
            continue
        rows.append(_row(id_, label, category, caveat, calculator.compute(cfg, cat), base))
    bundle = {}
    for id_ in FREE_BUNDLE:
        bundle.update(by_id[id_][3])
    cfg = _apply(cat, base_cfg, bundle)
    rows.append(_row("free_bundle", "ΟΛΕΣ οι «δωρεάν» βελτιώσεις μαζί", "σύνολο",
                     "χωρίς αλλαγή κύκλου/φρεσκάδας", calculator.compute(cfg, cat), base))
    return base, rows
