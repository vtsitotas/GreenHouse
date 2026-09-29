"""The parameter catalogue: every number the simulator uses, with its source.

Values that exist in the firmware / Pi / toolchain are taken from
firmware_params (parsed, never retyped). Hardware, standard and repo-doc
values carry an exact citation. Model and scenario values are marked as
such so nobody mistakes an assumption for a measurement.

    python -m meshsim params --md docs/simulator/PARAMETERS.md
"""
import json
from dataclasses import asdict, dataclass

from . import analytic as an
from .explain import CONFIG as CONFIG_EXPLAIN, for_param as explain_param
from . import firmware_params

GROUPS = [
    ("A1", "Firmware — πακέτα και μηνύματα"),
    ("A2", "Firmware — routing, beacons, trickle"),
    ("A3", "Firmware — TTL"),
    ("A4", "Firmware — buffers και μνήμη"),
    ("A5", "Firmware — κύκλος αφύπνισης (sleepy κόμβος)"),
    ("A6", "Γέφυρα (bridge) και Pi"),
    ("A7", "CART depth N: όλοι κοιμούνται και κάνουν relay (firmware + σχεδιασμένες τιμές)"),
    ("B", "Hardware ESP32-C3 (datasheet) και toolchain (sdkconfig)"),
    ("C", "Πλακέτα, ενέργεια, μπαταρία, ηλιακό"),
    ("D", "PHY/MAC: IEEE 802.11 DSSS + ESP-NOW frame"),
    ("E", "Μοντέλο ραδιοδιάδοσης και καναλιού"),
    ("F", "Σενάριο (ρυθμίζεται ανά run)"),
    ("G", "Παράγωγες ποσότητες (υπολογίζονται)"),
    ("H", "Relay buffer: προϋπολογισμός RTC μνήμης"),
]

KINDS = {
    "firmware": "διαβάζεται από τον κώδικα firmware/Pi (parser)",
    "toolchain": "διαβάζεται από το sdkconfig / core του arduino-esp32",
    "datasheet": "ESP32-C3 datasheet (Espressif)",
    "standard": "IEEE 802.11 / τεκμηρίωση ESP-IDF",
    "repo-doc": "τεκμηρίωση του repo (εκτίμηση σχεδίασης, όχι μέτρηση)",
    "measured": "μέτρηση στο bench",
    "planned": "σχεδιασμένο (spec/plan), δεν υπάρχει ακόμα στο firmware",
    "model": "υπόθεση μοντέλου του simulator (ρυθμιζόμενη)",
    "unverified": "μη τεκμηριωμένο από την Espressif — ρυθμιζόμενο, με sweep",
    "scenario": "παράμετρος σεναρίου (ορίζεται ανά run)",
    "derived": "υπολογίζεται από τις παραπάνω τιμές",
}

DS = "ESP32-C3 Datasheet (Espressif)"
SNP = "docs/SENSOR_NODE_POWER_AND_SOLAR.md"
CARTV2 = "docs/superpowers/specs/2026-09-23-cart-v2-revision.md"
RES = "docs/analysis/cart_sim_results.txt"
ESPNOW_DOC = "ESP-IDF ESP-NOW guide (frame format, default rate)"
IEEE = "IEEE 802.11-2020 Clause 15 (DSSS PHY)"


@dataclass
class Param:
    group: str
    key: str
    value: object
    unit: str = ""
    source: str = ""
    note: str = ""
    kind: str = "firmware"


class Catalogue:
    def __init__(self):
        self.params = {}

    def add(self, group, key, value, unit="", source="", note="", kind="firmware"):
        if key in self.params:
            raise KeyError(f"duplicate parameter {key}")
        self.params[key] = Param(group, key, value, unit, source, note, kind)
        return value

    def __getitem__(self, key):
        return self.params[key].value

    def __contains__(self, key):
        return key in self.params

    def group(self, gid):
        return [p for p in self.params.values() if p.group == gid]

    def to_dict(self):
        return {k: asdict(p) for k, p in self.params.items()}


