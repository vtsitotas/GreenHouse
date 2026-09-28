"""Hardware component library: the parts we have and the parts we could swap in.

Every entry carries its source and a `kind` (same vocabulary as params.KINDS),
so the calculator can say "this autonomy rests on an unmeasured number".
Units: currents in mA, times in s, capacities in mAh, power in W.
"""

DS_C3 = "ESP32-C3 Datasheet (Espressif)"
DS_SHT4X = "Sensirion SHT4x datasheet v6.4 (Nov 2023), Table 3/4"
DS_BME280 = "Bosch BME280 datasheet BST-BME280-DS001-23 rev 1.23, Table 1 + §9.1"
SNP = "docs/SENSOR_NODE_POWER_AND_SOLAR.md"
EDGE = "docs/EDGE_NODE_POWER_OPTIMIZATION.md"
HPL = "docs/HARDWARE_PARTS_LIST.md"

# ── Board (MCU module) sleep floor, excluding the divider and the sensors ───
BOARDS = {
    "supermini_stock": {
        "sleep_ma": 3.05, "source": f"{EDGE}:39", "kind": "repo-doc",
        "note": "πλακέτα όπως έρχεται: power LED (~3 mA) + LDO + idle αισθητήρες",
    },
    "supermini_led_removed": {
        "sleep_ma": 0.055, "source": f"{SNP}:137", "kind": "repo-doc",
        "note": "LED αφαιρεμένο· 55 µA = RTC + LDO quiescent (εκτίμηση, ΔΕΝ έχει μετρηθεί)",
    },
    "supermini_ldo_bypass": {
        "sleep_ma": 0.010, "source": f"{SNP}:266-270 + {DS_C3} Table 5-9", "kind": "unverified",
        "note": "LiFePO4 κατευθείαν στο 3V3 (χωρίς LDO quiescent): chip 5 µA + ~5 µA διαρροές πλακέτας "
                "— εκτίμηση, πρέπει να μετρηθεί (PPK2/µCurrent)",
    },
    "bare_chip_ideal": {
        "sleep_ma": 0.005, "source": f"{DS_C3}, Table 5-9", "kind": "datasheet",
        "note": "θεωρητικό όριο: μόνο το chip σε deep sleep (RTC timer + RTC memory)",
    },
}

# ── Battery-voltage divider (always across the cell unless switched) ───────
DIVIDERS = {
    "220k_x2": {"sleep_ma": 0.0075, "source": f"{SNP}:46", "kind": "repo-doc",
                "note": "3,3 V / 440 kΩ, πάντα ON (σημερινό)"},
    "1M_x2": {"sleep_ma": 0.00165, "source": "3,3 V / 2 MΩ (Ohm)", "kind": "derived",
              "note": "χρειάζεται πυκνωτή 100 nF στο ADC pin για σωστή ανάγνωση"},
    "switched": {"sleep_ma": 0.0001, "source": "P-MOSFET high-side switch (εκτίμηση διαρροής)", "kind": "unverified",
                 "note": "ρεύμα μόνο κατά την ανάγνωση (16 ms)· +1 εξάρτημα"},
    "none": {"sleep_ma": 0.0, "source": "—", "kind": "model", "note": "χωρίς μέτρηση μπαταρίας"},
}

# ── RTC slow clock source ────────────────────────────────────────────────────
RTC_CLOCKS = {
    "rc136k": {"sleep_ma": 0.0, "source": "sdkconfig CONFIG_RTC_CLK_SRC_INT_RC", "kind": "toolchain",
               "note": "σημερινό· drift 0,17 % (Gate 0 run 1)"},
    "rc_fast_d256": {"sleep_ma": 0.005, "source": "docs/superpowers/specs/2026-09-23-cart-v2-revision.md §2",
                     "kind": "datasheet", "note": "+5 µA, καλύτερη σταθερότητα (Gate 0 run 2 θα το μετρήσει)"},
}

# ── Climate (air T/RH) sensors — all powered from a GPIO, 0 mA when off ─────
# active_ma = current while powered and measuring; settle_s = time from power-on
# until a valid reading; read_s = bus transaction after settle.
CLIMATE_SENSORS = {
    "dht22": {"active_ma": 1.5, "settle_s": 2.0, "read_s": 0.006,
              "source": f"{SNP}:44 + firmware SENSOR_WARMUP_MS", "kind": "repo-doc",
              "note": "σημερινό· το 2 s warm-up είναι το 80 % του ξύπνιου χρόνου"},
    "sht40": {"active_ma": 0.320, "settle_s": 0.001, "read_s": 0.0083,
              "source": DS_SHT4X, "kind": "datasheet",
              "note": "power-up ≤1 ms, μέτρηση high repeatability ≤8,3 ms @ 320 µA· I²C"},
    "bme280": {"active_ma": 0.714, "settle_s": 0.002, "read_s": 0.0093,
               "source": DS_BME280, "kind": "datasheet",
               "note": "forced mode T+P+H ×1: ≤9,3 ms· 714 µA (χειρότερη φάση)· + βαρομετρική πίεση"},
}

