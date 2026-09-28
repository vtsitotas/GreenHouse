"""Every tunable variable of a run, as flat dotted keys.

Defaults come from the parameter catalogue (so a firmware change moves them
too) and the hardware library. A run = defaults ← preset ← --set overrides.

    python -m meshsim keys          # list every key with default + meaning
"""
import copy
import json

from . import hardware as hw

# (key, default | callable(cat), description, choices or None)
SPEC = [
    # ── network / scenario ────────────────────────────────────────────────
    ("net.ranks", 50, "βάθος δικτύου (ranks)", None),
    ("net.per_rank", 10, "κόμβοι ανά rank (ισορροπημένο layered δέντρο)", None),
    ("net.max_children", 3, "χειρότερος relay: παιδιά στον πιο φορτωμένο parent (για συγκρούσεις)", None),
    ("net.hidden_frac", 0.3, "ποσοστό ζευγών αδελφών που δεν ακούγονται (hidden terminals)", None),
    # ── timing ────────────────────────────────────────────────────────────
    ("timing.T_s", 900, "κύκλος αφύπνισης (s)· standards 900 / 1800", None),
    ("timing.report_every", 1, "αποστολή κάθε k μετρήσεις (batching· >1 θέλει αλλαγή πακέτου)", None),
    ("timing.t_boot_s", 0.2, "deep sleep → app (140–230 ms, repo)", None),
    ("timing.radio_init_s", 0.1, "WiFi/ESP-NOW init (ΜΗ μετρημένο)", None),
    ("timing.awake_cap_s", lambda c: c["MESH_WAKE_MAX_AWAKE_MS"] / 1000, "σκληρό όριο αφύπνισης (firmware)", None),
    ("timing.app_ack_wait_s", lambda c: c["MESH_APP_ACK_WAIT_MS"] / 1000, "αναμονή app-ACK (firmware)", None),
    # ── scheme ────────────────────────────────────────────────────────────
    ("scheme.technique", "T1-ladder", "τεχνική", ["phase1", "T1-ladder", "T2-window"]),
    ("scheme.t2_ack", "unicast", "T2: πώς γυρίζει το ACK", ["flood", "unicast", "aggregate"]),
    ("scheme.t1_hop_ack", "per_frame", "T1: ACK ανά frame ή ένα ανά ριπή", ["per_frame", "batch"]),
    ("scheme.max_ttl", lambda c: c["MESH_MAX_TTL"], "MESH_MAX_TTL (firmware 16)", None),
    ("scheme.ttl_margin", lambda c: c["MESH_TTL_MARGIN"], "MESH_TTL_MARGIN", None),
    ("scheme.relay_buffer", 50, "buffer parent/relay (frames)· άνω όριο RTC από §H", None),
    ("scheme.own_buffer", lambda c: c["MESH_DATA_BUFFER_SIZE"], "buffer δικών μετρήσεων", None),
    # ── clock sync (CART generalised) ─────────────────────────────────────
    ("sync.bias", lambda c: c["DRIFT_BIAS_MEASURED"], "σχετικό bias ρολογιού ζεύγους (0,17 % μετρημένο)", None),
    ("sync.step_per_300s", lambda c: c["DRIFT_STEP_PER_300S"], "innovation ρυθμού ανά κύκλο 300 s (∝ T)", None),
    ("sync.policy", "margin", "guard policy", ["margin", "aimd"]),
    ("sync.g_min_s", lambda c: c["MESH_WAKE_GUARD_MIN_MS"] / 1000, "ελάχιστο guard", None),
    ("sync.g_max_s", None, "μέγιστο guard (None = από τον κανόνα)", None),
    ("sync.g_max_rule", "cart_v2", "κανόνας G_max: cart_v2 = 2·|b|·T·1,3 · bias_wander = + ±zσ περιπλάνησης",
     ["cart_v2", "bias_wander"]),
    ("sync.cycles", 20000, "κύκλοι Monte Carlo για τα στατιστικά συγχρονισμού", None),
    ("sync.z", 3.0, "σ-περιθώριο για συσσωρευμένη απόκλιση (T2)", None),
    # ── radio / MAC ───────────────────────────────────────────────────────
    ("radio.link_margin_db", 10.0, "RSSI πάνω από την ευαισθησία σε κάθε link", None),
    ("radio.mac_retry", lambda c: c["MAC_RETRY_LIMIT"], "MAC retransmissions (μη τεκμηριωμένο)", None),
    ("radio.cw", lambda c: c["CW_MIN"], "contention window (slots)", None),
    ("radio.jitter_s", lambda c: c["MESH_CART_JITTER_MS"] / 1000, "jitter αποστολής μέσα στο slot (J)", None),
    ("radio.attempts", lambda c: c["MESH_CART_ATTEMPTS"], "app-level προσπάθειες ανά slot", None),
    ("radio.hop_proc_s", 0.002, "επεξεργασία ανά hop (CMAC verify, callback) — μοντέλο", None),
    # ── bridge / Pi ───────────────────────────────────────────────────────
    ("bridge.baud", lambda c: c["UART_BAUD"], "UART baud γέφυρας ↔ Pi", None),
    ("bridge.framing", "hex_json", "μορφή γραμμής UART", ["hex_json", "binary"]),
    ("bridge.usb_echo", False, "USB debug echo (με host που δεν διαβάζει: έως ~2 s block)", None),
    ("bridge.ingress_queue", lambda c: c["BRIDGE_INGRESS_QUEUE"], "ουρά εισόδου γέφυρας (frames)", None),
    ("pi.process_s", lambda c: c["PI_PROCESS_MS"] / 1000, "Pi επεξεργασία ανά frame (ΜΗ μετρημένο)", None),
    # ── hardware ──────────────────────────────────────────────────────────
    ("hw.board", "supermini_led_removed", "πλακέτα / sleep floor", list(hw.BOARDS)),
    ("hw.divider", "220k_x2", "divider μπαταρίας", list(hw.DIVIDERS)),
    ("hw.rtc_clock", "rc136k", "πηγή RTC ρολογιού", list(hw.RTC_CLOCKS)),
    ("hw.climate_sensor", "dht22", "αισθητήρας αέρα", list(hw.CLIMATE_SENSORS)),
    ("hw.soil_sensor", "capacitive_v12", "αισθητήρας εδάφους", list(hw.SOIL_SENSORS)),
    ("hw.warmup_mode", "radio_on", "τι κάνει το MCU στο warm-up", list(hw.WARMUP_MODES)),
    ("hw.battery", "lifepo4_18650_1500", "μπαταρία", list(hw.BATTERIES)),
    ("hw.battery_mah", None, "override χωρητικότητας (None = από τη μπαταρία)", None),
    ("hw.solar", "6v_2w", "ηλιακό πάνελ", list(hw.SOLAR_PANELS)),
    ("hw.sun", "athens_winter", "ηλιοφάνεια (peak sun hours)", list(hw.SUN_PSH)),
    ("hw.solar_derate", 0.5, "απώλειες σύννεφα/σκόνη/γωνία/θερμοκρασία", None),
    ("hw.charger_eff", 0.7, "απόδοση TP5000", None),
    # ── energy model ──────────────────────────────────────────────────────
    ("energy.model", "per_state", "ενεργειακό μοντέλο", ["per_state", "lumped"]),
    ("energy.i_tx_ma", lambda c: c["I_TX_MA"], "TX (datasheet @21 dBm = άνω φράγμα)", None),
    ("energy.i_rx_ma", lambda c: c["I_RX_MA"], "RX / radio ανοιχτό", None),
    ("energy.i_cpu_ma", lambda c: c["I_CPU_RUN_MA"], "CPU run, radio off", None),
    ("energy.i_cpu_idle_ma", lambda c: c["I_CPU_IDLE_MA"], "CPU idle, radio off", None),
    ("energy.i_light_sleep_ma", lambda c: c["I_LIGHT_SLEEP_UA"] / 1000, "light sleep chip", None),
    ("energy.i_active_lumped_ma", lambda c: c["I_ACTIVE_LUMPED_MA"], "lumped: ενιαίο ρεύμα awake", None),
    ("energy.phase1_lumped_awake_s", lambda c: c["AWAKE_PHASE1_S"], "lumped phase1: awake του repo", None),
    # ── discrete-event simulation ─────────────────────────────────────────
    ("des.cycles", 3, "κύκλοι που προσομοιώνονται", None),
    ("des.seed", 1, "seed (ίδιο seed → ίδιο αποτέλεσμα)", None),
    ("des.seeds", 1, "πόσα seeds (>1 → μέσος όρος ± 95 % CI)", None),
    ("des.trace_cycles", 1, "κύκλοι που καταγράφονται στο timeline (Gantt)", None),
    ("des.shadow_sigma_db", lambda c: c["SHADOWING_SIGMA_DB"], "shadowing ανά link (dB)", None),
    ("des.window_s", None, "T2: μήκος παραθύρου (None = από τον calculator)", None),
    ("des.clock_burnin", 64, "κύκλοι προθέρμανσης ρολογιών πριν την προσομοίωση", None),
    ("des.sweep_energy", "expected", "ενέργεια sweeps: αναμενόμενη (μοντέλο) ή παρατηρημένη",
     ["expected", "observed"]),
    # ── requirements (for checks / optimisation) ──────────────────────────
    ("req.lifetime_days", 365, "στόχος αυτονομίας χωρίς ήλιο (ημέρες)", None),
    ("req.latency_s", 900, "μέγιστη αποδεκτή καθυστέρηση μέτρησης (s)", None),
    ("req.pdr", 0.99, "ελάχιστο ποσοστό παράδοσης ανά κύκλο", None),
]