def build(fw=None):
    fw = fw or firmware_params.load()
    D, S, L, PY, TC = (fw[k] for k in ("defines", "structs", "literals", "python", "toolchain"))
    c = Catalogue()

    def fwd(group, name, unit="", note=""):
        return c.add(group, name, D[name]["value"], unit, D[name]["src"], note, "firmware")

    def lit(group, name, unit="", note=""):
        return c.add(group, name, L[name]["value"], unit, L[name]["src"], note, "firmware")

    def struct(group, name, note=""):
        return c.add(group, f"sizeof({name})", S[name]["size"], "B", S[name]["src"], note, "firmware")

    def py(group, name, unit="", note=""):
        return c.add(group, name, PY[name]["value"], unit, PY[name]["src"], note, "firmware")

    def tc(group, name, unit="", note="", fallback=None, fb_src=""):
        if name in TC:
            return c.add(group, name, TC[name]["value"], unit, TC[name]["src"], note, "toolchain")
        return c.add(group, name, fallback, unit, fb_src, note + " (fallback — λείπει το Arduino15)", "toolchain")

    # ── A1 packets ───────────────────────────────────────────────────────────
    fwd("A1", "MESH_PACKET_LEN", "B", "sealed data frame = header + nettag + ciphertext + apptag")
    fwd("A1", "MESH_HEADER_LEN", "B", "magic, origin MAC, seq, boot_count, flags, rank, ttl")
    fwd("A1", "MESH_NETTAG_LEN", "B", "AES-CMAC(NetKey) truncated, ελέγχεται από κάθε relay")
    fwd("A1", "MESH_BODY_LEN", "B", "AES-GCM ciphertext: T, H, soil, battery_mv, parent MAC, RSSI")
    fwd("A1", "MESH_APPTAG_LEN", "B", "GCM tag, ανοίγει μόνο το Pi")
    fwd("A1", "MESH_AAD_LEN", "B", "το ttl (byte 15) εξαιρείται — αλλάζει σε κάθε hop")
    struct("A1", "MeshBeacon", "broadcast, cleartext + nettag")
    struct("A1", "MeshAck", "app-level ACK, flood broadcast")
    struct("A1", "MeshJoinBeacon", "μόνο μη-enrolled κόμβοι")
    fwd("A1", "MESH_PROVISION_LEN", "B", "NetKey 16 + flags 1 + tag 16")
    c.add("A1", "BEACON_V3_LEN", 29, "B", f"{CARTV2} §3.1",
          "CART: + sleepy_depth + guard_hint_q", "planned")

    # ── A2 routing / beacons ─────────────────────────────────────────────────
    fwd("A2", "MESH_BEACON_INTERVAL_MIN_MS", "ms", "trickle floor (reset target)")
    fwd("A2", "MESH_BEACON_INTERVAL_MAX_MS", "ms", "trickle ceiling· κάθε beacon διπλασιάζει το διάστημα")
    fwd("A2", "MESH_BRIDGE_BEACON_INTERVAL_MS", "ms", "η γέφυρα δεν κάνει backoff")
    fwd("A2", "MESH_PARENT_TIMEOUT_FACTOR", "×", "parent χάνεται μετά από 3× το advertised interval")
    lit("A2", "TX_FAIL_DROP_COUNT", "tx", "διαδοχικές αποτυχίες unicast → drop parent")
    fwd("A2", "MESH_ORPHAN_FRESH_MS", "ms", "UNROUTED beacon από MAC σιωπηλή τόσο → νέο orphan")
    fwd("A2", "MESH_ORPHAN_RESET_MIN_GAP_MS", "ms", "≤1 orphan-triggered trickle reset ανά 10 s")
    fwd("A2", "MESH_RESCAN_AFTER_MS", "ms", "always-on unrouted → επιβεβαίωση καναλιού")
    fwd("A2", "MESH_WINDOW_DURATION_MS", "ms", "μεταφέρεται στο beacon, αχρησιμοποίητο σήμερα")
    fwd("A2", "MESH_RANK_UNROUTED", "", "sentinel: χωρίς parent")
    fwd("A2", "MESH_NEIGHBOR_SLOTS", "slots", "LRU πίνακας γειτόνων (orphan detection)")
    fwd("A2", "MESH_JOIN_BEACON_INTERVAL_MS", "ms", "μόνο μη-enrolled")
    fwd("A2", "MESH_FIXED_CHANNEL", "", "όλοι οι κόμβοι στο ίδιο κανάλι (2412 MHz)")
    c.add("A2", "PARENT_SELECTION", "strict rank < own· μετά μικρότερο rank· μετά RSSI",
          "", f"{firmware_params.NODE_H}:374", "RPL strict-rank → δομικά χωρίς loops")
    cart_on = D.get("MESH_CART_ENABLE", {}).get("value") == 1
    c.add("A2", "SLEEPY_PARENT_RULE",
          "sleepy parent δεκτός όταν το beacon έχει RX_OPEN και RELAY_CAP (CART depth N)" if cart_on
          else "sleepy beacon ποτέ parent (Phase 1)", "",
          f"{firmware_params.NODE_H} meshHandleBeacon()",
          "MESH_CART_ENABLE=0 επαναφέρει το Phase 1" if cart_on else "το CART το αίρει")

    # ── A3 TTL ───────────────────────────────────────────────────────────────
    fwd("A3", "MESH_TTL_MARGIN", "hops", "data ttl = rank + margin, στο transmit")
    fwd("A3", "MESH_MAX_TTL", "hops", "ανώτατο όριο· relay κάνει drop ttl>max ή ttl==0")
    fwd("A3", "MESH_ACK_TTL", "hops", "fallback της γέφυρας αν το Pi δεν στείλει ttl")
    py("A3", "ACK_TTL_MARGIN", "hops", "Pi: ACK ttl = min(ACK_TTL_MAX, rank + margin)")
    py("A3", "ACK_TTL_MAX", "hops", "")

    # ── A4 buffers / memory ──────────────────────────────────────────────────
    fwd("A4", "MESH_DATA_BUFFER_SIZE", "frames", "δικές του μετρήσεις, ring drop-oldest, σε RTC")
    fwd("A4", "MESH_INFLIGHT_MAX", "frames", "frames που περιμένουν app-ACK σε ένα wake")
    fwd("A4", "MESH_DEDUP_CACHE_SIZE", "entries", "(origin, seq) ring σε κάθε relay και στη γέφυρα")
    fwd("A4", "MESH_DEDUP_WINDOW_MS", "ms", "static_assert: MAX_AWAKE < window < SLEEP_INTERVAL")
    fwd("A4", "MESH_ACK_DEDUP_CACHE_SIZE", "entries", "(target, seq) ring για ACK flood")
    struct("A4", "MeshRtcState", "επιβιώνει στον deep sleep (RTC FAST)")
    struct("A4", "MeshInFlightEntry", "RAM μόνο")
    if cart_on and "MESH_RELAY_BUFFER_SIZE" in D:
        c.add("A4", "RELAY_BUFFER_TODAY", D["MESH_RELAY_BUFFER_SIZE"]["value"], "frames",
              D["MESH_RELAY_BUFFER_SIZE"]["src"],
              "sleepy relays κρατούν frames σε RTC ως το παράθυρο του parent· always-on relays κάνουν cut-through")
    else:
        c.add("A4", "RELAY_BUFFER_TODAY", 0, "frames", f"{firmware_params.NODE_H} meshRelayData()",
              "τα relays κάνουν cut-through, χωρίς buffer")

    # ── A5 wake cycle ────────────────────────────────────────────────────────
    fwd("A5", "MESH_SLEEP_INTERVAL_MS", "ms",
        "τιμή test στο firmware σήμερα· στον sim το T ορίζεται ανά run (§F)")
    fwd("A5", "SENSOR_WARMUP_MS", "ms", "αισθητήρες ON· επικαλύπτεται με το radio bring-up")
    fwd("A5", "MESH_TX_CONFIRM_WAIT_MS", "ms", "αναμονή send-callback (L2 ACK)")
    fwd("A5", "MESH_APP_ACK_WAIT_MS", "ms", "αναμονή ACK από το Pi· αναπάντητα → επόμενο wake")
    fwd("A5", "MESH_WAKE_DISCOVERY_MS", "ms", "αναζήτηση νέου parent μετά από αποτυχία")
    fwd("A5", "MESH_WAKE_MAX_AWAKE_MS", "ms", "σκληρό όριο αφύπνισης")
    fwd("A5", "MESH_MIN_SLEEP_MS", "ms", "ελάχιστος ύπνος")
    lit("A5", "BATT_ADC_SAMPLES", "δείγματα", "")
    lit("A5", "BATT_ADC_SAMPLE_DELAY_MS", "ms", "")
    lit("A5", "COLD_BOOT_USB_WAIT_MS", "ms", "μόνο σε cold boot, όχι σε timer wake")
    lit("A5", "UNCONFIRMED_WAKES_RESCAN", "wakes", "link counter → rescan καναλιού")
    fwd("A5", "SEND_INTERVAL_MS", "ms", "always-on: περίοδος ≈ SEND_INTERVAL + WARMUP")

    # ── A6 bridge / Pi ───────────────────────────────────────────────────────
    fwd("A6", "UART_BAUD", "baud", "γέφυρα ↔ Pi, 8N1")
    lit("A6", "BRIDGE_FRAME_FORMAT", "", "κάθε data frame γίνεται μία γραμμή JSON με hex")
    lit("A6", "BRIDGE_UART_WRITE", "", "println → +CRLF· μπλοκάρει όσο γεμίζει το FIFO")
    lit("A6", "BRIDGE_USB_ECHO", "", "δεύτερη εγγραφή ανά frame στο USB-CDC (debug)")
    c.add("A6", "BRIDGE_ACK_BROADCASTS", 1, "tx", f"{firmware_params.BRIDGE}:105",
          "ένα broadcast ανά ACK, χωρίς retry, η γέφυρα δεν κάνει re-flood")
    c.add("A6", "BRIDGE_FRAME_QUEUE", 0, "frames", f"{firmware_params.BRIDGE}:121",
          "καμία ουρά εφαρμογής: η εγγραφή γίνεται μέσα στο ESP-NOW RX callback")
    py("A6", "BAUD", "baud", "Pi πλευρά")
    py("A6", "HEARTBEAT_INTERVAL_S", "s", "")
    fwd("A6", "MESH_OFFLINE_AFTER", "×", "")
    fwd("A6", "MESH_EXPECTED_REPORT_INTERVAL_MS", "ms", "")
    py("A6", "_LIFEPO4_CURVE", "mV → %", "SoC πίνακας (piecewise linear)")
    c.add("A6", "PI_PROCESS_MS", 20.0, "ms", "pi/scripts/serial_bridge.py:303",
          "reload nodes.json + AES-GCM + ≤6 MQTT publish ανά frame — ΜΗ μετρημένο, εύρος 5–300",
          "model")

    # ── A7 CART ──────────────────────────────────────────────────────────────
    # Implemented in firmware (CART depth N, 2026-09-28): read from mesh_config.h.
    for key, unit, note in (
        ("MESH_CART_ENABLE", "", "0 = Phase 1 (rollback)"),
        ("MESH_CART_SLOT_MS", "ms", "παράθυρο λήψης ανά κόμβο (σκάλα)"),
        ("MESH_RELAY_BUFFER_SIZE", "frames", "relay buffer σε RTC"),
        ("MESH_DRIFT_BIAS_PPM", "ppm", "Gate 0 run 1"),
        ("MESH_DRIFT_STEP_PPM_300S", "ppm", "Gate 0: προσωρινό"),
        ("MESH_GUARD_CAP_MS", "ms", "ανώτατο G_max"),
    ):
        if key in D:
            fwd("A7", key, unit, note)
    # Designed in the CART plan; where the firmware now defines the same name,
    # the firmware value wins.
    PL = fw["planned"]
    for key, unit, note in (
        ("MESH_SLEEPY_RELAY_DEPTH_MAX", "hops", "0 = Phase 1"),
        ("MESH_MAX_SLEEPY_CHILDREN", "", "admission control μέσω RELAY_CAP"),
        ("MESH_RX_BEACON_PERIOD_MS", "ms", "RX_OPEN beacons μέσα στο παράθυρο"),
        ("MESH_WAKE_GUARD_MIN_MS", "ms", "radio/boot jitter floor"),
        ("MESH_CART_JITTER_MS", "ms", "J"),
        ("MESH_CART_ATTEMPTS", "", ""),
        ("MESH_CART_PER_CHILD_MS", "ms", ""),
        ("MESH_KNOCK_WINDOW_MS", "ms", ""),
        ("MESH_RELAY_ACK_LINGER_MS", "ms", ""),
        ("MESH_SCHED_HIST", "catches", "margin policy window"),
    ):
        if key in D:
            fwd("A7", key, unit, note)
        else:
            c.add("A7", key, PL[key]["value"], unit, PL[key]["src"], note, "planned")
    kn, kd = D["MESH_GUARD_K_NUM"], D["MESH_GUARD_K_DEN"]
    c.add("A7", "GUARD_MARGIN_K", kn["value"] / kd["value"], "", kn["src"],
          f"k = kNum/kDen = {kn['value']}/{kd['value']}· G = clamp(2·k·max|err| + pad, G_min, G_max)", "firmware")
    c.add("A7", "GUARD_PAD_MS", D["MESH_GUARD_PAD_MS"]["value"], "ms", D["MESH_GUARD_PAD_MS"]["src"], "", "firmware")
    f10 = L["G_MAX_FACTOR_X10"]
    c.add("A7", "G_MAX_FACTOR", f10["value"] / 10, "", f10["src"], "G_max ≥ 2·|b|·T·1,3", "firmware")
    lit("A7", "G_MAX_WANDER_Z", "σ", "G_max ≥ z·σ_wander, σ = T·step/√(1−0,98²)")
    c.add("A7", "DRIFT_BIAS_PLANNING", 0.006, "", f"{CARTV2} §2", "0,6 % σχετικό bias", "repo-doc")
    c.add("A7", "DRIFT_BIAS_MEASURED", 0.0017, "", f"{CARTV2} §5 (Gate 0 run 1)",
          "χειρότερο ζεύγος, robust stats — run 2 εκκρεμεί", "measured")
    c.add("A7", "DRIFT_STEP_PER_300S", 0.0001, "", "docs/analysis/cart_sim.py:32",
          "ανά κύκλο, κλιμακώνεται ∝ T", "repo-doc")

    # ── B hardware ───────────────────────────────────────────────────────────
    c.add("B", "I_TX_MA", 335.0, "mA", f"{DS}, Table 5-7",
          "802.11b 1 Mbps @21 dBm· το firmware κόβει στα 20 dBm → άνω φράγμα", "datasheet")
    c.add("B", "I_RX_MA", 84.0, "mA", f"{DS}, Table 5-7", "802.11b/g/n HT20 RX", "datasheet")
    c.add("B", "I_CPU_RUN_MA", 23.0, "mA", f"{DS}, Table 5-8", "modem-sleep 160 MHz, periph off, CPU run", "datasheet")
    c.add("B", "I_CPU_IDLE_MA", 16.0, "mA", f"{DS}, Table 5-8", "modem-sleep 160 MHz, periph off, CPU idle", "datasheet")
    c.add("B", "I_LIGHT_SLEEP_UA", 130.0, "µA", f"{DS}, Table 5-9", "", "datasheet")
    c.add("B", "I_DEEP_SLEEP_CHIP_UA", 5.0, "µA", f"{DS}, Table 5-9", "RTC timer + RTC memory, μόνο το chip", "datasheet")
    c.add("B", "RX_SENSITIVITY_1M_DBM", -98.4, "dBm", f"{DS}, Table 6-4", "802.11b 1 Mbps", "datasheet")
    c.add("B", "TX_POWER_MAX_DBM", 21.0, "dBm", f"{DS}, Table 6-2", "802.11b", "datasheet")
    c.add("B", "RTC_FAST_MEM_B", 8192, "B", f"{DS}, Memory", "διατηρείται στον deep sleep", "datasheet")
    c.add("B", "SRAM_KB", 400, "KB", f"{DS}, Memory", "16 KB ως cache", "datasheet")
    c.add("B", "WAKE_LATENCY_S", (0.140, 0.230), "s", "docs/technical/02-esp-now-protocol.md:113",
          "deep sleep → app_main, πριν το radio init", "repo-doc")
    tc("B", "CONFIG_ESP_PHY_MAX_WIFI_TX_POWER", "dBm", "πραγματικό όριο TX του build", 20)
    tc("B", "CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ", "MHz", "", 160)
    tc("B", "CONFIG_RTC_CLK_SRC_INT_RC", "", "RTC slow clock = εσωτερικό RC ~136 kHz (drift)", True)
    tc("B", "CONFIG_BOOTLOADER_RESERVE_RTC_SIZE", "B", "δεσμευμένα στη RTC από τον bootloader", 16)
    tc("B", "CONFIG_ESP_WIFI_DYNAMIC_TX_BUFFER_NUM", "buffers", "όριο για back-to-back esp_now_send", 32)
    tc("B", "CONFIG_ESP_WIFI_STATIC_RX_BUFFER_NUM", "buffers", "ουρά εισόδου radio", 8)
    tc("B", "CONFIG_ESP_WIFI_DYNAMIC_RX_BUFFER_NUM", "buffers", "ουρά εισόδου radio", 32)
    tc("B", "CONFIG_ESP_WIFI_MGMT_SBUF_NUM", "buffers", "", 32)
    tc("B", "HWSERIAL_TX_BUFFER_DEFAULT", "B", "0 = χωρίς ring buffer, μόνο HW FIFO → println μπλοκάρει", 0)
    c.add("B", "UART_HW_FIFO_B", 128, "B", "SOC_UART_FIFO_LEN (ESP32-C3)", "", "datasheet")

    # ── C board / energy ─────────────────────────────────────────────────────
    c.add("C", "I_SLEEP_BOARD_UA", 62.5, "µA", f"{SNP}:137", "55 µA (RTC + LDO) + 7,5 µA divider", "repo-doc")
    c.add("C", "I_DIVIDER_UA", 7.5, "µA", f"{SNP}:46", "2 × 220 kΩ, πάντα ON", "repo-doc")
    c.add("C", "I_ACTIVE_LUMPED_MA", 86.5, "mA", f"{SNP}:136", "όλο το awake ενιαία (μοντέλο repo)", "repo-doc")
    c.add("C", "I_ALWAYS_ON_MA", 100.0, "mA", f"{SNP}:183", "", "repo-doc")
    c.add("C", "I_DHT22_MA", 1.5, "mA", f"{SNP}:44", "μόνο στο warmup (GPIO5 HIGH)", "repo-doc")
    c.add("C", "I_SOIL_MA", 5.0, "mA", f"{SNP}:45", "μόνο στο warmup (GPIO4 HIGH)", "repo-doc")
    c.add("C", "AWAKE_PHASE1_S", 2.5, "s", f"{SNP}:138", "2 s warmup ∥ radio + ~0,3 s read/send/confirm", "repo-doc")
    c.add("C", "BATTERY_MAH", 1500, "mAh", f"{SNP}:47", "LiFePO4 18650, 3,2 V, κατευθείαν στο 3V3", "repo-doc")
    c.add("C", "BATTERY_DOD", 0.8, "", f"{SNP}:167", "", "repo-doc")
    c.add("C", "SOLAR_WINTER_MAH_DAY", 437, "mAh/day", f"{SNP}:231", "6 V/2 W, 2 PSH, 50 % derate, TP5000 70 %", "repo-doc")
    c.add("C", "ENERGY_MODEL", "lumped | per-state", "", "meshsim", "lumped = repo· per-state = datasheet ανά κατάσταση", "model")
    c.add("C", "PER_STATE_CURRENTS",
          "BOOT 23 mA · LISTEN/RX 84 mA · TX 335 mA (μόνο airtime) · CPU 23 mA · +6,5 mA αισθητήρες στο warmup · SLEEP 62,5 µA",
          "", f"{DS} 5-7/5-8 + {SNP}", "", "model")

    # ── D PHY / MAC ──────────────────────────────────────────────────────────
    c.add("D", "PHY_RATE_MBPS", 1, "Mbps", ESPNOW_DOC, "DSSS DBPSK", "standard")
    c.add("D", "PLCP_LONG_US", an.PLCP_LONG_US, "µs", IEEE, "long preamble 144 + header 48· υποχρεωτικό στο 1 Mbps", "standard")
    c.add("D", "ESPNOW_OVERHEAD_B", an.ESPNOW_OVERHEAD_BYTES, "B", ESPNOW_DOC,
          " + ".join(f"{k} {v}" for k, v in an.ESPNOW_OVERHEAD.items()), "standard")
    c.add("D", "ACK_FRAME_B", an.ACK_FRAME_BYTES, "B", IEEE, "802.11 ACK control frame", "standard")
    c.add("D", "SIFS_US", an.SIFS_US, "µs", IEEE, "", "standard")
    c.add("D", "SLOT_US", an.SLOT_US, "µs", IEEE, "εναλλακτικό preset ERP: 9 µs", "standard")
    c.add("D", "DIFS_US", an.DIFS_US, "µs", IEEE, "SIFS + 2·slot", "standard")
    c.add("D", "CW_MIN", an.CW_MIN, "slots", IEEE, "εναλλακτικό preset ERP: 15 (cart_sim: 16)", "standard")
    c.add("D", "CW_MAX", an.CW_MAX, "slots", IEEE, "", "standard")
    c.add("D", "MAC_RETRY_LIMIT", 5, "retx", "αναφορές κοινότητας (esp-idf #9383, Instructables)",
          "η Espressif δεν το τεκμηριώνει· sweep {0,3,5,7}", "unverified")
    c.add("D", "MAX_PAYLOAD_B", 250, "B", ESPNOW_DOC, "ESP-NOW v1", "standard")

    # ── E radio model ────────────────────────────────────────────────────────
    c.add("E", "FREQ_HZ", 2.412e9, "Hz", "κανάλι 1", "", "standard")
    c.add("E", "PL_1M_DB", round(an.free_space_pl_db(1.0), 2), "dB", "Friis, d0 = 1 m", "", "derived")
    c.add("E", "PATHLOSS_EXPONENT", 2.5, "", "meshsim", "θερμοκήπιο: 2–3", "model")
    c.add("E", "SHADOWING_SIGMA_DB", 4.0, "dB", "meshsim", "log-normal", "model")
    c.add("E", "SENS_FER", 0.08, "", IEEE, "ορισμός sensitivity: FER 8 %, PSDU 1024 B", "standard")
    c.add("E", "SENS_PSDU_B", 1024, "B", IEEE, "", "standard")
    c.add("E", "CCA_THRESHOLD_DBM", -82.0, "dBm", "meshsim", "carrier sense· hidden terminals από την τοπολογία", "model")
    c.add("E", "PER_MODEL", "SINR → BER(DBPSK) → PER(L)", "", "meshsim",
          "η παρεμβολή μετράει ως θόρυβος: οι συγκρούσεις προκύπτουν χωρίς αυθαίρετο capture threshold", "model")

    # ── F scenario ───────────────────────────────────────────────────────────
    for key, val, unit, note in (
        ("RANKS", 50, "ranks", ""),
        ("NODES_PER_RANK", 10, "κόμβοι", "σύνολο = RANKS × NODES_PER_RANK"),
        ("TOPOLOGY", "layered", "", "layered (γειτονία rank k±1) | geometric 2D"),
        ("T_S", 900, "s", "presets: 900 (15′), 1800 (30′)· οποιαδήποτε τιμή > 30 s"),
        ("TECHNIQUE", "T2-flood", "", "T1-ladder | T1-per-cycle | T2-flood | T2-unicast"),
        ("SLEEP_MODE", "allsleepy", "",
         "allsleepy = το deployment: ΟΛΟΙ οι κόμβοι μπαταρία, ύπνος, relay, συγχρονισμένη αφύπνιση· "
         "phase1 μόνο ως baseline του σημερινού firmware, ποτέ ως πρόταση"),
        ("ACK_RELAY_GATE", "cart", "",
         "cart: !sleepy || rxOpen (απαραίτητο στο allsleepy)· firmware: !sleepy (baseline — μπλοκάρει κάθε re-flood)"),
        ("MAX_TTL_OVERRIDE", None, "hops", "None = firmware (16)· what-if π.χ. 255"),
        ("RELAY_BUFFER", "derived (§H)", "frames", "T1 store-and-forward"),
        ("RELAY_OVERFLOW", "backpressure", "", "drop-oldest | drop-new | backpressure (δεν γίνεται hop-ACK)"),
        ("CYCLES", 4, "κύκλοι", "steady-state + extrapolation σε mAh/day"),
        ("SEEDS", 5, "", "95 % CI"),
        ("NODE_MTBF_H", None, "h", "None = χωρίς αποτυχίες κόμβων"),
    ):
        c.add("F", key, val, unit, "σενάριο", note, "scenario")

    # ── G derived ────────────────────────────────────────────────────────────
    pkt, beacon, ack, join, prov = (c["MESH_PACKET_LEN"], c["sizeof(MeshBeacon)"],
                                    c["sizeof(MeshAck)"], c["sizeof(MeshJoinBeacon)"],
                                    c["MESH_PROVISION_LEN"])
    for label, length in (("join", join), ("ack", ack), ("beacon", beacon),
                          ("beacon_v3", c["BEACON_V3_LEN"]), ("provision", prov), ("data", pkt)):
        c.add("G", f"AIRTIME_{label.upper()}_US", an.airtime_us(length), "µs",
              "192 + 8·(43 + L)", f"L = {length} B", "derived")
    c.add("G", "AIRTIME_80211_ACK_US", an.ack_airtime_us(), "µs", "192 + 8·14", "", "derived")
    c.add("G", "UNICAST_DATA_NO_BACKOFF_US", an.unicast_exchange_us(pkt), "µs",
          "DIFS + DATA + SIFS + ACK", "", "derived")
    c.add("G", "UNICAST_DATA_MEAN_US", an.unicast_exchange_us(pkt, an.mean_backoff_slots()), "µs",
          "+ μέσο backoff CW/2·slot", "", "derived")
    c.add("G", "CART_SPEC_AIRTIME_ERROR",
          "beacon 0,63 → 0,752 ms· data+ACK 1,2 → 1,338 ms", "", CARTV2 + " §2",
          "το spec παρέλειψε 15 B vendor action header", "derived")

    baud = c["UART_BAUD"]
    line_b = an.bridge_frame_line_bytes(c["BRIDGE_FRAME_FORMAT"], pkt, c["BRIDGE_UART_WRITE"] == "println")
    line_s = line_b * an.uart_byte_time_s(baud)
    c.add("G", "UART_FRAME_LINE_B", line_b, "B", "format + 2·61 hex + CRLF", "", "derived")
    c.add("G", "UART_FRAME_LINE_MS", round(line_s * 1e3, 3), "ms", "10 bit/byte @ UART_BAUD", "", "derived")
    c.add("G", "BRIDGE_MAX_FRAMES_S", round(1 / line_s, 2), "frames/s", "1 / line time",
          "ανώτατος ρυθμός γέφυρας (χωρίς USB echo)", "derived")
    ack_lo = an.pi_ack_line_bytes(0, ttl=1)
    ack_hi = an.pi_ack_line_bytes(65535, ok=False, ttl=16)
    c.add("G", "UART_ACK_LINE_B", (ack_lo, ack_hi), "B", "compact json.dumps + \\n", "εύρος seq/ttl/ok", "derived")
    c.add("G", "BRIDGE_INGRESS_QUEUE", c["CONFIG_ESP_WIFI_STATIC_RX_BUFFER_NUM"] + c["CONFIG_ESP_WIFI_DYNAMIC_RX_BUFFER_NUM"],
          "frames", "static + dynamic RX buffers", "όσο το println μπλοκάρει, τα frames περιμένουν εδώ", "derived")

    ttl_args = (c["MESH_TTL_MARGIN"], c["MESH_MAX_TTL"], c["ACK_TTL_MARGIN"], c["ACK_TTL_MAX"], c["MESH_MAX_TTL"])
    ceiling = an.depth_ceiling(*ttl_args)
    c.add("G", "DEPTH_CEILING_RANK", ceiling, "rank", "TTL κανόνες firmware + Pi",
          "βαθύτεροι κόμβοι δεν παραδίδουν / δεν παίρνουν ACK", "derived")
    R, W = c["RANKS"], c["NODES_PER_RANK"]
    undelivered = sum(W for r in range(1, R + 1) if r > ceiling)
    c.add("G", "UNDELIVERED_NODES_SCENARIO", undelivered, "κόμβοι", f"{R}×{W} με firmware TTL", "", "derived")
    c.add("G", "FLOOD_REBROADCASTS_FW", an.flood_rebroadcasts(R, W, *ttl_args), "broadcasts/κύκλο",
          f"{R}×{W}, όλοι relay, χωρίς απώλειες", "O(N²)", "derived")
    c.add("G", "FLOOD_REBROADCASTS_NO_TTL", an.flood_rebroadcasts(R, W, c["MESH_TTL_MARGIN"], 255,
                                                                   c["ACK_TTL_MARGIN"], 255, 255),
          "broadcasts/κύκλο", f"{R}×{W}, what-if MAX_TTL=255", "", "derived")
    c.add("G", "RANK1_SUBTREE", R, "κόμβοι", "N / W σε ισορροπημένο layered δέντρο",
          "frames/κύκλο που περνά κάθε rank-1 relay", "derived")

    ber_s = an.ber_at_sensitivity(c["SENS_FER"], c["SENS_PSDU_B"])
    c.add("G", "BER_AT_SENSITIVITY", float(f"{ber_s:.4g}"), "", "1 − (1−FER)^(1/8192)", "", "derived")
    c.add("G", "EBN0_AT_SENSITIVITY_DB", round(an.dbpsk_ebn0_db_for_ber(ber_s), 3), "dB",
          "DBPSK: ln(1/(2·BER))", "", "derived")
    c.add("G", "PER_DATA_AT_SENSITIVITY", round(an.frame_error_rate(c["RX_SENSITIVITY_1M_DBM"], pkt), 5), "",
          "61 B frame στο −98,4 dBm", "", "derived")
    c.add("G", "LINK_BUDGET_DB", c["CONFIG_ESP_PHY_MAX_WIFI_TX_POWER"] - c["RX_SENSITIVITY_1M_DBM"], "dB",
          "TX 20 dBm − sensitivity", "", "derived")

    for T in (300, 900, 1800):
        c.add("G", f"MAH_DAY_PHASE1_LEAF_T{T}",
              round(an.mah_per_day(T, c["AWAKE_PHASE1_S"], c["I_ACTIVE_LUMPED_MA"], c["I_SLEEP_BOARD_UA"] / 1000), 2),
              "mAh/day", "lumped model", f"έλεγχος: {RES}" if T != 1800 else "", "derived")
    for T in (900, 1800):
        for label, b in (("MEASURED", c["DRIFT_BIAS_MEASURED"]), ("PLANNING", c["DRIFT_BIAS_PLANNING"])):
            c.add("G", f"G_MAX_T{T}_{label}", round(an.g_max_s(b, T, c["G_MAX_FACTOR"]), 2), "s",
                  "2·|b|·T·1,3", f"b = {b * 100:.2f} %", "derived")
    c.add("G", "ALWAYS_ON_MAH_DAY", c["I_ALWAYS_ON_MA"] * 24, "mAh/day", "100 mA × 24 h", "", "derived")

    # ── H relay-buffer RTC budget ────────────────────────────────────────────
    rtc_used, relay_bytes = 0, 0
    for v in L["RTC_VARS"]["value"]:
        size = firmware_params.TYPE_SIZE.get(v["type"]) or S[v["type"]]["size"]
        for d in v.get("dims", []):
            size *= firmware_params.safe_eval(d, {k: x["value"] for k, x in D.items()})
        rtc_used += size
        if v["name"] == "meshRelayBuf":
            relay_bytes = size
        c.add("H", f"RTC:{v['name']}", size, "B", v["src"], v["type"], "firmware")
    c.add("H", "RTC_OURS_B", rtc_used, "B", "Σ RTC_DATA_ATTR", "ό,τι δηλώνει ο δικός μας κώδικας", "derived")
    c.add("H", "RTC_MEASURED_TOTAL_B", 3848, "B",
          "riscv32-esp-elf-size -A edge_node_esp32_c3.ino.elf (core 3.3.11, 2026-09-28)",
          ".rtc.text 20 + .rtc.data 3756 + .rtc.force_slow 32 + .rtc_reserved 40", "measured")
    c.add("H", "RTC_IDF_MEASURED_B", 92, "B", "ίδια μέτρηση",
          "ό,τι παίρνουν ESP-IDF/Arduino/bootloader (όλα τα RTC sections εκτός .rtc.data)", "measured")
    free_upper = c["RTC_FAST_MEM_B"] - c["RTC_IDF_MEASURED_B"] - rtc_used
    c.add("H", "RTC_FREE_UPPER_BOUND_B", free_upper, "B", "8192 − ESP-IDF (μετρημένο) − δικά μας",
          "ελεύθερη μνήμη ύπνου με τον τρέχοντα relay buffer", "derived")
    c.add("H", "RELAY_BUFFER_MAX_UPPER_BOUND", (free_upper + relay_bytes) // pkt, "frames",
          f"⌊(ελεύθερη + σημερινός relay buffer) / {pkt}⌋", "πόσα frames θα χωρούσε ο relay buffer το πολύ",
          "derived")
    c.add("H", "RELAY_BUFFER_MIN_REQUIRED", c["RANK1_SUBTREE"], "frames", "RANK1_SUBTREE",
          "T1 per-cycle: ένα rank-1 relay πρέπει να χωρέσει όλο το subtree του", "derived")
    c.add("H", "RELAY_FLUSH_TIME_S_AT_MIN", round(c["RANK1_SUBTREE"] * c["UNICAST_DATA_MEAN_US"] / 1e6, 4),
          "s", "B × UNICAST_DATA_MEAN", f"έναντι MESH_WAKE_MAX_AWAKE_MS = {c['MESH_WAKE_MAX_AWAKE_MS']} ms", "derived")
    c.add("H", "BACK_TO_BACK_TX_LIMIT", c["CONFIG_ESP_WIFI_DYNAMIC_TX_BUFFER_NUM"], "frames",
          "dynamic TX buffers", "flush > 32 χωρίς αναμονή callback → ESP_ERR_ESPNOW_NO_MEM", "derived")
    return c