SOIL_SENSORS = {
    "capacitive_v12": {"active_ma": 5.0, "settle_s": 0.1, "source": f"{SNP}:45",
                       "kind": "unverified",
                       "note": "5 mA (repo)· χρόνος σταθεροποίησης 0,1 s ΔΕΝ είναι μετρημένος "
                               "(σήμερα 'κρύβεται' στα 2 s του DHT22)"},
    "none": {"active_ma": 0.0, "settle_s": 0.0, "source": "—", "kind": "model", "note": ""},
}

# ── What the MCU does while the sensors settle ───────────────────────────────
WARMUP_MODES = {
    "radio_on": {"note": "σημερινό firmware: ESP-NOW ανοιχτό κατά το warm-up (I_RX)", "kind": "firmware"},
    "cpu_idle": {"note": "radio κλειστό, CPU σε delay (modem-sleep idle)", "kind": "datasheet"},
    "light_sleep": {"note": "light sleep με GPIO hold στους αισθητήρες (130 µA chip)", "kind": "datasheet"},
}

# ── Batteries ────────────────────────────────────────────────────────────────
BATTERIES = {
    "lifepo4_18650_1500": {"mah": 1500, "v": 3.2, "dod": 0.8, "self_discharge_pct_month": 3.0,
                           "source": f"{SNP}:47,167", "kind": "repo-doc",
                           "note": "σημερινό· αυτοεκφόρτιση 3 %/μήνα = τυπική LiFePO4 (μη μετρημένη)"},
    "lifepo4_26650_3000": {"mah": 3000, "v": 3.2, "dod": 0.8, "self_discharge_pct_month": 3.0,
                           "source": "τυπική χωρητικότητα αγοράς 26650 LiFePO4", "kind": "unverified",
                           "note": "ίδια χημεία/τάση → ίδιο firmware, μεγαλύτερη θήκη"},
}

# ── Solar ────────────────────────────────────────────────────────────────────
SOLAR_PANELS = {
    "none": {"w": 0.0, "source": "—", "kind": "model"},
    "6v_2w": {"w": 2.0, "source": f"{SNP}:222-231", "kind": "repo-doc", "note": "σημερινό"},
    "5v_1w": {"w": 1.0, "source": f"{SNP}:49", "kind": "repo-doc",
              "note": "5 V: κίνδυνος να πέσει κάτω από το dropout του TP5000 σε ζέστη"},
}
SUN_PSH = {
    "athens_winter": {"psh": 2.0, "source": f"{SNP}:224-226", "kind": "repo-doc"},
    "athens_summer": {"psh": 6.5, "source": "τυπική τιμή PVGIS για Αθήνα (Ιούλιος) — προσέγγιση", "kind": "unverified"},
    "indoor_shade": {"psh": 0.3, "source": "υπόθεση: σκιά φυλλώματος/εσωτερικό θερμοκηπίου", "kind": "model"},
}

# ── Gateway side (mains today; off-grid option for remote sites) ─────────────
PI = {
    "pi_zero_w": {"wh_day": 30.0, "source": f"{HPL}:182", "kind": "repo-doc",
                  "note": "~30 Wh/ημέρα ≈ 1,25 W μέσος όρος"},
}
BRIDGE_BOARD = {
    "esp32c3_supermini": {"i_ma": 84.0, "v": 5.0, "source": f"{DS_C3}, Table 5-7 (RX)", "kind": "datasheet",
                          "note": "πάντα σε RX· τροφοδοσία από τα 5 V του Pi μέσω LDO (ίδιο ρεύμα)"},
}
OFFGRID = {
    "pack_12v_lifepo4_6ah": {"wh": 12.8 * 6, "source": f"{HPL}:182", "kind": "repo-doc"},
    "pack_12v_lifepo4_12ah": {"wh": 12.8 * 12, "source": f"{HPL}:182", "kind": "repo-doc"},
    "panel_20w": {"w": 20.0, "source": f"{HPL}:190", "kind": "repo-doc"},
}


def library():
    """Everything, for the catalogue and the dashboard."""
    return {"boards": BOARDS, "dividers": DIVIDERS, "rtc_clocks": RTC_CLOCKS,
            "climate_sensors": CLIMATE_SENSORS, "soil_sensors": SOIL_SENSORS,
            "warmup_modes": WARMUP_MODES, "batteries": BATTERIES, "solar_panels": SOLAR_PANELS,
            "sun": SUN_PSH, "pi": PI, "bridge_board": BRIDGE_BOARD, "offgrid": OFFGRID}
