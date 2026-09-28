# Κατάλογος παραμέτρων — GreenHouse mesh simulator

> **Αυτό το αρχείο παράγεται αυτόματα** από `sim/meshsim/params.py`. Μην το διορθώνεις με το χέρι:
> `python -m meshsim params --md docs/simulator/PARAMETERS.md` (από τον φάκελο `sim/`).
> Οι τιμές firmware/toolchain διαβάζονται από τον κώδικα με parser, άρα αλλάζουν μόνες τους όταν αλλάξει το firmware.

Spec: `docs/superpowers/specs/2026-09-28-mesh-simulator-design.md`

## Τύποι πηγής

| kind | σημασία |
|---|---|
| `firmware` | διαβάζεται από τον κώδικα firmware/Pi (parser) |
| `toolchain` | διαβάζεται από το sdkconfig / core του arduino-esp32 |
| `datasheet` | ESP32-C3 datasheet (Espressif) |
| `standard` | IEEE 802.11 / τεκμηρίωση ESP-IDF |
| `repo-doc` | τεκμηρίωση του repo (εκτίμηση σχεδίασης, όχι μέτρηση) |
| `measured` | μέτρηση στο bench |
| `planned` | σχεδιασμένο (spec/plan), δεν υπάρχει ακόμα στο firmware |
| `model` | υπόθεση μοντέλου του simulator (ρυθμιζόμενη) |
| `unverified` | μη τεκμηριωμένο από την Espressif — ρυθμιζόμενο, με sweep |
| `scenario` | παράμετρος σεναρίου (ορίζεται ανά run) |
| `derived` | υπολογίζεται από τις παραπάνω τιμές |

## Βασικά ευρήματα (από τις παράγωγες τιμές)

- **Ταβάνι βάθους: rank 17.** Με τους TTL κανόνες του firmware, στο σενάριο 50×10 **330 κόμβοι δεν παραδίδουν ποτέ**.
- **Flood ACK: 18.100 re-broadcasts/κύκλο** (firmware TTL)· 137.200 χωρίς όριο TTL — κλιμάκωση O(N²).
- **Γέφυρα:** γραμμή UART 150 B = 13.021 ms → **μέγιστο 76.8 frames/s**· ουρά εισόδου μόνο 40 frames.
- **Airtime (1 Mbps):** data 1024 µs, beacon 752 µs, ACK 696 µs· unicast με L2 ACK 1388 µs (+backoff → 1698 µs).
- **Relay buffer:** άνω φράγμα RTC 123 frames, ελάχιστο απαιτούμενο 50 (subtree rank-1)· τελική τιμή μετά τη μέτρηση RTC του ESP-IDF.
- **Ενέργεια Phase-1 leaf:** 7.26 mAh/day @15′, 4.38 mAh/day @30′.

## A1. Firmware — πακέτα και μηνύματα

| Παράμετρος | Τιμή | Μονάδα | Πηγή | kind | Σημείωση |
|---|---|---|---|---|---|
| `MESH_PACKET_LEN` | 61 | B | `firmware/libraries/GreenhouseMesh/mesh_packet.h:20` | firmware | sealed data frame = header + nettag + ciphertext + apptag |
| `MESH_HEADER_LEN` | 16 | B | `firmware/libraries/GreenhouseMesh/mesh_packet.h:16` | firmware | magic, origin MAC, seq, boot_count, flags, rank, ttl |
| `MESH_NETTAG_LEN` | 8 | B | `firmware/libraries/GreenhouseMesh/mesh_packet.h:17` | firmware | AES-CMAC(NetKey) truncated, ελέγχεται από κάθε relay |
| `MESH_BODY_LEN` | 21 | B | `firmware/libraries/GreenhouseMesh/mesh_packet.h:18` | firmware | AES-GCM ciphertext: T, H, soil, battery_mv, parent MAC, RSSI |
| `MESH_APPTAG_LEN` | 16 | B | `firmware/libraries/GreenhouseMesh/mesh_packet.h:19` | firmware | GCM tag, ανοίγει μόνο το Pi |
| `MESH_AAD_LEN` | 15 | B | `firmware/libraries/GreenhouseMesh/mesh_packet.h:22` | firmware | το ttl (byte 15) εξαιρείται — αλλάζει σε κάθε hop |
| `sizeof(MeshBeacon)` | 27 | B | `firmware/libraries/GreenhouseMesh/mesh_node.h:50` | firmware | broadcast, cleartext + nettag |
| `sizeof(MeshAck)` | 20 | B | `firmware/libraries/GreenhouseMesh/mesh_node.h:91` | firmware | app-level ACK, flood broadcast |
| `sizeof(MeshJoinBeacon)` | 8 | B | `firmware/libraries/GreenhouseMesh/mesh_node.h:66` | firmware | μόνο μη-enrolled κόμβοι |
| `MESH_PROVISION_LEN` | 33 | B | `firmware/libraries/GreenhouseMesh/mesh_crypto.h:14` | firmware | NetKey 16 + flags 1 + tag 16 |
| `BEACON_V3_LEN` | 29 | B | `docs/superpowers/specs/2026-09-23-cart-v2-revision.md §3.1` | planned | CART: + sleepy_depth + guard_hint_q |