def to_markdown(c):
    out = []
    P = out.append
    P("# Κατάλογος παραμέτρων — GreenHouse mesh simulator\n")
    P("> **Αυτό το αρχείο παράγεται αυτόματα** από `sim/meshsim/params.py`. Μην το διορθώνεις με το χέρι:")
    P("> `python -m meshsim params --md docs/simulator/PARAMETERS.md` (από τον φάκελο `sim/`).")
    P("> Οι τιμές firmware/toolchain διαβάζονται από τον κώδικα με parser, άρα αλλάζουν μόνες τους όταν αλλάξει το firmware.\n")
    P("Spec: `docs/superpowers/specs/2026-09-28-mesh-simulator-design.md`\n")
    P("## Τύποι πηγής\n")
    P("| kind | σημασία |\n|---|---|")
    for k, v in KINDS.items():
        P(f"| `{k}` | {v} |")
    P("")
    P("## Βασικά ευρήματα (από τις παράγωγες τιμές)\n")
    P(f"- **Ταβάνι βάθους: rank {c['DEPTH_CEILING_RANK']}.** Με τους TTL κανόνες του firmware, στο σενάριο "
      f"{c['RANKS']}×{c['NODES_PER_RANK']} **{c['UNDELIVERED_NODES_SCENARIO']} κόμβοι δεν παραδίδουν ποτέ**.")
    P(f"- **Flood ACK: {c['FLOOD_REBROADCASTS_FW']:,} re-broadcasts/κύκλο** (firmware TTL)· "
      f"{c['FLOOD_REBROADCASTS_NO_TTL']:,} χωρίς όριο TTL — κλιμάκωση O(N²).".replace(",", "."))
    P(f"- **Γέφυρα:** γραμμή UART {c['UART_FRAME_LINE_B']} B = {c['UART_FRAME_LINE_MS']} ms → "
      f"**μέγιστο {c['BRIDGE_MAX_FRAMES_S']} frames/s**· ουρά εισόδου μόνο {c['BRIDGE_INGRESS_QUEUE']} frames.")
    P(f"- **Airtime (1 Mbps):** data {c['AIRTIME_DATA_US']:.0f} µs, beacon {c['AIRTIME_BEACON_US']:.0f} µs, "
      f"ACK {c['AIRTIME_ACK_US']:.0f} µs· unicast με L2 ACK {c['UNICAST_DATA_NO_BACKOFF_US']:.0f} µs "
      f"(+backoff → {c['UNICAST_DATA_MEAN_US']:.0f} µs).")
    P(f"- **Relay buffer:** firmware {c['RELAY_BUFFER_TODAY']} frames· χωράνε έως "
      f"{c['RELAY_BUFFER_MAX_UPPER_BOUND']} (μνήμη ύπνου μετρημένη στο ELF: {c['RTC_MEASURED_TOTAL_B']}/8192 B), "
      f"χρειάζονται τουλάχιστον {c['RELAY_BUFFER_MIN_REQUIRED']} (subtree rank-1 στο 50×10).")
    P(f"- **Ενέργεια Phase-1 leaf:** {c['MAH_DAY_PHASE1_LEAF_T900']} mAh/day @15′, "
      f"{c['MAH_DAY_PHASE1_LEAF_T1800']} mAh/day @30′.\n")
    titles = dict(GROUPS)
    for gid, title in GROUPS:
        rows = c.group(gid)
        if not rows:
            continue
        P(f"## {gid}. {titles[gid]}\n")
        P("| Παράμετρος | Τιμή | Τι είναι | Γιατί αυτή η τιμή | Πηγή | kind | Τεχνική σημείωση |")
        P("|---|---|---|---|---|---|---|")
        for p in rows:
            what, why = explain_param(p.key) or ("", "")
            P(f"| `{p.key}` | {_fmt(p.value)} {p.unit} | {what} | {why} | {_code(p.source)} | {p.kind} | {p.note} |")
        P("")
    _hardware_md(P)
    _config_md(P, c)
    return "\n".join(out)


