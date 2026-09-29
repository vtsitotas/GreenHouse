# Mesh Simulator (meshsim) — Design Spec

**Date:** 2026-09-28  
**Status:** Approved (plan mode 2026-09-28). Implementation on branch `feat/mesh-simulator`.  
**Parameter catalogue (generated):** `docs/simulator/PARAMETERS.md`

> **Deployment constraint (user, 2026-09-28): there are NO always-on relays.** Every field
> node is battery-powered, deep-sleeps, relays for others, and wakes on a synchronized
> schedule. Only the Pi and the bridge beside it have mains power. `allsleepy` is the
> design target; `phase1` exists only as a labelled baseline of today's firmware, and
> `cart1` (one sleepy hop) as the stepping stone. Scaling fixes come from scheduling,
> TTL/ACK design, bridge throughput and depth — never from powered relays.

> **Addendum (user, 2026-09-28): simulator + calculator.** Every variable is tweakable per run
> (`sim/meshsim/config.py`, `--set key=value`, presets), every run is logged in full
> (`sim/runs/<run>/` + `index.csv`), and a deterministic calculator
> (`calculator.py`) answers questions like "edge-node autonomy for these parameters" with a
> per-state energy breakdown, feasibility checks and theoretical limits. A hardware component
> library (`hardware.py`: boards, dividers, RTC clock, SHT40/BME280/DHT22, batteries, solar) and an
> improvements table (`improvements.py`) show what each hardware/firmware/gateway change buys.
> The DES engine (below) validates the calculator's stochastic terms. Usage:
> `docs/simulator/README.md`.


## Context

Ζητήθηκε ένας προσομοιωτής για όλο το σύστημα που να τα έχει όλα. Πρέπει να είναι μαθηματικά ορθός και δεμένος στις **ακριβείς** τιμές του firmware
και του hardware (ESP32-C3 SuperMini). Σενάριο-στόχος: **50 ranks × 10 κόμβοι/rank = 500 κόμβοι**.
Θα προσομοιώνει χρόνο, ranks, καταστάσεις κόμβων (sleep/boot/listen/RX/TX), ενέργεια, time-on-air/alive,
πιθανότητες αποτυχίας και σύγκρουσης, και occupancy/utilization των buffers.
Συγκρίνει δύο τεχνικές:
- **T1 hop-by-hop:** κάθε hop επιβεβαιώνεται (app-ACK) πριν προχωρήσει, με store-and-forward στον buffer του parent.
- **T2 end-to-end:** το μήνυμα ανεβαίνει όλα τα hops ως το Pi και μετά τα ACK κατεβαίνουν όλα τα hops,
  σε δύο παραλλαγές: **flood** όπως το σημερινό firmware και **reverse unicast**.
Μοντέλα ύπνου: **Phase 1** (όπως τρέχει σήμερα), **CART v2** (cap 1 sleepy hop), **all-sleepy-N** (ο στόχος deployment),
με διακόπτη. Ο κύκλος T είναι μεταβλητός ανά run, με presets **900 s και 1800 s**.
Μορφή: **Python DES engine** (καθαρή stdlib, ώστε να τρέχει και σε Pyodide) και **interactive web dashboard** (Pyodide + JSON import).
Το υπάρχον `docs/analysis/cart_sim.py` ξαναχρησιμοποιείται: η λογική του guard-window γίνεται port και οι αριθμοί του γίνονται regression test.