## A2. Firmware — routing, beacons, trickle

| Παράμετρος | Τιμή | Μονάδα | Πηγή | kind | Σημείωση |
|---|---|---|---|---|---|
| `MESH_BEACON_INTERVAL_MIN_MS` | 2000 | ms | `firmware/libraries/GreenhouseMesh/mesh_config.h:29` | firmware | trickle floor (reset target) |
| `MESH_BEACON_INTERVAL_MAX_MS` | 60000 | ms | `firmware/libraries/GreenhouseMesh/mesh_config.h:30` | firmware | trickle ceiling· κάθε beacon διπλασιάζει το διάστημα |
| `MESH_BRIDGE_BEACON_INTERVAL_MS` | 2000 | ms | `firmware/libraries/GreenhouseMesh/mesh_config.h:31` | firmware | η γέφυρα δεν κάνει backoff |
| `MESH_PARENT_TIMEOUT_FACTOR` | 3 | × | `firmware/libraries/GreenhouseMesh/mesh_config.h:33` | firmware | parent χάνεται μετά από 3× το advertised interval |
| `TX_FAIL_DROP_COUNT` | 3 | tx | `firmware/libraries/GreenhouseMesh/mesh_node.h:392` | firmware | διαδοχικές αποτυχίες unicast → drop parent |
| `MESH_ORPHAN_FRESH_MS` | 60000 | ms | `firmware/libraries/GreenhouseMesh/mesh_config.h:41` | firmware | UNROUTED beacon από MAC σιωπηλή τόσο → νέο orphan |
| `MESH_ORPHAN_RESET_MIN_GAP_MS` | 10000 | ms | `firmware/libraries/GreenhouseMesh/mesh_config.h:43` | firmware | ≤1 orphan-triggered trickle reset ανά 10 s |
| `MESH_RESCAN_AFTER_MS` | 60000 | ms | `firmware/libraries/GreenhouseMesh/mesh_config.h:38` | firmware | always-on unrouted → επιβεβαίωση καναλιού |
| `MESH_WINDOW_DURATION_MS` | 3000 | ms | `firmware/libraries/GreenhouseMesh/mesh_config.h:35` | firmware | μεταφέρεται στο beacon, αχρησιμοποίητο σήμερα |
| `MESH_RANK_UNROUTED` | 255 |  | `firmware/libraries/GreenhouseMesh/mesh_config.h:13` | firmware | sentinel: χωρίς parent |
| `MESH_NEIGHBOR_SLOTS` | 16 | slots | `firmware/libraries/GreenhouseMesh/mesh_node.h:126` | firmware | LRU πίνακας γειτόνων (orphan detection) |
| `MESH_JOIN_BEACON_INTERVAL_MS` | 3000 | ms | `firmware/libraries/GreenhouseMesh/mesh_node.h:60` | firmware | μόνο μη-enrolled |
| `MESH_FIXED_CHANNEL` | 1 |  | `firmware/libraries/GreenhouseMesh/mesh_config.h:55` | firmware | όλοι οι κόμβοι στο ίδιο κανάλι (2412 MHz) |
| `PARENT_SELECTION` | strict rank < own· μετά μικρότερο rank· μετά RSSI |  | `firmware/libraries/GreenhouseMesh/mesh_node.h:374` | firmware | RPL strict-rank → δομικά χωρίς loops |
| `SLEEPY_PARENT_RULE` | sleepy beacon ποτέ parent (Phase 1) |  | `firmware/libraries/GreenhouseMesh/mesh_node.h:350` | firmware | το CART το αίρει για relay-capable κόμβους |

## A3. Firmware — TTL

| Παράμετρος | Τιμή | Μονάδα | Πηγή | kind | Σημείωση |
|---|---|---|---|---|---|
| `MESH_TTL_MARGIN` | 2 | hops | `firmware/libraries/GreenhouseMesh/mesh_config.h:14` | firmware | data ttl = rank + margin, στο transmit |
| `MESH_MAX_TTL` | 16 | hops | `firmware/libraries/GreenhouseMesh/mesh_config.h:19` | firmware | ανώτατο όριο· relay κάνει drop ttl>max ή ttl==0 |
| `MESH_ACK_TTL` | 6 | hops | `firmware/libraries/GreenhouseMesh/mesh_node.h:77` | firmware | fallback της γέφυρας αν το Pi δεν στείλει ttl |
| `ACK_TTL_MARGIN` | 2 | hops | `pi/scripts/serial_bridge.py:469` | firmware | Pi: ACK ttl = min(ACK_TTL_MAX, rank + margin) |
| `ACK_TTL_MAX` | 16 | hops | `pi/scripts/serial_bridge.py:470` | firmware |  |