def _hardware_md(P):
    from . import hardware
    P("## I. Μητρώο εξαρτημάτων (επιλογές hardware για το calculator)\n")
    titles = {"boards": "Πλακέτα / sleep floor", "dividers": "Divider μπαταρίας", "rtc_clocks": "Ρολόι RTC",
              "climate_sensors": "Αισθητήρας αέρα", "soil_sensors": "Αισθητήρας εδάφους",
              "warmup_modes": "MCU στο warm-up", "batteries": "Μπαταρία", "solar_panels": "Ηλιακό πάνελ",
              "sun": "Ηλιοφάνεια", "pi": "Pi", "bridge_board": "Γέφυρα", "offgrid": "Off-grid gateway"}
    for group, entries in hardware.library().items():
        P(f"### {titles.get(group, group)} (`{group}`)\n")
        P("| επιλογή | τιμές | πηγή | kind | σημείωση |\n|---|---|---|---|---|")
        for name, e in entries.items():
            vals = ", ".join(f"{k}={_fmt(v)}" for k, v in e.items() if k not in ("source", "kind", "note"))
            P(f"| `{name}` | {vals} | {_code(e.get('source', ''))} | {e.get('kind', '')} | {e.get('note', '')} |")
        P("")


def _config_md(P, c):
    from . import config
    P("## J. Μεταβλητές run (`--set key=value`)\n")
    P("Κάθε run: defaults ← preset ← `--set`. Όλα καταγράφονται στο `sim/runs/<run>/config.json`.\n")
    P("| key | default | Τι είναι | Γιατί αυτή η default | επιλογές |\n|---|---|---|---|---|")
    for row in config.describe(c):
        ch = " \\| ".join(map(str, row["choices"])) if row["choices"] else ""
        what, why = CONFIG_EXPLAIN.get(row["key"], (row["desc"], ""))
        P(f"| `{row['key']}` | {_fmt(row['default'])} | {what} | {why} | {ch} |")
    P("")
    P("### Presets\n")
    for name, p in config.PRESETS.items():
        sets = ", ".join(f"`{k}={v}`" for k, v in p.items() if not k.startswith("_"))
        P(f"- **`{name}`** — {p['_doc']}: {sets}")
    P("")


def _fmt(v):
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, (list, tuple)):
        if v and isinstance(v[0], (list, tuple)):
            return " · ".join(f"{a}→{b:g}" for a, b in v)
        return " – ".join(_fmt(x) for x in v)
    if v is None:
        return "—"
    return str(v).replace("|", "\\|")


def _code(src):
    return f"`{src}`" if ("/" in src or ".h" in src or ".py" in src) else src


def to_json(c):
    return json.dumps(c.to_dict(), indent=1, ensure_ascii=False)