Διαδικασία: το έργο είναι αρχιτεκτονικό. Με την έγκριση αυτού του σχεδίου γράφεται πρώτα το spec
(`docs/superpowers/specs/2026-09-28-mesh-simulator-design.md`) και μετά ο κατάλογος παραμέτρων (παραδοτέο #1).

---

## Ευρήματα ήδη από την ανάλυση (ο sim θα τα ποσοτικοποιήσει)

1. **Ταβάνι βάθους = rank 17.** Το data TTL είναι `min(rank+2,16)` (`mesh_node.h:495-499`) και κάθε relay κάνει drop στο ttl==0 (`:692`).
   Για παράδοση χρειάζεται `t0 ≥ r−1`, άρα r ≤ 17. Το ACK TTL `min(16,rank+2)` (`serial_bridge.py:469-481`) έχει το ίδιο όριο.
   Στο 50×10 τα ranks 18–50 (330 κόμβοι) **δεν παραδίδουν ποτέ** με το σημερινό firmware. Ο sim δείχνει το exact firmware και ένα what-if με MAX_TTL=255.
2. **Το flood ACK είναι O(N²).** Κάθε non-sleepy κόμβος με rank ≤ min(16,r+2) ξαναστέλνει κάθε ACK μία φορά.
   Στο 50×10 με firmware defaults: Σ_{r=1..17} 10·10·min(16,r+2) = **18.100 broadcasts/κύκλο**.
   Στο what-if χωρίς όριο TTL: Σ_{r=1..50} 10·10·min(50,r+2) = **137.200 broadcasts/κύκλο**.
3. **Ρίζα = UART bottleneck.** Η γραμμή frame είναι 150 B, δηλαδή 13,02 ms στα 115200 8N1, άρα **max 76,8 frames/s**.
   Το HardwareSerial TX buffer είναι 0 (`HardwareSerial.cpp:142`) και το HW FIFO 128 B, οπότε το `println` μπλοκάρει μέσα στο ESP-NOW RX callback.
   Η ουρά εισόδου της γέφυρας είναι τα WiFi RX buffers (static 8 + dynamic 32). Άρα σε burst 500 frames έχουμε **drops στη γέφυρα**.
4. **Στο all-sleepy δεν γίνεται re-flood ACK.** Το gate `!sleepy` (`mesh_node.h:479`) μπλοκάρει κάθε re-flood, οπότε τα ACK δεν περνούν το rank 1.
   Το CART προτείνει `!sleepy || rxWindowOpen`. Και τα δύο θα είναι switchable.
5. **Οι airtimes του CART spec είναι υποεκτιμημένες.** Οι τιμές 0,63 / 1,2 ms παραλείπουν τα 15 B του vendor action frame.
   Οι σωστές είναι beacon **752 µs**, data+SIFS+ACK **1.338 µs** (βλ. §D).
6. Ο **neighbor table είναι 16** θέσεων, ενώ κάθε κόμβος ακούει ~30 (3 ranks × 10), άρα έχουμε thrash.
   Ο **dedup ring είναι 32/30 s**, ενώ ένα rank-1 relay περνά ~50 frames/κύκλο, οπότε αντίγραφα μπορεί να ξεφεύγουν.
7. **Τα relays σήμερα δεν έχουν buffer** (cut-through, `mesh_node.h:671-701`). Ο relay buffer είναι νέα παράμετρος (§H).

---

## Κατάλογος παραμέτρων (draft παραδοτέου #1)

Σε κάθε τιμή σημειώνεται η πηγή. Το τελικό `docs/simulator/PARAMETERS.md` **παράγεται από τον κώδικα**:
ο parser διαβάζει τα `#define` από τα headers, οπότε ο κατάλογος δεν μπορεί να αποκλίνει από το firmware.

### A. Firmware (GreenhouseMesh, sketches, Pi)
| Παράμετρος | Τιμή | Πηγή |
|---|---|---|
| Data packet | 61 B = header 16 + nettag 8 + body 21 + apptag 16 | `mesh_packet.h:15-21` |
| Beacon / ACK / join / provision | 27 / 20 / 8 / 33 B (CART beacon v3: 29 B) | `mesh_node.h:50,91,66`; `mesh_crypto.h:14` |
| Beacon trickle | 2000 → ×2 → 60000 ms, reset σε αλλαγές | `mesh_config.h:29-30`; `mesh_node.h:274-281` |
| Bridge beacon | 2000 ms σταθερό | `mesh_config.h:31` |
| Parent timeout | 3 × advertised interval· ή 3 διαδοχικά tx fails | `mesh_config.h:33`; `mesh_node.h:390-396` |
| Orphan fresh / reset gap | 60000 / 10000 ms | `mesh_config.h:41-44` |
| Parent selection | strict rank < δικό μου· μετά χαμηλότερο rank, μετά RSSI | `mesh_node.h:374-379` |
| TTL | margin 2, max 16· data = min(rank+2,16)· ACK = min(16,rank+2), fallback 6 | `mesh_config.h:14,19`; `serial_bridge.py:469-481`; `mesh_node.h:77` |
| Own buffer / in-flight | 10 (drop-oldest, RTC) / 11 | `mesh_config.h:89`; `mesh_inflight.h:22` |
| Dedup ring / ACK dedup | 32 / 8 εγγραφές, παράθυρο 30000 ms | `mesh_config.h:76-77`; `mesh_node.h:135` |
| Neighbor slots | 16 | `mesh_node.h:126` |
| Wake: warmup / tx-confirm / app-ACK wait / discovery / max awake / min sleep | 2000 / 500 / 2000 / 5000 / 10000 / 1000 ms | `edge_node_esp32_c3.ino:39`; `mesh_config.h:61-72` |
| Battery ADC | 8 δείγματα × 2 ms | `edge_node_esp32_c3.ino:97-100` |
| Cold-boot USB wait | 1500 ms (μόνο σε μη-timer wake) | `edge_node_esp32_c3.ino:248` |
| Always-on κύκλος | 5000 idle + 2000 warmup ≈ 7 s | `edge_node_esp32_c3.ino:333-353` |
| Link counter | ≥2 unconfirmed wakes → rescan καναλιού | `edge_node_esp32_c3.ino:145,220-222` |
| T (sleep interval) | **ανά run**, presets 900 / 1800 s (firmware σήμερα 60 s test)· static_assert 10 s < 30 s < T | `mesh_config.h:60,95-100` |
| Bridge UART | 115200 8N1· frame line 150 B → 13,02 ms· ACK line ~66 B → ~5,7 ms | `bridge_esp32.ino:15,153`; `serial_bridge.py:474` |
| Bridge ACK | 1 broadcast, χωρίς retry, χωρίς re-flood | `bridge_esp32.ino:91-107` |
| Pi επεξεργασία/frame | **param**, default 20 ms, εύρος 5–300 ms (μη μετρημένο· reload nodes.json + AES-GCM + ≤6 publish) | `serial_bridge.py:303-390`; CART v2 §3.2 |
| Pi replay | seen-set, re-ACK διπλότυπου χωρίς republish | `serial_bridge.py:291-356` |
| CART (Part C, planned) | G_min 250 ms· G_max = 2·\|b\|·T·1,3· J 100 ms· 3 attempts· 150 ms/child· max 6 children· RX_OPEN/100 ms· knock 500 ms· linger 300 ms· hist 16· k 1,5· pad 50 ms | `cart_v2 spec §3`; `cart plan:974-989` |
| Drift | bias planning 0,6 %, measured 0,17 % (Gate 0 run 1)· step 0,01 %@300 s, κλιμάκωση ∝ T (0,03 %@900 s, 0,06 %@1800 s extrapolated) | `cart_v2 spec §2,§5`; `cart_sim.py:32` |

### B. Hardware: ESP32-C3 (datasheet Tables 5-7…6-4) + sdkconfig του core 3.3.11
| Παράμετρος | Τιμή | Πηγή |
|---|---|---|
| TX 802.11b 1 Mbps | 335 mA @21 dBm (το firmware κόβει στα 20 dBm: `CONFIG_ESP_PHY_MAX_WIFI_TX_POWER=20`) | datasheet 5-7; sdkconfig:1706 |
| RX 802.11b/g/n | 84 mA | datasheet 5-7 |
| Modem-sleep 160 MHz | 23 mA CPU run / 16 idle (periph off)· 28 / 21 (periph on) | datasheet 5-8 |
| Light / deep sleep (chip) | 130 µA / 5 µA | datasheet 5-9 |
| RX sensitivity 1 Mbps | −98,4 dBm | datasheet 6-4 |
| RTC FAST mem / bootloader reserve | 8192 B / 16 B | datasheet; sdkconfig:389 |
| WiFi buffers | dyn TX 32· static RX 8· dyn RX 32· mgmt 32 | sdkconfig:1839-1856 |
| CPU / RTC clock | 160 MHz / internal RC ~136 kHz (RC_FAST_D256 = +5 µA) | sdkconfig:1614,1758 |
| UART HW FIFO / TX ringbuffer | 128 B / 0 | `HardwareSerial.cpp:34,142` |
| Wake latency (deep sleep → app) | 140–230 ms | `02-esp-now-protocol.md:113` |

### C. Board / ενέργεια / μπαταρία / solar (repo docs)
| Παράμετρος | Τιμή | Πηγή |
|---|---|---|
| Sleep floor board | 55 µA + divider 7,5 µA = **62,5 µA** | `SENSOR_NODE_POWER_AND_SOLAR.md:137`; `cart_sim.py:26` |
| Lumped active (repo model) | 86,5 mA | `cart_sim.py:25` |
| Always-on | ~100 mA → 2400 mAh/day | `EDGE_NODE_POWER_OPTIMIZATION.md:18` |
| DHT22 / soil | 1,5 mA / 5 mA, μόνο κατά το warmup | `SENSOR_NODE_POWER_AND_SOLAR.md:44-45` |
| Awake budget Phase 1 | 2,5 s | `SENSOR_NODE_POWER_AND_SOLAR.md:138` |
| Μπαταρία | LiFePO4 18650 1500 mAh, **1200 usable** (80 % DoD), πίνακας SoC 3400→2800 mV | `SENSOR_NODE_POWER_AND_SOLAR.md:47,167`; `serial_bridge.py:255-257` |
| Solar | 6 V/2 W, χειρότερος χειμώνας **437 mAh/day** (TP5000 70 %) | `SENSOR_NODE_POWER_AND_SOLAR.md:224-231` |
| Έλεγχος αναφοράς | Phase-1 leaf = **7,26 mAh/day @900 s**, 18,79 @300 s | `cart_sim_results.txt:81,91` |

Δύο energy models με διακόπτη:
- **lumped:** 86,5 mA / 62,5 µA, αναπαράγει το repo.
- **per-state:** με τιμές datasheet: BOOT 23 mA × t_boot, LISTEN/RX 84 mA, TX 335 mA μόνο για τη διάρκεια του airtime, CPU 23 mA, αισθητήρες +6,5 mA στο warmup, SLEEP 62,5 µA.

### D. PHY/MAC (IEEE 802.11 DSSS + ESP-NOW frame format)
- 1 Mbps DBPSK, long PLCP **192 µs** (υποχρεωτικό στο 1 Mbps).
- ESP-NOW overhead **43 B**: MAC hdr 24 + category 1 + OUI 3 + random 4 + vendor element 7 + FCS 4.
- **airtime(L) = 192 + 8·(43+L) µs**, οπότε:

| Frame | join 8 | ACK 20 | beacon 27 | beacon v3 29 | prov 33 | data 61 | 802.11 ACK (14 B) |
|---|---|---|---|---|---|---|---|
| µs | 600 | 696 | 752 | 768 | 800 | 1024 | 304 |

- SIFS 10 µs, slot 20 µs, DIFS 50 µs, CW 31→1023 (DSSS).
- Εναλλακτικό preset ERP: slot 9 µs, CW 15, όπως το `cart_sim` με cw=16. Γίνεται sensitivity και στα δύο, γιατί το blob της Espressif δεν τεκμηριώνει ποιο χρησιμοποιεί.
- Unicast: DIFS + backoff + data + SIFS + ACK = 1388 µs + backoff.
- **MAC retry limit:** η Espressif δεν το τεκμηριώνει και οι αναφορές κοινότητας λένε ~5. Param default 5, sweep {0,3,5,7}. Σημειώνεται ως μη επαληθευμένο.
- Κανάλι 1 (`mesh_config.h:55`). Max payload 250 B (v1).

### E. Ράδιο / κανάλι (μοντέλο, μαθηματικά συνεπές)
- Log-distance: PL(d) = 40,1 dB (1 m @2412 MHz) + 10·n·log10(d) + X_σ.
- Προσαρμοσμένο στη μετρημένη ευαισθησία του datasheet:
  - 802.11b ορίζει sensitivity στο FER 8 % με PSDU 1024 B, άρα BER_s = 1 − 0,92^(1/8192) = 1,02·10⁻⁵.
  - Για DBPSK, BER = ½·e^(−Eb/N0), άρα στο −98,4 dBm Eb/N0 = 10,34 dB.
  - SINR→BER με την ίδια καμπύλη, και PER(L) = 1 − (1−BER)^(8·(43+L)).
  - Οι συγκρούσεις προκύπτουν φυσικά, αφού η παρεμβολή μετρά ως θόρυβος. Δεν χρειάζεται αυθαίρετο capture threshold.
- CCA threshold: param default −82 dBm. Οι hidden terminals προκύπτουν από την τοπολογία.
- Params: n (default 2,5), σ (4 dB), fading ανά frame, απόσταση/τοπολογία.

### F. Σενάριο (ανά run)
- R ranks (50), W ανά rank (10).
- Τοπολογία: layered (γειτονία μόνο rank k±1, εγγυημένα R ranks) ή geometric 2D.
- T ∈ {900, 1800, custom}, διάρκεια (κύκλοι), seeds.
- Τεχνική ∈ {T1-ladder, T1-per-cycle, T2-flood, T2-unicast}. Sleep mode ∈ {phase1, cart1, allsleepy}.
- ACK relay gate ∈ {firmware `!sleepy`, CART `!sleepy||rxOpen`}. MAX_TTL (16 | what-if).
- Relay buffer B + πολιτική overflow (drop-oldest/drop-new/backpressure).
- Failure injection: MTBF κόμβου, DHT NaN, power-loss → seq reset.

### G. Παράγωγες ποσότητες (υπολογίζονται και εμφανίζονται)
- Airtimes.
- Ταβάνι TTL (17).
- Αναμενόμενα floods.
- Μέγιστος ρυθμός γέφυρας (76,8 fr/s).
- Guard G_max ανά T και b:
  - με b = 0,17 %: 3,98 s @900, 7,96 s @1800
  - με b = 0,6 %: 14,04 / 28,08 s
- ρ = λ/μ σε κάθε ουρά.
- Χωρητικότητα RTC buffer (§H).

### H. Default του relay buffer (υπολογισμός από τους περιορισμούς του hardware, όπως ζητήθηκε)
Το B πρέπει να επιβιώνει στον deep sleep, άρα ζει στη RTC FAST (8192 B):

`B_max = ⌊(8192 − 16 [bootloader] − RTC_IDF/Arduino [μετρημένο] − sizeof(MeshRtcState)≈632 − CART state − margin) / 61⌋`

Προσωρινά B_max ≈ 100.

Το `RTC_IDF` μετριέται με compile του edge sketch μέσω arduino-cli και ανάγνωση των sections `.rtc.*` από το ELF/map.

Επίσης B ≥ μέγιστο subtree ανά κύκλο: στο 50×10 ένα rank-1 relay έχει ~50 απογόνους.

Default B = min(B_max·0,8, …). Ο buffer ελέγχεται και ως προς το χρόνο flush (B × 1,7 ms) μέσα στο awake cap των 10 s και ως προς τα dyn TX buffers (32) για back-to-back sends.

---

## Τεχνικές (σημασιολογία στον sim)

- **T2 end-to-end:** κάθε origin στέλνει, τα relays κάνουν cut-through (exact `meshRelayData`), η γέφυρα γράφει στο UART, το Pi απαντά με ACK line, η γέφυρα κάνει broadcast και μετά:
  - **flood:** exact `meshHandleAck`, με dedup 8, TTL και gate.
  - **unicast:** reverse path, με downward table ανά relay.
  
  Ο origin περιμένει 2000 ms. Ό,τι μείνει αναπάντητο γίνεται requeue για τον επόμενο κύκλο, και αυτό δίνει διπλότυπα στο Pi.
  Στο all-sleepy mode υπάρχει κοινό συγχρονισμένο παράθυρο ≤ 10 s.
- **T1 hop-by-hop:** το παιδί στέλνει, ο parent το αποθηκεύει στον buffer B και απαντά με per-hop ACK (unicast 20 B), και το παιδί απελευθερώνει.
  - **ladder:** staggered wake τύπου DMAC. Το rank r ξυπνά στο slot (R−r−1) για να λάβει και στο (R−r) για να στείλει, άρα latency ≈ R·τ μέσα σε 1 κύκλο.
  - **per-cycle:** ένα hop ανά κύκλο, latency R κύκλοι.
  
  Η γέφυρα κάνει ACK μόνο ό,τι χώρεσε στην ουρά της, κι αυτό λειτουργεί ως φυσικό backpressure.
  Το τ προκύπτει από G + αναμενόμενα frames × χρόνο exchange και μπορεί να αλλάξει.

## Μετρικές (όλες ανά κόμβο / rank / δίκτυο, με 95 % CI από πολλά seeds)

- **Ενέργεια:**
  - mAh/day ανά state, μέσο ρεύμα, διάρκεια ζωής μπαταρίας
  - περιθώριο έναντι solar
- **Χρόνος:**
  - χρόνος ανά state (sleep, boot, listen, RX, TX, CPU) και duty cycle
  - **time-on-air** (TX) και **time-alive** (awake)
- **Κίνηση:**
  - frames ανά τύπο, retries/frame
  - channel utilization ανά collision domain
- **Αξιοπιστία:**
  - PDR end-to-end και ανά hop, ACK delivery
  - **drops ανά αιτία:** PER, collision, TTL, overflow, dedup, missed wake, no parent, bridge queue
  - διπλότυπα στο Pi
  - missed wakes, orphan sweeps, αλλαγές parent
- **Συγκρούσεις:** P(collision)/frame μετρημένο και αναλυτικό, ποσοστό από hidden terminals.
- **Latency:** παραγωγή→Pi (κατανομή ανά rank), ACK RTT, ηλικία μέτρησης.
- **Buffers:**
  - own, relay, bridge-RX queue, in-flight: time-weighted mean, p95, max
  - **utilization** = occupancy/capacity, overflow
  - χρόνος ανακύκλωσης του dedup ring
- **Ουρές:** ρ ανά κόμβο και στη γέφυρα, σύγκριση με M/D/1/K.
- **Σύνοψη T1 vs T2** ανά σενάριο.

## Αρχιτεκτονική (κάθε μονάδα έχει μία ευθύνη)

`sim/meshsim/`, καθαρή stdlib ώστε να τρέχει σε Pyodide:
- `firmware_params.py`: parser για `#define`/structs από τα headers και το `.ino` (single source of truth).
- `params.py`: dataclasses ομάδων A–H, παράγωγες ποσότητες, export σε MD/JSON.
- `des.py`: event kernel (heapq, ντετερμινιστικά seeds).
- `phy.py`: airtime, BER/PER, CSMA/CA backoff, retries.
- `channel.py`: SINR, CCA, hidden terminals.
- `topology.py`: layered / geometric, RSSI matrix.
- `clock.py`: drift (bias + OU step), guard policies (port από `cart_sim.py`).
- `energy.py`: lumped / per-state, μπαταρία, solar.
- `node.py`: exact firmware κατάσταση (buffers, in-flight, dedup, neighbors, trickle, parent, TTL, wake cycle).
- `bridge.py`, `pi.py`: UART serialization, RX queue 8+32, Pi latency, ACK TTL.
- `protocols/`: `phase1.py`, `t1.py` (ladder / per_cycle), `t2.py` (flood / unicast), `cart.py`.
- `metrics.py`, `analytic.py` (κλειστοί τύποι για cross-check), `cli.py` (`python -m meshsim params|run|sweep|report`).

Επιπλέον:
- `sim/scenarios/*.json`: presets.
  - `bench3` (τα 3 πραγματικά sensors, T=60)
  - `phase1_900`
  - `g50x10_{t1ladder,t1cycle,t2flood,t2uni}_{900,1800}`
  - `cart_pair`
- `sim/web/index.html`: dashboard.
  - Pyodide από jsdelivr, φορτώνει τα `.py`.
  - Parameter panel με tooltip πηγής.
  - Run, Gantt timeline καταστάσεων ανά κόμβο/rank, animation ranks.
  - Γραφήματα ενέργειας, buffer, PDR/latency CDF, συγκρούσεων.
  - Σύγκριση T1/T2, import/export JSON.
  - Δημοσιεύεται ως Artifact.
- `sim/tests/`: pytest, όπως το `pi/tests`.

Κλίμακα: το 500×(T2-flood) έχει ~10⁶ events/κύκλο, οπότε τα μεγάλα runs γίνονται από CLI με `--cycles` και steady-state extrapolation για mAh/day. Στο browser τα defaults είναι μικρά ή γίνεται import JSON.

## Επαλήθευση (μαθηματική ορθότητα)

1. Parser: οι τιμές ταυτίζονται με τα headers και το test σπάει αν αλλάξει το firmware.
2. Airtimes: 600/696/752/768/800/1024/304 µs ακριβώς.
3. Energy lumped: Phase-1 leaf **7,26 mAh/day @900 s** και **18,79 @300 s** (αναπαραγωγή `cart_sim_results.txt`).
4. CSMA first-slot collision: 6,25 / 12,1 / 17,8 % για k=2/4/6 (= `cart_sim` B1). Pure ALOHA DES: 1−e^(−2G).
5. Ένα link με PER p και R retries δίνει 1−p^(R+1) εντός CI.
6. Ουρά γέφυρας έναντι αναλυτικού M/D/1/K.
7. Guard policy: αναπαράγει τον πίνακα A2 του `cart_sim` εντός ανοχής.
8. Ταβάνι TTL: το rank 17 παραδίδει, το 18 όχι (firmware defaults).
9. Flood count χωρίς απώλειες = **18.100** ακριβώς (50×10).
10. Ίδιο seed δίνει byte-identical JSON.

Τρέξιμο: `python -m pytest sim/tests`, `python -m meshsim run sim/scenarios/g50x10_t2flood_900.json`, και το dashboard ανοιχτό στο browser pane.

## Σειρά υλοποίησης

0. Spec doc (+ commit). Δεν αγγίζω τις ανοιχτές αλλαγές στο `docs/presentation/`.
1. **Κατάλογος παραμέτρων:** `firmware_params.py` + `params.py` + παραγωγή `docs/simulator/PARAMETERS.md` και προβολή του.
2. RTC budget (arduino-cli compile + ELF sections) → `docs/simulator/rtc_budget.md` → default B.
3. `des` + `phy` + `channel` + tests (2, 4, 5).
4. `topology` + `node` (exact firmware) + tests (1, 8).
5. `energy` + `clock` + tests (3, 7).
6. Πρωτόκολλα: phase1 / t2 / t1 / cart + bridge / Pi + tests (6, 9).
7. `metrics` + `analytic` + CLI (JSON/CSV) + test 10.
8. Web dashboard (Pyodide) + Artifact.
9. Τρέξιμο των presets → `docs/simulator/RESULTS.md` (T1 vs T2, 50×10, 900/1800 s).

Εκτέλεση inline (κυρίως μηχανικές εργασίες, και υπάρχει το γνωστό πρόβλημα των subagents με το worktree). Λιτό scope, όπως ταιριάζει σε διπλωματική.