## A4. Firmware — buffers και μνήμη

| Παράμετρος | Τιμή | Μονάδα | Πηγή | kind | Σημείωση |
|---|---|---|---|---|---|
| `MESH_DATA_BUFFER_SIZE` | 10 | frames | `firmware/libraries/GreenhouseMesh/mesh_config.h:89` | firmware | δικές του μετρήσεις, ring drop-oldest, σε RTC |
| `MESH_INFLIGHT_MAX` | 11 | frames | `firmware/libraries/GreenhouseMesh/mesh_inflight.h:22` | firmware | frames που περιμένουν app-ACK σε ένα wake |
| `MESH_DEDUP_CACHE_SIZE` | 32 | entries | `firmware/libraries/GreenhouseMesh/mesh_config.h:76` | firmware | (origin, seq) ring σε κάθε relay και στη γέφυρα |
| `MESH_DEDUP_WINDOW_MS` | 30000 | ms | `firmware/libraries/GreenhouseMesh/mesh_config.h:77` | firmware | static_assert: MAX_AWAKE < window < SLEEP_INTERVAL |
| `MESH_ACK_DEDUP_CACHE_SIZE` | 8 | entries | `firmware/libraries/GreenhouseMesh/mesh_node.h:135` | firmware | (target, seq) ring για ACK flood |
| `sizeof(MeshRtcState)` | 632 | B | `firmware/libraries/GreenhouseMesh/mesh_node.h:603` | firmware | επιβιώνει στον deep sleep (RTC FAST) |
| `sizeof(MeshInFlightEntry)` | 64 | B | `firmware/libraries/GreenhouseMesh/mesh_inflight.h:28` | firmware | RAM μόνο |
| `RELAY_BUFFER_TODAY` | 0 | frames | `firmware/libraries/GreenhouseMesh/mesh_node.h:671` | firmware | τα relays κάνουν cut-through, χωρίς buffer — ο buffer B της T1 είναι νέα παράμετρος (§H) |

## A5. Firmware — κύκλος αφύπνισης (sleepy κόμβος)

| Παράμετρος | Τιμή | Μονάδα | Πηγή | kind | Σημείωση |
|---|---|---|---|---|---|
| `MESH_SLEEP_INTERVAL_MS` | 60000 | ms | `firmware/libraries/GreenhouseMesh/mesh_config.h:60` | firmware | τιμή test στο firmware σήμερα· στον sim το T ορίζεται ανά run (§F) |
| `SENSOR_WARMUP_MS` | 2000 | ms | `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino:39` | firmware | αισθητήρες ON· επικαλύπτεται με το radio bring-up |
| `MESH_TX_CONFIRM_WAIT_MS` | 500 | ms | `firmware/libraries/GreenhouseMesh/mesh_config.h:63` | firmware | αναμονή send-callback (L2 ACK) |
| `MESH_APP_ACK_WAIT_MS` | 2000 | ms | `firmware/libraries/GreenhouseMesh/mesh_config.h:65` | firmware | αναμονή ACK από το Pi· αναπάντητα → επόμενο wake |
| `MESH_WAKE_DISCOVERY_MS` | 5000 | ms | `firmware/libraries/GreenhouseMesh/mesh_config.h:61` | firmware | αναζήτηση νέου parent μετά από αποτυχία |
| `MESH_WAKE_MAX_AWAKE_MS` | 10000 | ms | `firmware/libraries/GreenhouseMesh/mesh_config.h:70` | firmware | σκληρό όριο αφύπνισης |
| `MESH_MIN_SLEEP_MS` | 1000 | ms | `firmware/libraries/GreenhouseMesh/mesh_config.h:72` | firmware | ελάχιστος ύπνος |
| `BATT_ADC_SAMPLES` | 8 | δείγματα | `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino:98` | firmware |  |
| `BATT_ADC_SAMPLE_DELAY_MS` | 2 | ms | `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino:100` | firmware |  |
| `COLD_BOOT_USB_WAIT_MS` | 1500 | ms | `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino:248` | firmware | μόνο σε cold boot, όχι σε timer wake |
| `UNCONFIRMED_WAKES_RESCAN` | 2 | wakes | `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino:145` | firmware | link counter → rescan καναλιού |
| `SEND_INTERVAL_MS` | 5000 | ms | `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino:38` | firmware | always-on: περίοδος ≈ SEND_INTERVAL + WARMUP |

## A6. Γέφυρα (bridge) και Pi