PRESETS = {
    "firmware_today": {
        "_doc": "Baseline: ό,τι τρέχει σήμερα στο bench (Phase 1, 3 αισθητήρες, T = 60 s test)",
        "net.ranks": 1, "net.per_rank": 3, "timing.T_s": 60, "scheme.technique": "phase1",
    },
    "stress_50x10": {
        "_doc": "Το stress-test: 50 ranks × 10 κόμβοι, όλοι sleepy + relay, 15′",
        "net.ranks": 50, "net.per_rank": 10, "timing.T_s": 900, "scheme.technique": "T1-ladder",
    },
    "greenhouse": {
        "_doc": "Θερμοκήπιο: ~40 κόμβοι, 4 hops, 15′ (αρχικό σημείο — επιβεβαίωση από γεωπόνο)",
        "net.ranks": 4, "net.per_rank": 10, "timing.T_s": 900, "scheme.technique": "T2-window",
        "req.latency_s": 900,
    },
    "nursery": {
        "_doc": "Φυτώριο: πυκνό, ρηχό (3 hops × 15), 15′",
        "net.ranks": 3, "net.per_rank": 15, "timing.T_s": 900, "scheme.technique": "T2-window",
        "req.pdr": 0.995,
    },
    "field": {
        "_doc": "Χωράφι: αραιό, αργή δυναμική εδάφους, 30′ (μεγάλες αποστάσεις → LoRa ανά τμήμα)",
        "net.ranks": 2, "net.per_rank": 10, "timing.T_s": 1800, "scheme.technique": "T2-window",
        "req.latency_s": 1800, "hw.sun": "athens_winter",
    },
}


def defaults(cat):
    out = {}
    for key, default, _desc, _choices in SPEC:
        out[key] = default(cat) if callable(default) else copy.deepcopy(default)
    return out


def describe(cat):
    d = defaults(cat)
    return [{"key": k, "default": d[k], "desc": desc, "choices": ch} for k, _, desc, ch in SPEC]


def parse_value(text):
    """--set values: JSON when it parses (numbers, true/false, null, lists), else a string."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def resolve(cat, preset=None, overrides=None):
    cfg = defaults(cat)
    layers = []
    if preset:
        if preset not in PRESETS:
            raise KeyError(f"unknown preset {preset!r}; choose from {sorted(PRESETS)}")
        layers.append({k: v for k, v in PRESETS[preset].items() if not k.startswith("_")})
    if overrides:
        layers.append(overrides)
    choices = {k: ch for k, _, _, ch in SPEC}
    for layer in layers:
        for k, v in layer.items():
            if k not in cfg:
                raise KeyError(f"unknown key {k!r} (see `python -m meshsim keys`)")
            if choices[k] and v not in choices[k]:
                raise ValueError(f"{k}={v!r}: choose from {choices[k]}")
            cfg[k] = v
    cfg["_preset"] = preset
    return cfg