| Παράμετρος | Τιμή | Μονάδα | Πηγή | kind | Σημείωση |
|---|---|---|---|---|---|
| `UART_BAUD` | 115200 | baud | `firmware/bridge_esp32/bridge_esp32.ino:15` | firmware | γέφυρα ↔ Pi, 8N1 |
| `BRIDGE_FRAME_FORMAT` | {"type":"frame","data":"%s"} |  | `firmware/bridge_esp32/bridge_esp32.ino:153` | firmware | κάθε data frame γίνεται μία γραμμή JSON με hex |
| `BRIDGE_UART_WRITE` | println |  | `firmware/bridge_esp32/bridge_esp32.ino:37` | firmware | println → +CRLF· μπλοκάρει όσο γεμίζει το FIFO |
| `BRIDGE_USB_ECHO` | Serial.printf |  | `firmware/bridge_esp32/bridge_esp32.ino:38` | firmware | δεύτερη εγγραφή ανά frame στο USB-CDC (debug) |
| `BRIDGE_ACK_BROADCASTS` | 1 | tx | `firmware/bridge_esp32/bridge_esp32.ino:105` | firmware | ένα broadcast ανά ACK, χωρίς retry, η γέφυρα δεν κάνει re-flood |
| `BRIDGE_FRAME_QUEUE` | 0 | frames | `firmware/bridge_esp32/bridge_esp32.ino:121` | firmware | καμία ουρά εφαρμογής: η εγγραφή γίνεται μέσα στο ESP-NOW RX callback |
| `BAUD` | 115200 | baud | `pi/scripts/serial_bridge.py:43` | firmware | Pi πλευρά |
| `HEARTBEAT_INTERVAL_S` | 2 | s | `pi/scripts/serial_bridge.py:55` | firmware |  |
| `MESH_OFFLINE_AFTER` | 3 | × | `firmware/libraries/GreenhouseMesh/mesh_config.h:103` | firmware |  |
| `MESH_EXPECTED_REPORT_INTERVAL_MS` | 5000 | ms | `firmware/libraries/GreenhouseMesh/mesh_config.h:104` | firmware |  |
| `_LIFEPO4_CURVE` | 3400→100 · 3350→90 · 3320→80 · 3300→70 · 3280→60 · 3260→50 · 3250→40 · 3220→30 · 3200→20 · 3000→10 · 2800→0 | mV → % | `pi/scripts/serial_bridge.py:255` | firmware | SoC πίνακας (piecewise linear) |
| `PI_PROCESS_MS` | 20 | ms | `pi/scripts/serial_bridge.py:303` | model | reload nodes.json + AES-GCM + ≤6 MQTT publish ανά frame — ΜΗ μετρημένο, εύρος 5–300 |

## A7. CART v2 Part C (σχεδιασμένο, όχι υλοποιημένο)

| Παράμετρος | Τιμή | Μονάδα | Πηγή | kind | Σημείωση |
|---|---|---|---|---|---|
| `MESH_SLEEPY_RELAY_DEPTH_MAX` | 1 | hops | `docs/superpowers/plans/2026-09-23-cart-phase2-synced-wake.md:974` | planned | 0 = Phase 1 |
| `MESH_MAX_SLEEPY_CHILDREN` | 6 |  | `docs/superpowers/plans/2026-09-23-cart-phase2-synced-wake.md:977` | planned | admission control μέσω RELAY_CAP |
| `MESH_RX_BEACON_PERIOD_MS` | 100 | ms | `docs/superpowers/plans/2026-09-23-cart-phase2-synced-wake.md:979` | planned | RX_OPEN beacons μέσα στο παράθυρο |
| `MESH_WAKE_GUARD_MIN_MS` | 250 | ms | `docs/superpowers/plans/2026-09-23-cart-phase2-synced-wake.md:981` | planned | radio/boot jitter floor |
| `MESH_CART_JITTER_MS` | 100 | ms | `docs/superpowers/plans/2026-09-23-cart-phase2-synced-wake.md:983` | planned | J |
| `MESH_CART_ATTEMPTS` | 3 |  | `docs/superpowers/plans/2026-09-23-cart-phase2-synced-wake.md:984` | planned |  |
| `MESH_CART_PER_CHILD_MS` | 150 | ms | `docs/superpowers/plans/2026-09-23-cart-phase2-synced-wake.md:985` | planned |  |
| `MESH_KNOCK_WINDOW_MS` | 500 | ms | `docs/superpowers/plans/2026-09-23-cart-phase2-synced-wake.md:986` | planned |  |
| `MESH_RELAY_ACK_LINGER_MS` | 300 | ms | `docs/superpowers/plans/2026-09-23-cart-phase2-synced-wake.md:987` | planned |  |
| `MESH_SCHED_HIST` | 16 | catches | `docs/superpowers/plans/2026-09-23-cart-phase2-synced-wake.md:865` | planned | margin policy window |
| `GUARD_MARGIN_K` | 1.5 |  | `docs/superpowers/specs/2026-09-23-cart-v2-revision.md §3.3` | planned | G = clamp(2·k·max|err| + pad, G_min, G_max) |
| `GUARD_PAD_MS` | 50 | ms | `docs/superpowers/specs/2026-09-23-cart-v2-revision.md §3.3` | planned |  |
| `G_MAX_FACTOR` | 1.3 |  | `docs/superpowers/specs/2026-09-23-cart-v2-revision.md §3.3` | planned | G_max = 2·|b|·T·1.3 |
| `DRIFT_BIAS_PLANNING` | 0.006 |  | `docs/superpowers/specs/2026-09-23-cart-v2-revision.md §2` | repo-doc | 0,6 % σχετικό bias |
| `DRIFT_BIAS_MEASURED` | 0.0017 |  | `docs/superpowers/specs/2026-09-23-cart-v2-revision.md §5 (Gate 0 run 1)` | measured | χειρότερο ζεύγος, robust stats — run 2 εκκρεμεί |
| `DRIFT_STEP_PER_300S` | 0.0001 |  | `docs/analysis/cart_sim.py:32` | repo-doc | ανά κύκλο, κλιμακώνεται ∝ T |

## B. Hardware ESP32-C3 (datasheet) και toolchain (sdkconfig)

| Παράμετρος | Τιμή | Μονάδα | Πηγή | kind | Σημείωση |
|---|---|---|---|---|---|
| `I_TX_MA` | 335 | mA | ESP32-C3 Datasheet (Espressif), Table 5-7 | datasheet | 802.11b 1 Mbps @21 dBm· το firmware κόβει στα 20 dBm → άνω φράγμα |
| `I_RX_MA` | 84 | mA | ESP32-C3 Datasheet (Espressif), Table 5-7 | datasheet | 802.11b/g/n HT20 RX |
| `I_CPU_RUN_MA` | 23 | mA | ESP32-C3 Datasheet (Espressif), Table 5-8 | datasheet | modem-sleep 160 MHz, periph off, CPU run |
| `I_CPU_IDLE_MA` | 16 | mA | ESP32-C3 Datasheet (Espressif), Table 5-8 | datasheet | modem-sleep 160 MHz, periph off, CPU idle |
| `I_LIGHT_SLEEP_UA` | 130 | µA | ESP32-C3 Datasheet (Espressif), Table 5-9 | datasheet |  |
| `I_DEEP_SLEEP_CHIP_UA` | 5 | µA | ESP32-C3 Datasheet (Espressif), Table 5-9 | datasheet | RTC timer + RTC memory, μόνο το chip |
| `RX_SENSITIVITY_1M_DBM` | -98.4 | dBm | ESP32-C3 Datasheet (Espressif), Table 6-4 | datasheet | 802.11b 1 Mbps |
| `TX_POWER_MAX_DBM` | 21 | dBm | ESP32-C3 Datasheet (Espressif), Table 6-2 | datasheet | 802.11b |
| `RTC_FAST_MEM_B` | 8192 | B | ESP32-C3 Datasheet (Espressif), Memory | datasheet | διατηρείται στον deep sleep |
| `SRAM_KB` | 400 | KB | ESP32-C3 Datasheet (Espressif), Memory | datasheet | 16 KB ως cache |
| `WAKE_LATENCY_S` | 0.14 – 0.23 | s | `docs/technical/02-esp-now-protocol.md:113` | repo-doc | deep sleep → app_main, πριν το radio init |
| `CONFIG_ESP_PHY_MAX_WIFI_TX_POWER` | 20 | dBm | `Arduino15/packages/esp32/tools/esp32c3-libs/3.3.11/sdkconfig:1706` | toolchain | πραγματικό όριο TX του build |
| `CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ` | 160 | MHz | `Arduino15/packages/esp32/tools/esp32c3-libs/3.3.11/sdkconfig:1758` | toolchain |  |
| `CONFIG_RTC_CLK_SRC_INT_RC` | True |  | `Arduino15/packages/esp32/tools/esp32c3-libs/3.3.11/sdkconfig:1614` | toolchain | RTC slow clock = εσωτερικό RC ~136 kHz (drift) |
| `CONFIG_BOOTLOADER_RESERVE_RTC_SIZE` | 16 | B | `Arduino15/packages/esp32/tools/esp32c3-libs/3.3.11/sdkconfig:389` | toolchain | δεσμευμένα στη RTC από τον bootloader |
| `CONFIG_ESP_WIFI_DYNAMIC_TX_BUFFER_NUM` | 32 | buffers | `Arduino15/packages/esp32/tools/esp32c3-libs/3.3.11/sdkconfig:1844` | toolchain | όριο για back-to-back esp_now_send |
| `CONFIG_ESP_WIFI_STATIC_RX_BUFFER_NUM` | 8 | buffers | `Arduino15/packages/esp32/tools/esp32c3-libs/3.3.11/sdkconfig:1839` | toolchain | ουρά εισόδου radio |
| `CONFIG_ESP_WIFI_DYNAMIC_RX_BUFFER_NUM` | 32 | buffers | `Arduino15/packages/esp32/tools/esp32c3-libs/3.3.11/sdkconfig:1840` | toolchain | ουρά εισόδου radio |
| `CONFIG_ESP_WIFI_MGMT_SBUF_NUM` | 32 | buffers | `Arduino15/packages/esp32/tools/esp32c3-libs/3.3.11/sdkconfig:1856` | toolchain |  |
| `HWSERIAL_TX_BUFFER_DEFAULT` | 0 | B | `Arduino15/packages/esp32/hardware/esp32/3.3.11/cores/esp32/HardwareSerial.cpp:142` | toolchain | 0 = χωρίς ring buffer, μόνο HW FIFO → println μπλοκάρει |
| `UART_HW_FIFO_B` | 128 | B | SOC_UART_FIFO_LEN (ESP32-C3) | datasheet |  |

## C. Πλακέτα, ενέργεια, μπαταρία, ηλιακό

| Παράμετρος | Τιμή | Μονάδα | Πηγή | kind | Σημείωση |
|---|---|---|---|---|---|
| `I_SLEEP_BOARD_UA` | 62.5 | µA | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:137` | repo-doc | 55 µA (RTC + LDO) + 7,5 µA divider |
| `I_DIVIDER_UA` | 7.5 | µA | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:46` | repo-doc | 2 × 220 kΩ, πάντα ON |
| `I_ACTIVE_LUMPED_MA` | 86.5 | mA | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:136` | repo-doc | όλο το awake ενιαία (μοντέλο repo) |
| `I_ALWAYS_ON_MA` | 100 | mA | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:183` | repo-doc |  |
| `I_DHT22_MA` | 1.5 | mA | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:44` | repo-doc | μόνο στο warmup (GPIO5 HIGH) |
| `I_SOIL_MA` | 5 | mA | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:45` | repo-doc | μόνο στο warmup (GPIO4 HIGH) |
| `AWAKE_PHASE1_S` | 2.5 | s | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:138` | repo-doc | 2 s warmup ∥ radio + ~0,3 s read/send/confirm |
| `BATTERY_MAH` | 1500 | mAh | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:47` | repo-doc | LiFePO4 18650, 3,2 V, κατευθείαν στο 3V3 |
| `BATTERY_DOD` | 0.8 |  | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:167` | repo-doc |  |
| `SOLAR_WINTER_MAH_DAY` | 437 | mAh/day | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:231` | repo-doc | 6 V/2 W, 2 PSH, 50 % derate, TP5000 70 % |
| `ENERGY_MODEL` | lumped \| per-state |  | meshsim | model | lumped = repo· per-state = datasheet ανά κατάσταση |
| `PER_STATE_CURRENTS` | BOOT 23 mA · LISTEN/RX 84 mA · TX 335 mA (μόνο airtime) · CPU 23 mA · +6,5 mA αισθητήρες στο warmup · SLEEP 62,5 µA |  | `ESP32-C3 Datasheet (Espressif) 5-7/5-8 + docs/SENSOR_NODE_POWER_AND_SOLAR.md` | model |  |

## D. PHY/MAC: IEEE 802.11 DSSS + ESP-NOW frame

| Παράμετρος | Τιμή | Μονάδα | Πηγή | kind | Σημείωση |
|---|---|---|---|---|---|
| `PHY_RATE_MBPS` | 1 | Mbps | ESP-IDF ESP-NOW guide (frame format, default rate) | standard | DSSS DBPSK |
| `PLCP_LONG_US` | 192 | µs | IEEE 802.11-2020 Clause 16 (DSSS PHY) | standard | long preamble 144 + header 48· υποχρεωτικό στο 1 Mbps |
| `ESPNOW_OVERHEAD_B` | 43 | B | ESP-IDF ESP-NOW guide (frame format, default rate) | standard | mac_header 24 + category_code 1 + oui 3 + random 4 + vendor_element_header 7 + fcs 4 |
| `ACK_FRAME_B` | 14 | B | IEEE 802.11-2020 Clause 16 (DSSS PHY) | standard | 802.11 ACK control frame |
| `SIFS_US` | 10 | µs | IEEE 802.11-2020 Clause 16 (DSSS PHY) | standard |  |
| `SLOT_US` | 20 | µs | IEEE 802.11-2020 Clause 16 (DSSS PHY) | standard | εναλλακτικό preset ERP: 9 µs |
| `DIFS_US` | 50 | µs | IEEE 802.11-2020 Clause 16 (DSSS PHY) | standard | SIFS + 2·slot |
| `CW_MIN` | 31 | slots | IEEE 802.11-2020 Clause 16 (DSSS PHY) | standard | εναλλακτικό preset ERP: 15 (cart_sim: 16) |
| `CW_MAX` | 1023 | slots | IEEE 802.11-2020 Clause 16 (DSSS PHY) | standard |  |
| `MAC_RETRY_LIMIT` | 5 | retx | αναφορές κοινότητας (esp-idf #9383, Instructables) | unverified | η Espressif δεν το τεκμηριώνει· sweep {0,3,5,7} |
| `MAX_PAYLOAD_B` | 250 | B | ESP-IDF ESP-NOW guide (frame format, default rate) | standard | ESP-NOW v1 |

## E. Μοντέλο ραδιοδιάδοσης και καναλιού

| Παράμετρος | Τιμή | Μονάδα | Πηγή | kind | Σημείωση |
|---|---|---|---|---|---|
| `FREQ_HZ` | 2.412e+09 | Hz | κανάλι 1 | standard |  |
| `PL_1M_DB` | 40.1 | dB | Friis, d0 = 1 m | derived |  |
| `PATHLOSS_EXPONENT` | 2.5 |  | meshsim | model | θερμοκήπιο: 2–3 |
| `SHADOWING_SIGMA_DB` | 4 | dB | meshsim | model | log-normal |
| `SENS_FER` | 0.08 |  | IEEE 802.11-2020 Clause 16 (DSSS PHY) | standard | ορισμός sensitivity: FER 8 %, PSDU 1024 B |
| `SENS_PSDU_B` | 1024 | B | IEEE 802.11-2020 Clause 16 (DSSS PHY) | standard |  |
| `CCA_THRESHOLD_DBM` | -82 | dBm | meshsim | model | carrier sense· hidden terminals από την τοπολογία |
| `PER_MODEL` | SINR → BER(DBPSK) → PER(L) |  | meshsim | model | η παρεμβολή μετράει ως θόρυβος: οι συγκρούσεις προκύπτουν χωρίς αυθαίρετο capture threshold |

## F. Σενάριο (ρυθμίζεται ανά run)

| Παράμετρος | Τιμή | Μονάδα | Πηγή | kind | Σημείωση |
|---|---|---|---|---|---|
| `RANKS` | 50 | ranks | σενάριο | scenario |  |
| `NODES_PER_RANK` | 10 | κόμβοι | σενάριο | scenario | σύνολο = RANKS × NODES_PER_RANK |
| `TOPOLOGY` | layered |  | σενάριο | scenario | layered (γειτονία rank k±1) | geometric 2D |
| `T_S` | 900 | s | σενάριο | scenario | presets: 900 (15′), 1800 (30′)· οποιαδήποτε τιμή > 30 s |
| `TECHNIQUE` | T2-flood |  | σενάριο | scenario | T1-ladder | T1-per-cycle | T2-flood | T2-unicast |
| `SLEEP_MODE` | allsleepy |  | σενάριο | scenario | phase1 | cart1 | allsleepy |
| `ACK_RELAY_GATE` | firmware |  | σενάριο | scenario | firmware: !sleepy | cart: !sleepy || rxOpen |
| `MAX_TTL_OVERRIDE` | — | hops | σενάριο | scenario | None = firmware (16)· what-if π.χ. 255 |
| `RELAY_BUFFER` | derived (§H) | frames | σενάριο | scenario | T1 store-and-forward |
| `RELAY_OVERFLOW` | backpressure |  | σενάριο | scenario | drop-oldest | drop-new | backpressure (δεν γίνεται hop-ACK) |
| `CYCLES` | 4 | κύκλοι | σενάριο | scenario | steady-state + extrapolation σε mAh/day |
| `SEEDS` | 5 |  | σενάριο | scenario | 95 % CI |
| `NODE_MTBF_H` | — | h | σενάριο | scenario | None = χωρίς αποτυχίες κόμβων |

## G. Παράγωγες ποσότητες (υπολογίζονται)

| Παράμετρος | Τιμή | Μονάδα | Πηγή | kind | Σημείωση |
|---|---|---|---|---|---|
| `AIRTIME_JOIN_US` | 600 | µs | 192 + 8·(43 + L) | derived | L = 8 B |
| `AIRTIME_ACK_US` | 696 | µs | 192 + 8·(43 + L) | derived | L = 20 B |
| `AIRTIME_BEACON_US` | 752 | µs | 192 + 8·(43 + L) | derived | L = 27 B |
| `AIRTIME_BEACON_V3_US` | 768 | µs | 192 + 8·(43 + L) | derived | L = 29 B |
| `AIRTIME_PROVISION_US` | 800 | µs | 192 + 8·(43 + L) | derived | L = 33 B |
| `AIRTIME_DATA_US` | 1024 | µs | 192 + 8·(43 + L) | derived | L = 61 B |
| `AIRTIME_80211_ACK_US` | 304 | µs | 192 + 8·14 | derived |  |
| `UNICAST_DATA_NO_BACKOFF_US` | 1388 | µs | DIFS + DATA + SIFS + ACK | derived |  |
| `UNICAST_DATA_MEAN_US` | 1698 | µs | `+ μέσο backoff CW/2·slot` | derived |  |
| `CART_SPEC_AIRTIME_ERROR` | beacon 0,63 → 0,752 ms· data+ACK 1,2 → 1,338 ms |  | `docs/superpowers/specs/2026-09-23-cart-v2-revision.md §2` | derived | το spec παρέλειψε 15 B vendor action header |
| `UART_FRAME_LINE_B` | 150 | B | format + 2·61 hex + CRLF | derived |  |
| `UART_FRAME_LINE_MS` | 13.021 | ms | `10 bit/byte @ UART_BAUD` | derived |  |
| `BRIDGE_MAX_FRAMES_S` | 76.8 | frames/s | `1 / line time` | derived | ανώτατος ρυθμός γέφυρας (χωρίς USB echo) |
| `UART_ACK_LINE_B` | 62 – 68 | B | compact json.dumps + \n | derived | εύρος seq/ttl/ok |
| `BRIDGE_INGRESS_QUEUE` | 40 | frames | static + dynamic RX buffers | derived | όσο το println μπλοκάρει, τα frames περιμένουν εδώ |
| `DEPTH_CEILING_RANK` | 17 | rank | TTL κανόνες firmware + Pi | derived | βαθύτεροι κόμβοι δεν παραδίδουν / δεν παίρνουν ACK |
| `UNDELIVERED_NODES_SCENARIO` | 330 | κόμβοι | 50×10 με firmware TTL | derived |  |
| `FLOOD_REBROADCASTS_FW` | 18100 | broadcasts/κύκλο | 50×10, όλοι relay, χωρίς απώλειες | derived | O(N²) |
| `FLOOD_REBROADCASTS_NO_TTL` | 137200 | broadcasts/κύκλο | 50×10, what-if MAX_TTL=255 | derived |  |
| `RANK1_SUBTREE` | 50 | κόμβοι | `N / W σε ισορροπημένο layered δέντρο` | derived | frames/κύκλο που περνά κάθε rank-1 relay |
| `BER_AT_SENSITIVITY` | 1.018e-05 |  | `1 − (1−FER)^(1/8192)` | derived |  |
| `EBN0_AT_SENSITIVITY_DB` | 10.335 | dB | `DBPSK: ln(1/(2·BER))` | derived |  |
| `PER_DATA_AT_SENSITIVITY` | 0.00843 |  | 61 B frame στο −98,4 dBm | derived |  |
| `LINK_BUDGET_DB` | 118.4 | dB | TX 20 dBm − sensitivity | derived |  |
| `MAH_DAY_PHASE1_LEAF_T300` | 18.79 | mAh/day | lumped model | derived | έλεγχος: docs/analysis/cart_sim_results.txt |
| `MAH_DAY_PHASE1_LEAF_T900` | 7.26 | mAh/day | lumped model | derived | έλεγχος: docs/analysis/cart_sim_results.txt |
| `MAH_DAY_PHASE1_LEAF_T1800` | 4.38 | mAh/day | lumped model | derived |  |
| `G_MAX_T900_MEASURED` | 3.98 | s | 2·|b|·T·1,3 | derived | b = 0.17 % |
| `G_MAX_T900_PLANNING` | 14.04 | s | 2·|b|·T·1,3 | derived | b = 0.60 % |
| `G_MAX_T1800_MEASURED` | 7.96 | s | 2·|b|·T·1,3 | derived | b = 0.17 % |
| `G_MAX_T1800_PLANNING` | 28.08 | s | 2·|b|·T·1,3 | derived | b = 0.60 % |
| `ALWAYS_ON_MAH_DAY` | 2400 | mAh/day | 100 mA × 24 h | derived |  |

## H. Relay buffer: προϋπολογισμός RTC μνήμης

| Παράμετρος | Τιμή | Μονάδα | Πηγή | kind | Σημείωση |
|---|---|---|---|---|---|
| `RTC:meshRtcState` | 632 | B | `firmware/libraries/GreenhouseMesh/mesh_node.h:605` | firmware | MeshRtcState |
| `RTC:g_unconfirmedWakes` | 1 | B | `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino:62` | firmware | uint8_t |
| `RTC_FREE_UPPER_BOUND_B` | 7543 | B | 8192 − bootloader − RTC_DATA_ATTR | derived | πριν αφαιρεθούν τα δεδομένα RTC του ESP-IDF/Arduino (μέτρηση στο βήμα 2) |
| `RELAY_BUFFER_MAX_UPPER_BOUND` | 123 | frames | `⌊free / 61⌋` | derived | άνω φράγμα· η τελική τιμή μετά τη μέτρηση ELF sections |
| `RELAY_BUFFER_MIN_REQUIRED` | 50 | frames | RANK1_SUBTREE | derived | T1 per-cycle: ένα rank-1 relay πρέπει να χωρέσει όλο το subtree του |
| `RELAY_FLUSH_TIME_S_AT_MIN` | 0.0849 | s | B × UNICAST_DATA_MEAN | derived | έναντι MESH_WAKE_MAX_AWAKE_MS = 10000 ms |
| `BACK_TO_BACK_TX_LIMIT` | 32 | frames | dynamic TX buffers | derived | flush > 32 χωρίς αναμονή callback → ESP_ERR_ESPNOW_NO_MEM |

