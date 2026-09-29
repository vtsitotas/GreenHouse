# meshsim — Μαθηματικό μοντέλο: τύποι, παραδοχές, εξηγήσεις

Το έγγραφο συγκεντρώνει **κάθε τύπο και κάθε παραδοχή** του simulator, με παραπομπή στη συνάρτηση του κώδικα που τον υλοποιεί.
Οι τιμές των παραμέτρων και η πηγή τους βρίσκονται στο [PARAMETERS.md](PARAMETERS.md) (το ονομάζουμε `cat[...]` / `cfg[...]`).
Τα αριθμητικά παραδείγματα βγαίνουν από τον ίδιο τον κώδικα, με τη ρύθμιση «15′ SHT40» του [WORLD_GREENHOUSE_100x50.md](WORLD_GREENHOUSE_100x50.md):
R = 100 ranks, W = 50 κόμβοι/rank, T = 900 s, slot 0,8 s, J 50 ms, k 2,5, bias_wander, γέφυρα 921600 binary, Pi 5 ms.

Δύο μηχανές, με την ίδια φυσική:
- **calculator** (`calculator.py`): ντετερμινιστικός, με αναμενόμενες τιμές. Δίνει ενέργεια ανά rank σε ms.
- **DES** (`des.py`): discrete-event. Κάθε frame, backoff, σύγκρουση, retry, buffer και ξύπνημα είναι γεγονός στο χρόνο, και ελέγχει τον calculator.

Μονάδες: δευτερόλεπτα, mA, mAs (= mA·s), mAh. `1 mAh = 3600 mAs`.

Οι αριθμοί σε αγκύλες [n] παραπέμπουν στη [Βιβλιογραφία](#19-βιβλιογραφία).
Οι αποδείξεις/εξηγήσεις των τύπων είναι στο [Παράρτημα Α](#παράρτημα-α-από-πού-βγαίνει-κάθε-τύπος).

---

## Σύμβολα

| Σύμβολο | Σημασία | Config |
|---|---|---|
| R, W, N = R·W | ranks, κόμβοι ανά rank, σύνολο κόμβων | `net.ranks`, `net.per_rank` |
| T | κύκλος αφύπνισης | `timing.T_s` |
| m | μετρήσεις ανά αποστολή (batching) | `timing.report_every` |
| T_c = T·m | περίοδος επικοινωνίας | — |
| r | rank κόμβου (1 = δίπλα στη γέφυρα) | — |
| s(r) = R − r + 1 | frames που στέλνει ένας κόμβος rank r ανά κύκλο (δικό του + υποδέντρο) | — |
| L | payload ESP-NOW σε bytes (data = 61) | `MESH_PACKET_LEN` |
| SLOT, J | παράθυρο λήψης, jitter αποστολής | `scheme.t1_slot_s`, `radio.jitter_s` |
| b, step | bias και «περιπλάνηση» ρολογιού ζεύγους | `sync.bias`, `sync.step_per_300s` |
| G | guard (πόσο νωρίτερα ξυπνά ο κόμβος για να βρει τον parent) | — |

---

## 1. Τοπολογία (παραδοχή)

**Ισορροπημένο layered δέντρο.** Ο κόμβος (r, j) έχει γονιό τον (r−1, j) και ο rank 1 τη γέφυρα (`des.parent`).
- Κάθε «στήλη» j είναι μια αλυσίδα R κόμβων, και ο rank r κουβαλά s(r) = R − r + 1 μετρήσεις ανά κύκλο.
- Ο χειρότερος relay είναι ο rank 1 (s = R) ή ο rank 2 (s = R − 1, αλλά συγχρονίζεται και με γονιό· βλ. §9).

**Ακοή (ποιος ακούει ποιον):**
- Ένας κόμβος ακούει τα ranks r−1, r, r+1 (`des.hears`).
- Ένα ποσοστό `net.hidden_frac` (30 %) ζευγών στο ίδιο rank δεν ακούγονται (hidden terminals).
- Τα r−1 και r+1 δεν ακούγονται μεταξύ τους, οπότε hidden terminals υπάρχουν και γύρω από κάθε δέκτη.

**Γιατί:** δίνει το χειρότερο ρεαλιστικό φορτίο (μακριές αλυσίδες) και κλειστούς τύπους.
Σε πραγματικό θερμοκήπιο το δέντρο είναι ακανόνιστο: κάποιοι relays έχουν πολλά παιδιά, κάποιοι κανένα.
Το `net.max_children` (3) εκφράζει τον πιο φορτωμένο γονιό μόνο για τις συγκρούσεις.

Πηγές: η έννοια του rank (απόσταση από τη ρίζα σε hops) είναι η ίδια με το RPL [20]. Ο τρόπος ακοής ακολουθεί το κλασικό μοντέλο hidden terminal [7].

---

## 2. PHY / MAC (IEEE 802.11 DSSS 1 Mbps + ESP-NOW)

`analytic.py`

```
airtime(L) = 192 µs (long PLCP) + 8·(43 + L) / 1 Mbps
```
- Τα 43 B είναι το overhead του vendor action frame ESP-NOW: MAC header 24 + category 1 + OUI 3 + random 4 + vendor element 7 + FCS 4.
- Το long preamble είναι υποχρεωτικό στο 1 Mbps.

| Frame | L (B) | airtime |
|---|---|---|
| data | 61 | **1024 µs** |
| MeshAck | 20 | 696 µs |
| beacon | 27 | 752 µs |
| 802.11 ACK (14 B, χωρίς ESP-NOW overhead) | — | 192 + 112 = **304 µs** |

Χρόνοι CSMA/CA (DSSS): SIFS = 10 µs, slot = 20 µs, DIFS = SIFS + 2·slot = 50 µs, CW ∈ [31, 1023].

```
backoff ~ Uniform{0..CW} slots          →  E[backoff] = CW/2 · 20 µs = 310 µs  (CW = 31)
unicast  = DIFS + backoff + airtime(L) + SIFS + 304 µs
broadcast = DIFS + backoff + airtime(L)            (χωρίς ACK, χωρίς retry)
t_exch   = 50 + 310 + 1024 + 10 + 304 = 1698 µs    (data, μέση τιμή)
```

**Retries.** Με πιθανότητα αποτυχίας ανά απόπειρα p και r retransmissions (`radio.mac_retry` = 5, **μη τεκμηριωμένο** από την Espressif):
```
P(αποτυχία frame) = p^(r+1)
E[απόπειρες]      = (1 − p^(r+1)) / (1 − p)                 (γεωμετρική κομμένη στο r+1)
t_frame = t_exch · E[απόπειρες] + t_proc                    (t_proc = radio.hop_proc_s = 2 ms, μοντέλο: CMAC + callback)
```
Παράδειγμα: t_frame = 1,698 + 2 = **3,698 ms**.

**Παραδοχή:** το CW δεν διπλασιάζεται στον calculator (μέση τιμή πρώτης απόπειρας). Στον DES διπλασιάζεται: `CW_n = min((CW+1)·2ⁿ − 1, 1023)`.

Πηγές: χρόνοι DSSS (PLCP, SIFS, slot, CW) [1, Clause 15], DCF/CSMA-CA και ACK [1, Clause 10], μορφή πλαισίου ESP-NOW και προεπιλεγμένος ρυθμός 1 Mbps [11], ανάλυση DCF [5].

---

## 3. Κανάλι: από RSSI σε PER

`analytic.frame_error_rate`

1. **Βαθμονόμηση στην ευαισθησία του datasheet.**
   Το 802.11b ορίζει την ευαισθησία στο FER 8 % με PSDU 1024 B, άρα:
   ```
   BER_s = 1 − 0,92^(1/8192) = 1,02·10⁻⁵
   ```
2. **DBPSK.** Ισχύει `BER = ½·exp(−Eb/N0)`, άρα:
   ```
   (Eb/N0)_s = ln(1/(2·BER_s)) = 10,34 dB   στα −98,4 dBm
   ```
3. **Σε κάθε άλλη ισχύ λήψης:**
   ```
   Eb/N0(RSSI) = (Eb/N0)_s + (RSSI − (−98,4)) − interference_margin
   BER = ½·exp(−10^(Eb/N0/10))
   PER(L) = 1 − (1 − BER)^(8·(43+L))
   ```
   Για data: PER = 0,84 % στην ευαισθησία, 1,8·10⁻⁷ στα +3 dB, ≈ 0 από +6 dB και πάνω.
   **Το link είναι σχεδόν «ψηφιακό»: είτε δουλεύει είτε όχι.**

**RSSI κάθε link (DES):**
```
RSSI = −98,4 + link_margin (10 dB) + X,   X ~ N(0, σ = 4 dB), σταθερό ανά link
```
- **Link προς γονιό:** το X είναι το μέγιστο από `des.parent_candidates` = 3 ανεξάρτητα δείγματα.
  Έτσι μοντελοποιείται ότι το firmware διαλέγει γονιό πρώτα με χαμηλότερο rank και μετά με **καλύτερο RSSI**, και αλλάζει γονιό μετά από 3 αποτυχίες (`TX_FAIL_DROP_COUNT`).
- Χωρίς αυτό, 1 στα ~2 000 links πέφτει κάτω από την ευαισθησία (ουρά −3,3σ). Ο στατικός γονιός τότε κόβει όλο το υποδέντρο, κάτι που δεν συμβαίνει στο firmware.
- **Calculator:** χρησιμοποιεί μόνο το μέσο link (sens + 10 dB), δηλαδή p ≈ 0 ανά απόπειρα.

Πηγές: ορισμός ευαισθησίας με FER 8 % σε PSDU 1024 B [1, Clause 15], BER του DBPSK [2], log-normal shadowing [3], ευαισθησία −98,4 dBm στο 1 Mbps [9].

---

## 4. Συγκρούσεις

### Calculator (κλειστοί τύποι, `compute`)
Θεωρούμε c = `net.max_children` παιδιά που ξεκινούν μαζί προς τον ίδιο γονιό.

- **CSMA, σύγκρουση στο πρώτο slot** (ίδιο με `cart_sim` B1). Τα παιδιά που ακούγονται μεταξύ τους συγκρούονται μόνο αν διαλέξουν ίδιο ελάχιστο backoff, και το MAC retry το λύνει:
  ```
  P = 1 − Σ_{m=0}^{CW−1} c·(1/CW)·((CW−1−m)/CW)^(c−1)
  ```
  Παραδείγματα: 6,25 / 12,1 / 17,8 % για c = 2 / 4 / 6 και CW = 16.
- **Hidden παιδιά:** επικαλύπτονται σαν ALOHA μέσα στο jitter J. Με x = airtime/J:
  ```
  P(επικάλυψη ζεύγους)  = 2x − x²
  P(ανά απόπειρα)       = 1 − (1 − hidden_frac·(2x − x²))^(c−1)
  P(χάνεται μετά από A app-απόπειρες) = P(ανά απόπειρα)^A          (A = radio.attempts = 3)
  ```

### DES (ακριβές μοντέλο, `sense`, `_rx_ok`)
- **Carrier sense:** ο αποστολέας περιμένει όσο ακούει κάποιον που εκπέμπει. Περιμένει επίσης όσο ο δέκτης ενός unicast που ακούει βρίσκεται στη φάση του ACK (virtual carrier sense).
- **Αποτυχία λήψης** όταν ισχύει κάτι από τα εξής:
  - ο δέκτης κοιμάται·
  - ο δέκτης εκπέμπει ο ίδιος (half duplex)·
  - ο δέκτης ακούει *οποιονδήποτε* άλλον που εκπέμπει με χρονική επικάλυψη (όχι capture effect, συντηρητικό)·
  - αποτυγχάνει η κλήρωση PER.
- **ACK:** τα L2 ACK πιάνουν το μέσο αλλά δεν αλλοιώνονται ποτέ (απλούστευση).
- **Τι μετράει ο DES:** P(σύγκρουση ανά απόπειρα) = tx_coll / tx_attempts. Στο 100×50 βγαίνει 0,9 % (SHT40) και 0,2 % (DHT22).

Πηγές: ALOHA [4], CSMA και hidden terminal [6, 7], σύγκρουση στο πρώτο slot backoff [5].

---

## 5. Ρολόγια και συγχρονισμός (CART)

`clock.py` (γραμμή προς γραμμή port του `docs/analysis/cart_sim.py::run_policy`, με test που το ελέγχει) και `des.PairClock`.

### Μοντέλο απόκλισης ενός ζεύγους γονιός/παιδί
```
w_n      = 0,98·w_{n−1} + N(0, step_T)                 AR(1): θερμοκρασιακή «περιπλάνηση» ρυθμού
offset_n = carry + T·(b + w_n) + N(0, 20 ms)           πού βρίσκεται ο γονιός σε σχέση με την εκτίμησή μας
err_n    = offset_n − est
step_T   = step_300 · T/300                             (κλιμάκωση ∝ T: ΠΡΟΕΚΤΑΣΗ, μη μετρημένη στα 1800 s)
```
- **Στάσιμη τυπική απόκλιση της περιπλάνησης:** σ = T·step_T / √(1 − 0,98²).
  - T = 900 s: step = 0,03 %, **σ = 1,36 s**.
  - T = 1800 s: step = 0,06 %, **σ = 5,43 s**. Το σ ∝ T², και γι' αυτό το 30′ χάνει.
- **b:** 0,17 % μετρημένο (Gate 0 run 1), 0,6 % στην αρχική εκτίμηση.

### Πολιτική guard (firmware `mesh_sched.h`)
- **Hit** αν |err| ≤ G/2. Τότε:
  - ανανεώνεται η εκτίμηση (EWMA): `est ← 0,7·est + 0,3·offset` (η πρώτη φορά `est = offset`)·
  - μετά από 16 hits (παράθυρο `MESH_SCHED_HIST`) ισχύει
    ```
    G ← clamp(2·k·max|err|₁₆ + pad, G_min, G_max)          k = kNum/kDen (firmware 3/2), pad 50 ms, G_min 250 ms
    ```
- **Miss:** `G ← min(2G, G_max)` και `carry = err`, οπότε ο επόμενος κύκλος ξεκινά από το σφάλμα.
- **3 συνεχόμενα misses** → orphan sweep (ακούει έναν ολόκληρο κύκλο T_c) και reset της εκτίμησης.

### Μέγιστο guard G_max (firmware `meshSchedGuardMax`)
```
G_max = min( max( 2·|b|·T·1,3 ,  z·σ ),  MESH_GUARD_CAP_MS = 20 s ),     z = 6  (±3σ)
```
- Ο κανόνας `cart_v2` κρατά μόνο τον πρώτο όρο. Σε T = 900 s δίνει 3,98 s, αλλά σ = 1,36 s: το ζεύγος μπαίνει σε βρόχο miss/sweep.
- Ο κανόνας `bias_wander` (όπως το firmware) δίνει 8,14 s στα 900 s και 32,6 s → **20 s (cap)** στα 1800 s.

### Τι περνά στον calculator
Ο calculator τρέχει 20 000 κύκλους (`sync.cycles`) και κρατά:
- `miss` (P ανά κύκλο)·
- `listen` = E[χρόνος ακρόασης μέχρι το beacon του γονιού] = E[err + G/2], ή G σε miss·
- `guard_mean`·
- `drops_per_year` (sweeps)·
- `err_std`.

Στο παράδειγμα: guard_mean = 3,65 s, listen = 1,82 s, miss = 0.

**Συσσωρευμένη απόκλιση από τη ρίζα (T2):** κάθε hop ξανα-αγκυρώνεται στον δικό του γονιό, οπότε σ_r = σ_err·√(hops).

Πηγές: μοντέλο AR(1) [17], συγχρονισμός χωρίς κοινό ρολόι σε WSN [22, 23], RC ρολόι 136 kHz του ESP32-C3 και εξάρτηση από θερμοκρασία [10, 13], ιδέα διπλασιασμού παραθύρου μετά από αποτυχία (όπως το binary exponential backoff) [1, 5].

---

## 6. TTL και όριο βάθους

`analytic.py` (ακριβώς οι κανόνες του firmware)
```
TTL_data(r) = min(r + MESH_TTL_MARGIN, MESH_MAX_TTL)            meshTxTtl
```
- Κάθε relay κάνει drop αν ttl == 0 ή ttl > MAX, αλλιώς προωθεί με ttl−1.
- Η γέφυρα δεν ελέγχει TTL.
- Άρα παράδοση ⇔ TTL_data(r) ≥ r − 1.

```
TTL_ack(r)  = clamp(min(ACK_TTL_MAX, r + margin), 1, MESH_MAX_TTL)      serial_bridge + bridge
ο κόμβος rank k λαμβάνει ACK με ttl0 − (k−1) αν > 0
ταβάνι βάθους = max r: κάθε rank 1..r και παραδίδει και λαμβάνει ACK
```
Με margin 2: MAX_TTL 16 → rank 17, 64 → rank 65, 128 → rank 129.

**Flood ACK (T2):** ένας κόμβος ξαναστέλνει κάθε ACK μία φορά (dedup), εφόσον το έλαβε με ttl > 0.
Οι αναμεταδόσεις ανά κύκλο είναι
```
Σ_r W · #{κόμβοι k: ttl_ack(r) − (k−1) > 0}
```
Στο 50×10 με MAX_TTL 16 αυτό δίνει **18 100**. Η πολυπλοκότητα είναι O(N²).

Πηγές: κανόνες ακριβώς από το firmware (`mesh_node.h`, `serial_bridge.py`)· flooding και trickle [21].

---

## 7. Γέφυρα και Pi

`calculator._gateway`

**Γραμμή frame:**
- hex JSON: `len(fmt) + 2·L + 2 (CRLF)` = 150 B.
- binary (πρόταση): L + 5 (sync 2 + len 1 + CRC 2) = 66 B.

```
t_uart   = bytes · 10 bit / baud                               (8N1)
t_gw     = max(t_uart, t_Pi, t_uart_ack)                       (ανεξάρτητοι σειριακοί servers σε pipeline)
max ρυθμός = 1 / t_gw
ack_rtt  = t_uart + t_Pi + t_uart_ack + t_ack_broadcast
```
- **Σήμερα:** 150 B στα 115200 → 13,0 ms, Pi 20 ms (**μη μετρημένο**) → t_gw = 20 ms, 50 frames/s.
- **Πρόταση:** 66 B στα 921600 → 0,72 ms, Pi 5 ms → t_gw = 5 ms, 200 frames/s.

**Ουρά εισόδου γέφυρας:**
- Χωράει `bridge.ingress_queue` = 40 frames (WiFi RX buffers 8 static + 32 dynamic).
- Γεμάτη ουρά σημαίνει ότι δεν στέλνεται L2 ACK, άρα ο αποστολέας ξαναδοκιμάζει.
- Αναλυτικός έλεγχος: M/D/1/K (`md1k_blocking`, embedded Markov chain, Gross & Harris).

**Έλεγχος ρυθμού:** `N · t_gw ≤ T_c`, δηλαδή όλο το δίκτυο χωρά στη γέφυρα σε έναν κύκλο.

Πηγές: ουρά M/D/1/K [18], Wi-Fi RX buffers του ESP-IDF [12].

---

## 8. Τεχνικές

### 8.1 Phase 1 (σημερινό firmware χωρίς CART)
- Οι sleepy κόμβοι δεν γίνονται γονείς, άρα **μόνο ο rank 1 δρομολογείται**.
- Ο rank 1 κάνει: boot + αισθητήρες + wake beacon + 1 αποστολή + αναμονή app-ACK (`ack_rtt`).
- Οι υπόλοιποι ακούν `MESH_WAKE_DISCOVERY_MS` και ξανακοιμούνται.

### 8.2 T1 ladder όπως το firmware (CART depth N, `_scheme_t1_firmware`)
**Σκάλα:** κάθε κόμβος ανοίγει παράθυρο λήψης διάρκειας SLOT για τα παιδιά του. Οι αισθητήρες ζεσταίνονται μέσα στο παράθυρο.
Αμέσως μετά πιάνει το RX_OPEN beacon του γονιού του, που είναι ένα slot πιο νωρίς στη σκάλα, και στέλνει δικά του + ξένα frames.
Custody = το L2 ACK του γονιού. Ο γονιός κρατά το frame στον RTC relay buffer.
```
start[R] = t0 + SLOT,  start[r] = start[r+1] + SLOT     (σκάλα από το φύλλο προς τη ρίζα)
μήκος σκάλας = R · SLOT                                   (80 s για R 100, SLOT 0,8)
latency(r)   = r · SLOT + t_gw
```

**Πόσες στήλες μοιράζονται ένα slot (k_col).** Οι κόμβοι του rank 1 τρέχουν ελεύθερα με δικό τους ρολόι, άρα κάθε στήλη έχει τυχαία φάση.
Μια άλλη στήλη επικαλύπτει το παράθυρό μας (3 ranks ακοής × 2 πλευρές) με πιθανότητα ≈ 6·SLOT/T_c:
```
k_col = W                                     αν net.phase_sync (χειρότερη περίπτωση: όλοι σε φάση)
k_col = 1 + (W − 1)·min(1, 6·SLOT/T_c)        αλλιώς   (παράδειγμα: 1 + 49·0,00533 = 1,26)
```

**Ανάγκη παραθύρου:**
```
need[r] = k_col · s(r) · t_frame + J
need[1] = max(need[1], k_col · R · t_gw) · SLOT / flush_budget     (ο rank 1 στέλνει στη γέφυρα· flush_budget = rank1_flush_s ή SLOT)
slot_fill = max_r need[r] / SLOT                                    (παράδειγμα: 0,631 / 0,8 = 0,79)
```

**Επιπλέον ακρόαση για συγχρονισμό (rank ≥ 2).** Ο κόμβος ξυπνά G/2 νωρίτερα από το beacon του γονιού, ως τμήμα του δικού του παραθύρου:
```
early = max(0, G/2 − SLOT)
extra = max(0, early + listen − G/2)
```

**Αναμονή σειράς:** ο κόμβος στέλνει κατά μέσο όρο στη θέση (k_col+1)/(2·k_col) της ριπής.

**Χρήση καναλιού σε μια περιοχή ακρόασης (3 ranks × W):**
```
beacons/κόμβο = SLOT / 100 ms  (RX_OPEN)
airtime/κόμβο = beacons · (752 µs + DIFS + E[backoff])
frames        = Σ_{j∈busiest±1} s(j)
util = W · (3 · airtime/κόμβο + frames · t_frame) / T_c        (παράδειγμα: 6,25 %, όριο radio.max_util 30 %)
```

**Timeline ενός κόμβου rank r:**
```
boot (t_boot, I_cpu) → αισθητήρες (min(settle, SLOT), I_rx + I_sens) → ανάγνωση (I_rx + I_sens)
→ υπόλοιπο παραθύρου (I_rx) → [r ≥ 2] extra (I_rx) → αναμονή σειράς (I_rx) → s(r) αποστολές
```

### 8.3 T1 ladder με υπολογισμένα slots (`_scheme_t1`, όταν SLOT = κενό)
Αντί για σταθερό παράθυρο, το slot κάθε rank υπολογίζεται από την κίνηση:
```
D_r = W · s(r) · t_frame [+ W·t_ack αν batch] + J
D_1 = max(D_1, max(0, N − ingress) · t_gw) + J
```
Υποστηρίζει και app hop-ACK: `per_frame` (ένα ACK ανά frame) ή `batch` (ένα ανά ριπή).

### 8.4 T2 end-to-end (`_scheme_t2`)
Όλοι ξυπνούν σε **ένα** κοινό παράθυρο. Τα relays κάνουν cut-through και το ACK γυρίζει από το Pi.
```
up_time   = max(N·t_gw,  max_j Σ_{i∈j±1} W·s(i)·t_frame,  R·t_frame)
down_time = max(κανάλι ACK, N·t_uart_ack) + ουρά R hops
  flood:     κανάλι = max_j Σ W·floods(i) · t_ack_bcast
  unicast:   κανάλι = max_j Σ W·s(i) · t_ack_exch
  aggregate: ένα bitmap ACK ανά link
spread = 2·z·σ_err·√R                                     (συσσωρευμένη απόκλιση ρολογιών)
window = up_time + t_Pi + down_time + spread
```
Κάθε κόμβος είναι ξύπνιος όλο το παράθυρο (I_rx), εκτός από τα airtimes του (I_tx).

Πηγές: κλιμακωτό ξύπνημα σε δέντρο συλλογής δεδομένων (DMAC) [24], συγχρονισμένος κύκλος ύπνου/ξύπνου (S-MAC) [25], low-power listening και preamble (B-MAC) [26], custody transfer [27].

---

## 9. Ενέργεια

`calculator.Timeline`, `_currents`, `_sensor_phase`, `_add_tx`

**Ρεύματα (per-state, datasheet ESP32-C3):**

| Κατάσταση | Ρεύμα |
|---|---|
| TX | 335 mA (21 dBm· το firmware κόβει στα 20 dBm, άρα **άνω φράγμα**) |
| RX / radio ανοιχτό | 84 mA |
| CPU run / idle | 23 / 16 mA |
| light sleep | 0,13 mA |
| ύπνος | I_sleep = πλακέτα + divider + RTC ρολόι (π.χ. 55 + 0,1 + 0 µA = 55,1 µA με switched divider) |

Εναλλακτικό μοντέλο **lumped** (αναπαράγει το repo): 86,5 mA ξύπνιος, 62,5 µA ύπνος.
Test: Phase-1 φύλλο = 7,26 mAh/ημ @ 900 s.

**Αποστολή n frames:**
```
tx_air      = n · airtime · E[απόπειρες]        στα 335 mA
radio_other = n · t_frame − tx_air               στα 84 mA
```

**Ενέργεια ανά κύκλο και ανά ημέρα:**
```
mAs_κύκλου = Σ_states t_state·I_state + (T_c − awake)·I_sleep [+ (m−1)·ξυπνήματα μόνο-μέτρησης]
mAh/ημ     = mAs_κύκλου · (86400/T_c) / 3600 + sweeps_ανά_ημέρα · T_c · I_rx / 3600
```

Παράδειγμα, rank 2 (ο χειρότερος):

| Κατάσταση | mAs |
|---|---|
| boot | 4,6 |
| αισθητήρες | 8,9 |
| cpu | 2,2 |
| παράθυρο | 56,8 |
| **sync_listen** | **86,0** |
| αναμονή/ACK | 30,0 |
| TX | 34,0 |
| ύπνος | 49,5 |
| **Σύνολο** | **272 mAs/κύκλο → 7,25 mAh/ημ** |

- **Rank 1:** δεν έχει sync_listen, γιατί ο γονιός του είναι η γέφυρα που δεν κοιμάται. Βγάζει 5,20 mAh/ημ.
- **Φύλλο:** δεν κάνει relay. Βγάζει 5,66 mAh/ημ.

**Θεωρητικό όριο** (`_limits`): chip γυμνό (5 µA), χωρίς divider, SHT40, light sleep στο warm-up, boot 140 ms, 1 frame, G_min/2 ακρόαση.
Δίνει **0,74 mAh/ημ** (541 ημέρες χωρίς ήλιο).

**DES:** μετρά ακριβώς radio-on, ακρόαση και airtime κάθε κόμβου και προσθέτει το σταθερό κόστος ξυπνήματος (boot + αισθητήρες).
Τα orphan sweeps είναι σπάνια, οπότε ένα σύντομο run δεν τα δειγματίζει. Γι' αυτό χρησιμοποιείται ο αναμενόμενος ρυθμός του μοντέλου ζεύγους (`des.sweep_energy = expected`).

Πηγές: ρεύματα TX/RX/CPU/sleep [9], αισθητήρες [30, 31, 32].

---

## 10. Μπαταρία και ηλιακό

```
usable       = C · DoD                                = 1500 · 0,8 = 1200 mAh      (LiFePO4 18650)
αυτοεκφόρτιση = C · 3 %/μήνα / 30,44                  = 1,48 mAh/ημ                (τυπική, μη μετρημένη)
harvest      = P_panel · PSH · derate · η_charger / V_bat
             = 2 W · 2 h · 0,5 · 0,7 / 3,2 V          = 437,5 mAh/ημ  (Αθήνα, χειρότερος χειμώνας)
αυτονομία χωρίς ήλιο = usable / (mAh/ημ + αυτοεκφόρτιση)    (παράδειγμα: 1200 / (7,25 + 1,48) = 137,5 ημέρες)
περιθώριο ηλιακού    = harvest / (mAh/ημ + αυτοεκφόρτιση)   (50×)
energy neutral       ⇔ harvest ≥ mAh/ημ + αυτοεκφόρτιση
```

Πηγές: LiFePO4 (DoD, αυτοεκφόρτιση) [34], φορτιστής TP5000 [35], ηλιοφάνεια PVGIS [36].

---

## 11. Αξιοπιστία (on-time PDR)

Calculator, ανά rank r:
```
p_ontime(r) = (1 − miss)^(r−1) · (1 − p_link)^r · (1 − p_coll)^r
```
με p_link = p^(retries+1) και p_coll = P(hidden)^A.

- **Παραδοχή:** ανεξάρτητα γεγονότα ανά hop.
- **Ο DES δείχνει ότι αυτό είναι αισιόδοξο σε μεγάλο βάθος.** Ένα miss σε relay rank r καθυστερεί όλους τους R − r + 1 απογόνους του έναν κύκλο.
- Στην πράξη: 99,3–100 % (DES) έναντι 99,93 % (calculator) στο 100×50.

DES: `pdr_settled` = παραδομένες / παραχθείσες, **μόνο για μετρήσεις πριν από τον τελευταίο κύκλο**. Όσες παράχθηκαν στον τελευταίο κύκλο μπορεί να βρίσκονται ακόμα σε buffers.

---

## 12. Relay buffer: προϋπολογισμός μνήμης RTC

Ο buffer πρέπει να επιβιώνει στον deep sleep, άρα ζει στη RTC FAST (8192 B).
```
RTC_ours  = Σ sizeof(κάθε RTC_DATA_ATTR μεταβλητής)     (parser: τύποι, διαστάσεις, structs)
RTC_free  = 8192 − RTC_IDF (92 B, μετρημένο από το ELF) − RTC_ours
B_max     = ⌊(RTC_free + σημερινός relay buffer σε bytes) / 61⌋ = 121 frames
B_min     = μέγιστο υποδέντρο = R − 1                     (99 για R = 100)
```
Περιορισμοί του firmware που ελέγχονται επίσης:
- ριπή ≤ 32 dynamic TX buffers χωρίς αναμονή callback (αλλιώς `ESP_ERR_ESPNOW_NO_MEM`)·
- ξύπνημα ≤ `MESH_WAKE_MAX_AWAKE_MS`.

Πηγές: RTC FAST 8 KB και διατήρηση στον deep sleep [10, 14], dynamic TX buffers και `ESP_ERR_ESPNOW_NO_MEM` [11, 12].

---

## 13. Έλεγχοι (checks)

Κάθε run περνά από αυτούς. **Hard** = ο optimizer απορρίπτει τη ρύθμιση. **Soft** = αναφέρεται μόνο.

| Check | Συνθήκη | Είδος |
|---|---|---|
| ttl_depth | R ≤ ταβάνι TTL | hard |
| awake_cap | μέγιστος ξύπνιος ≤ `timing.awake_cap_s` | hard |
| relay_buffer | R − 1 ≤ B | hard |
| rtc_capacity | B ≤ B_max (121) | hard |
| ladder_span | R·SLOT ≤ T_c | hard |
| slot_capacity | max need[r] ≤ SLOT | hard |
| slot_covers_sensor | settle + ανάγνωση + 0,2 s ≤ SLOT | hard |
| channel_util | util ≤ 30 % | hard |
| gateway_rate | N·t_gw ≤ T_c | hard |
| energy_neutral | όλοι οι κόμβοι αυτάρκεις με το ηλιακό | hard |
| latency_req | max latency ≤ απαίτηση | hard |
| tx_burst | ριπή ≤ 32 ή custody ανά frame (l2/per_frame) | hard |
| dedup_ring | R − 1 ≤ 32 διαφορετικά frames σε 30 s στον rank 1 | soft (διπλότυπα, το Pi κάνει dedup) |
| neighbor_slots | 3W − 1 ≤ 16 | soft (άσκοπα trickle resets) |
| pdr_req | min p_ontime ≥ απαίτηση | στόχος |
| lifetime_req | αυτονομία χωρίς ήλιο ≥ 365 ημέρες | soft |
| usb_echo | χωρίς USB echo στη γέφυρα | soft |

---

## 14. Βελτιστοποίηση

`optimize.py`, `run_world_greenhouse.py`

- **Εφικτή** ρύθμιση: κανένα hard check δεν αποτυγχάνει, mean p_ontime ≥ στόχος και όλοι οι κόμβοι δρομολογούνται.
  Στη μελέτη 100×50 ζητείται επιπλέον **slot_fill ≤ 0,8**. Το 20 % headroom χρειάζεται για να αδειάζει η καθυστέρηση μετά από ένα sync miss (DES).
- **Μέγιστο W:** η εφικτότητα πέφτει μονότονα με το W, οπότε βρίσκεται με δυαδική αναζήτηση στο [1, 2000].
- **Κριτήριο:** ελάχιστη ενέργεια του χειρότερου κόμβου ανάμεσα στις εφικτές ρυθμίσεις, με πλήρες grid πάνω σε SLOT, J, κανόνα G_max, k και rank1_flush.

---

## 15. DES: πώς τρέχει

- **Πυρήνας:** ουρά προτεραιότητας (heapq) με (χρόνος, αύξων αριθμός, συνάρτηση). Ίδιο seed → ίδιο αποτέλεσμα.
- **Ανά κύκλο (T1):**
  - κάθε κόμβος ξυπνά στο start του παιδιού του (ή SLOT πριν από το δικό του start, αν είναι φύλλο), συν τη φάση της στήλης του: `Uniform(0, T_c − μήκος σκάλας)`, ή 0 αν `net.phase_sync`·
  - παράγει μία μέτρηση·
  - στο start[r] τρέχει το μοντέλο ζεύγους του. Αν έχει miss, ξανακοιμάται·
  - αλλιώς, μετά από `Uniform(0, J)`, στέλνει ένα-ένα frames (stop-and-wait στο L2 ACK).
- **BUF_FULL:** αν ο buffer του γονιού είναι γεμάτος, το παιδί κρατά τα frames για τον επόμενο κύκλο (το firmware το ανακοινώνει στο beacon).
- **Τελική αποτυχία MAC:** το frame μένει στον buffer και ξαναδοκιμάζεται μετά από `Uniform(0, J)`.
- **Τέλος παραθύρου** (start + SLOT): ύπνος. Ό,τι έμεινε περιμένει τον επόμενο κύκλο.
- **Ρολόγια:** κάθε ζεύγος προθερμαίνεται 64 κύκλους (`des.clock_burnin`), ώστε να ξεκινά σε σταθερή κατάσταση.
- **Γέφυρα:** ουρά RX (40) → UART → Pi → (T2) γραμμή ACK. Κάθε στάδιο είναι FIFO με έναν server.

Πηγές: μεθοδολογία discrete-event προσομοίωσης [19, 28].

**Γνωστές απλουστεύσεις του DES:**
- Το δέντρο είναι στατικό: δεν υπάρχει αλλαγή γονιού κατά το run (αντισταθμίζεται με το `parent_candidates`).
- Τα ACK της γέφυρας δεν περνούν από το κανάλι. Τα L2 ACK δεν αλλοιώνονται.
- Δεν προσομοιώνονται trickle beacons και RX_OPEN beacons (~0,6 % κατάληψη). Ο calculator τα μετρά στο channel_util.

---

## 16. Τι είναι μετρημένο και τι όχι

| Μέγεθος | Κατάσταση | Επίδραση |
|---|---|---|
| Bias ρολογιού 0,17 % | μετρημένο (1 ζεύγος) | G_max |
| Step περιπλάνησης, κλιμάκωση ∝ T | εκτίμηση / προέκταση | **καθορίζει αν 15′ ή 30′** |
| Χρόνος Pi ανά frame (20 ms) | μη μετρημένο | ρυθμός γέφυρας, ενέργεια rank 1 |
| MAC retry limit (5) | μη τεκμηριωμένο | E[απόπειρες] |
| Radio init 100 ms, soil settle 0,1 s | μη μετρημένα | ενέργεια ξυπνήματος |
| t_proc ανά hop 2 ms | μοντέλο | t_frame (54 % του χρόνου ανά frame) |
| Sleep floor 55 µA | εκτίμηση repo | ~50 mAs/κύκλο |
| RTC χρήση 3848 B | μετρημένο (ELF) | B_max = 121 |
| Ρεύματα chip | datasheet | όλη η ενέργεια |

Οι τρεις μετρήσεις που θα έκλειναν τις μεγαλύτερες αβεβαιότητες:
1. drift σε T = 1800 s (Gate 0 run 2)·
2. χρόνος Pi ανά frame·
3. ρεύμα ύπνου με PPK2.

---

## 17. Firmware κόμβου, ESP32-C3 και αισθητήρες: πώς δουλεύουν

Αυτή η ενότητα εξηγεί *τι κάνει το firmware* πάνω στο οποίο στηρίζεται το μοντέλο. Αρχεία:
- `firmware/libraries/GreenhouseMesh/*.h` (mesh)·
- `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino` (κόμβος)·
- `firmware/bridge_esp32/bridge_esp32.ino` (γέφυρα)·
- `pi/scripts/serial_bridge.py` (Pi).

### 17.1 ESP32-C3 και κύκλος ζωής ενός ξυπνήματος
- **Επεξεργαστής και ράδιο.** Το ESP32-C3 είναι RISC-V 160 MHz με ενσωματωμένο Wi-Fi 2,4 GHz [9, 10].
  Το firmware γράφεται στο Arduino core 3.3.11 [16], που τρέχει πάνω στο ESP-IDF (FreeRTOS).
- **Deep sleep** [14]. Σβήνουν CPU, κύρια RAM και ράδιο. Μένει μόνο το RTC domain (χρονομετρητής + RTC FAST μνήμη 8 KB). Ρεύμα chip ~5 µA [9].
  - Ξύπνημα με `esp_sleep_enable_timer_wakeup(T)` + `esp_deep_sleep_start()`.
  - **Κάθε ξύπνημα είναι πλήρες reboot**: ο κώδικας ξεκινά από το `setup()`. Γι' αυτό υπάρχει το κόστος boot (140–230 ms) σε κάθε κύκλο.
- **Μνήμη που επιβιώνει τον ύπνο.** Ό,τι δηλώνεται `RTC_DATA_ATTR` μπαίνει στη RTC FAST και επιβιώνει [14]:
  - γονιός, rank, κανάλι, buffer δικών μετρήσεων·
  - relay buffer (`meshRelayBuf`)·
  - κατάσταση του scheduler (`meshSched`: guard, ιστορικό αποκλίσεων, εκτίμηση).

  Γι' αυτό το μέγεθος του relay buffer το ορίζει η RTC μνήμη (§12) και όχι η κύρια RAM (400 KB).
- **Ρολόι ύπνου.** Στον deep sleep ο χρόνος μετριέται από τον εσωτερικό RC ταλαντωτή ~136 kHz [10, 13].
  Είναι φθηνός αλλά αποκλίνει με τη θερμοκρασία (το b και το step του §5). Γι' αυτό το CART δεν εμπιστεύεται το ρολόι: μαθαίνει την απόκλιση από κάθε επιτυχημένο ραντεβού.
  Εξωτερικός κρύσταλλος 32 kHz θα έριχνε το b κατά τάξεις μεγέθους. Είναι επιλογή hardware (`hw.rtc_clock`).

### 17.2 ESP-NOW
- **Τι είναι.** Πρωτόκολλο της Espressif πάνω στο 802.11: στέλνει δεδομένα μέσα σε *vendor-specific action frames* χωρίς σύνδεση σε access point [11].
  - Payload έως 250 B (v1), προεπιλεγμένος ρυθμός 1 Mbps.
  - Unicast: το ράδιο περιμένει **802.11 ACK** και κάνει αυτόματα retries. Broadcast: χωρίς ACK.
- **Send callback.** `ESP_NOW_SEND_SUCCESS` σημαίνει ότι ήρθε το L2 ACK του γείτονα, όχι ότι το μήνυμα έφτασε στο Pi.
  Πάνω σε αυτό στηρίζεται η **custody με L2 ACK** του CART: όταν ο γονιός κάνει ACK, το frame βρίσκεται στον RTC buffer του και το παιδί το σβήνει [27].
- **Buffers.** Το ESP-IDF έχει περιορισμένα Wi-Fi TX/RX buffers [12] (sdkconfig: 32 dynamic TX, 8 static + 32 dynamic RX).
  Ριπή πάνω από 32 frames χωρίς να περιμένει τα callbacks δίνει `ESP_ERR_ESPNOW_NO_MEM`. Γι' αυτό το firmware στέλνει stop-and-wait.
- **Κανάλι.** Όλο το δίκτυο είναι στο ίδιο κανάλι (`esp_wifi_set_channel`). Αν χαθεί η σύνδεση επί 2 ξυπνήματα, ο κόμβος ξανασκανάρει.
- **Retry limit.** Το όριο MAC retransmissions δεν τεκμηριώνεται από την Espressif. Το 5 είναι εκτίμηση της κοινότητας (§2, §16).

### 17.3 Mesh: rank, beacons, TTL (`mesh_node.h`)
- **Rank.** Η γέφυρα είναι rank 0. Κάθε κόμβος διαλέγει γονιό με **αυστηρά μικρότερο rank**, μετά το μικρότερο rank και μετά το καλύτερο RSSI. Το rank του είναι rank_γονιού + 1 (ίδια ιδέα με το RPL [20]).
  Μετά από 3 διαδοχικές αποτυχίες αποστολής αλλάζει γονιό.
- **Beacons.** Διαφημίζουν rank και διαθεσιμότητα με ρυθμό **trickle** [21]: 2 s → ×2 → 60 s, και reset σε κάθε αλλαγή. Έτσι σε σταθερό δίκτυο σχεδόν δεν κοστίζουν.
- **TTL.** Κάθε μήνυμα ξεκινά με TTL = rank + 2 (ως MAX_TTL) και κάθε relay το μειώνει κατά 1. Προστατεύει από βρόχους. Το MAX_TTL είναι το «τεχνητό» όριο βάθους του §6.
- **Dedup.** Δακτύλιος 32 θέσεων με παράθυρο 30 s. Στο Pi υπάρχει δεύτερο dedup ανά (MAC, seq).

### 17.4 CART depth N (`mesh_cart.h`, `mesh_sched.h`)
Στόχος: **όλοι οι κόμβοι κοιμούνται και όλοι κάνουν relay**, χωρίς κανέναν always-on κόμβο.
1. Ο κόμβος ξυπνά, ανοίγει το δικό του **παράθυρο λήψης** (SLOT) για τα παιδιά και ανάβει τους αισθητήρες μέσα σε αυτό.
   Όσο είναι ανοιχτό εκπέμπει RX_OPEN beacons κάθε 100 ms. Αν ο buffer του είναι γεμάτος, τα beacons λένε BUF_FULL.
2. Στο τέλος του παραθύρου ακούει για το RX_OPEN του γονιού. Ξύπνησε G/2 νωρίτερα από την εκτίμησή του (§5).
3. Μόλις το πιάσει, **ξανα-αγκυρώνει** το ρολόι του (η απόκλιση μπαίνει στο ιστορικό) και στέλνει με jitter, ένα-ένα, δικά του και ξένα frames.
4. Κοιμάται για T, υπολογισμένο ώστε το επόμενο παράθυρό του να πέσει ακριβώς πριν από του γονιού (σκάλα, όπως το DMAC [24]).
5. Αν χάσει 3 φορές τον γονιό, κάνει **orphan sweep**: ακούει έναν ολόκληρο κύκλο για να τον ξαναβρεί.

### 17.5 Κρυπτογραφία (`mesh_crypto.h`)
- **Δύο κλειδιά, με mbedTLS** [29] και τον hardware AES του C3 [10]:
  - **NetKey**: AES-CMAC [37, 39] πάνω στο header, κομμένο στα 8 B (nettag). Κάθε relay ελέγχει ότι το frame ανήκει στο δίκτυο χωρίς να βλέπει τα δεδομένα.
  - **AppKey**: AES-GCM [38] στο σώμα (δεδομένα αισθητήρων), με tag 16 B. Μόνο το Pi το αποκρυπτογραφεί.
- **Γιατί μετρά στο μοντέλο.** Κάθε hop κάνει έναν έλεγχο CMAC, και αυτό περιέχεται στο `radio.hop_proc_s` (2 ms, εκτίμηση).
  Επιπλέον τα 24 B tags κάνουν το πακέτο 61 B αντί για 37 B, δηλαδή +192 µs airtime.

### 17.6 Αισθητήρες και μετρήσεις (`edge_node_esp32_c3.ino`)
- **Τροφοδοσία με GPIO.** Οι αισθητήρες τροφοδοτούνται από pins (DHT → GPIO5, soil → GPIO4) και σβήνουν πριν από τον ύπνο.
  Έτσι δεν καταναλώνουν τίποτα στον ύπνο, αλλά χρειάζονται χρόνο σταθεροποίησης σε κάθε ξύπνημα.
- **DHT22 / AM2302** [30], μέσω Adafruit DHT sensor library 1.4.7 [33].
  - Σειριακό πρωτόκολλο ενός σύρματος (40 bits: 16 υγρασία + 16 θερμοκρασία + 8 checksum). Ακρίβεια ~±0,5 °C και ±2 %RH (έως ±5 %).
  - Το datasheet ζητά ≥ 1 s μετά την τροφοδοσία και ελάχιστη περίοδο μέτρησης 2 s. Γι' αυτό `SENSOR_WARMUP_MS = 2000`.
    Αυτά τα 2 s είναι το 80 % του ξύπνιου χρόνου σήμερα, και εξηγούν γιατί ο SHT40 κερδίζει τόσο.
- **SHT40** [31] (προτεινόμενος). I²C, ±0,2 °C και ±1,8 %RH τυπικά. Ανάβει σε ≤ 1 ms και μετρά σε ≤ 8,3 ms με ~320 µA, άρα περίπου 200× λιγότερο χρόνο από τον DHT22.
  Ο BME280 [32] είναι εναλλακτική με βαρομετρική πίεση.
- **Υγρασία εδάφους.** Capacitive sensor v1.2 (χωρίς επίσημο datasheet), αναλογική έξοδος στο ADC1_CH1 (GPIO1).
  - Γραμμική βαθμονόμηση: ποσοστό = (3163 − raw) / (3163 − 1529) · 100, με ξηρό = 3163 και νερό = 1529 counts.
  - Το GPIO2 αποφεύγεται: είναι strapping pin με pull-up σε κάποιες πλακέτες.
- **Μπαταρία.** Διαιρέτης 220 k/220 k στο GPIO3, με ρεύμα 7,5 µA πάντα ανοιχτό.
  - Η μέτρηση είναι ο μέσος όρος 8 δειγμάτων `analogReadMilliVolts` (με eFuse calibration του ADC [15]), × 2.
  - Κάτω από 2000 mV αναφέρεται ως 0 («δεν μετρήθηκε»).
  - Ο switched divider (`hw.divider`) μηδενίζει αυτά τα 7,5 µA.

### 17.7 Γέφυρα και Pi
- Η γέφυρα (ESP32-C3, πάντα ξύπνια, με ρεύμα από το Pi) γράφει κάθε frame ως γραμμή JSON με hex στο UART, στα 115200 8N1.
- Το Pi (`serial_bridge.py`):
  - αποκρυπτογραφεί (AES-GCM)·
  - κάνει dedup·
  - δημοσιεύει στο MQTT·
  - στέλνει ACK με TTL = min(ACK_TTL_MAX, rank + 2).
- Ο χρόνος επεξεργασίας ανά frame **δεν έχει μετρηθεί** (§16).

Πηγές §17: [9–16, 20, 21, 24, 27, 29–33, 37–39] και τα εσωτερικά έγγραφα Ε1–Ε6.

---

## 18. Φυσικό περιβάλλον και καθυστερήσεις: τι λαμβάνεται υπόψη και τι όχι

### 18.1 Διάδοση μέσα σε θερμοκήπιο

| Φαινόμενο | Στο μοντέλο σήμερα | Πόσο μετρά |
|---|---|---|
| Απόσταση (path loss) | **Όχι ρητά.** Κάθε link = ευαισθησία + 10 dB (`radio.link_margin_db`) | Ορίζει πόσα μέτρα είναι ένα hop |
| Φυτά ανάμεσα (όχι LOS) | **Όχι ρητά.** Περιέχονται μόνο έμμεσα στο περιθώριο 10 dB και στο shadowing σ = 4 dB | 3–17 dB για 5–50 m φυλλώματος [41, 40]· βρεγμένα φύλλα: μερικά dB παραπάνω |
| Έδαφος / ύψος κεραίας (two-ray, Fresnel) | Όχι | Κεραίες στα 0,5 m: μετά τα ~8 m η απώλεια αυξάνει με 40·log d αντί για 20·log d [3]· 1η ζώνη Fresnel 0,4–1,25 m για 5–50 m [43] |
| Shadowing (εμπόδια, μεταλλικός σκελετός, σωλήνες) | Ναι: log-normal σ = 4 dB, σταθερό ανά link | — |
| Γρήγορο fading ανά πλαίσιο (multipath, φύλλα που κινούνται) | Όχι | Χάνονται μεμονωμένα πλαίσια· τα διορθώνει το MAC retry |
| Υγρασία / αέρας (απορρόφηση αερίων) | Όχι | **Αμελητέα**: ~0,01 dB/km στα 2,4 GHz [42], δηλαδή 0,0005 dB σε 50 m. Η υγρασία μετρά μόνο μέσω των βρεγμένων φύλλων και της συμπύκνωσης πάνω στις κεραίες |
| Κεραία SuperMini (chip, κοντά σε PCB/μπαταρία) | Όχι | Υπόθεση ~ −3 dBi ανά άκρο· δεν έχει μετρηθεί |

**Εκτίμηση link budget:**
- Προϋποθέσεις: TX 20 dBm, ευαισθησία −98,4 dBm → 118,4 dB. Κεραίες −3 dBi/άκρο. Ύψος 0,5 m, two-ray. Φύλλωμα σε όλο το μήκος (Weissberger [41]).

| Απόσταση | Path loss | Φύλλωμα | Περιθώριο |
|---|---|---|---|
| 10 m | 62,0 | 5,8 | 44,6 dB |
| 20 m | 74,0 | 9,9 | 28,4 dB |
| 30 m | 81,1 | 12,6 | 18,7 dB |
| 50 m | 89,9 | 17,0 | 5,4 dB |

Άρα το «+10 dB» του μοντέλου αντιστοιχεί περίπου σε **hop 35–40 m** μέσα σε φυτά, και λιγότερο με βρεγμένα φύλλα.

**Συνέπεια για «έναν αισθητήρα ανά φυτό».** Το layered μοντέλο υποθέτει ότι κάθε κόμβος ακούει 3·W γείτονες (150 στο 100×50).
Με hop ~30 m και πυκνότητα 1–2 φυτών/m², ένας κόμβος θα άκουγε **χιλιάδες** γείτονες.
Άρα, με ένα κανάλι και μία γέφυρα, το 100×50 = 5000 αντιστοιχεί σε θερμοκήπιο βάθους ~3 km με ~1 αισθητήρα ανά 50–100 m², όχι σε έναν ανά φυτό.

Για πυκνότητα «ανά φυτό» χρειάζεται:
- γεωμετρικό μοντέλο (θέσεις + απόσταση + φύλλωμα)·
- περισσότερες γέφυρες ή κανάλια, ώστε κάθε γέφυρα να εξυπηρετεί μια ζώνη·
- ή cluster: ένας κόμβος ράδιο ανά ομάδα φυτών, με πολλούς αισθητήρες καλωδιωμένους πάνω του.

### 18.2 Καθυστερήσεις hardware, ρεύματος, διάδοσης

| Καθυστέρηση | Στο μοντέλο | Τιμή / σημείωση |
|---|---|---|
| Boot από deep sleep | Ναι | 200 ms (repo: 140–230 ms) |
| Εκκίνηση ράδιο + RF calibration | Ναι, μία παράμετρος | 100 ms, **μη μετρημένο** (`timing.radio_init_s`) |
| Warm-up / ανάγνωση αισθητήρων | Ναι | DHT22 2 s· SHT40 1 + 8,3 ms· soil 0,1 s (μη μετρημένο) |
| ADC μπαταρίας | Ναι | 8 × 2 ms |
| Επεξεργασία ανά hop (CMAC, callback, task switch) | Ναι | 2 ms ανά frame (εκτίμηση)· περιέχει τη διαδρομή λογισμικού `esp_now_send` → αέρας |
| MAC: DIFS, backoff, SIFS, ACK, retries | Ναι, ακριβώς (§2) | — |
| RX↔TX turnaround, CCA | Έμμεσα, μέσα στα SIFS/slot του προτύπου | ≤ 5 µs / ≤ 15 µs [1] |
| UART, Pi, γραμμή ACK | Ναι (§7) | Pi 20 ms μη μετρημένο |
| **Διάδοση σήματος (ταχύτητα φωτός)** | Όχι ρητά | d/c = 3,3 ns/m: 100 ns σε 30 m, **33 µs σε 100 hops συνολικά**. Το 802.11 την περιέχει ήδη στο slot των 20 µs (aAirPropagationTime ≤ 1 µs [1]). Είναι 10 000× μικρότερη από τον θόρυβο συγχρονισμού (20 ms), άρα αμελητέα |
| Πτώση τάσης μπαταρίας στο TX (335 mA) | Όχι | LiFePO4 18650 με εσωτερική αντίσταση δεκάδων mΩ: ~10–30 mV, αμελητέο για brown-out [34] |
| Θερμοκρασία → χωρητικότητα μπαταρίας | Όχι | LiFePO4 κοντά στους 0 °C: αισθητά λιγότερη διαθέσιμη χωρητικότητα [34]· χειμερινές νύχτες θερμοκηπίου |
| Θερμοκρασία → ρολόι RC | Ναι, έμμεσα (b, step, §5) | Η κλιμάκωση με T είναι προέκταση |
| Γήρανση μπαταρίας, απόδοση φορτιστή με θερμοκρασία | Όχι | — |

Πηγές §18: [1, 3, 34, 40–43].

---

## 19. Βιβλιογραφία

**Πρότυπα, θεωρία επικοινωνιών και δικτύων**

1. IEEE Std 802.11-2020, *IEEE Standard for Information Technology — Part 11: Wireless LAN Medium Access Control (MAC) and Physical Layer (PHY) Specifications*, IEEE, 2021. Clause 10: MAC/DCF· Clause 15: DSSS PHY (1–2 Mbps: PLCP, SIFS, slot, CW, ευαισθησία με FER 8 % σε 1024 B)· Clause 16: HR/DSSS.
2. J. G. Proakis, M. Salehi, *Digital Communications*, 5th ed., McGraw-Hill, 2008. BER του DBPSK: ½·e^(−Eb/N0).
3. T. S. Rappaport, *Wireless Communications: Principles and Practice*, 2nd ed., Prentice Hall, 2002, κεφ. 4. Log-distance path loss, log-normal shadowing.
4. N. Abramson, "The ALOHA System — Another Alternative for Computer Communications," *Proc. AFIPS Fall Joint Computer Conf.*, 1970, pp. 281–285.
5. G. Bianchi, "Performance Analysis of the IEEE 802.11 Distributed Coordination Function," *IEEE J. Sel. Areas Commun.*, 18(3):535–547, 2000.
6. L. Kleinrock, F. A. Tobagi, "Packet Switching in Radio Channels: Part I — Carrier Sense Multiple-Access Modes and Their Throughput-Delay Characteristics," *IEEE Trans. Commun.*, 23(12):1400–1416, 1975.
7. F. A. Tobagi, L. Kleinrock, "Packet Switching in Radio Channels: Part II — The Hidden Terminal Problem in Carrier Sense Multiple-Access and the Busy-Tone Solution," *IEEE Trans. Commun.*, 23(12):1417–1433, 1975.
8. A. Goldsmith, *Wireless Communications*, Cambridge University Press, 2005. Από BER σε PER, fading.

**ESP32-C3, ESP-IDF, Arduino**

9. Espressif Systems, *ESP32-C3 Series Datasheet*. Πίνακες ρευμάτων (TX 335 mA, RX 84 mA, modem-sleep, light/deep sleep) και RF (ευαισθησία −98,4 dBm @ 1 Mbps). https://www.espressif.com/sites/default/files/documentation/esp32-c3_datasheet_en.pdf
10. Espressif Systems, *ESP32-C3 Technical Reference Manual*. RTC/low-power management, RTC FAST memory, πηγές ρολογιού, ADC, AES accelerator. https://www.espressif.com/sites/default/files/documentation/esp32-c3_technical_reference_manual_en.pdf
11. Espressif, *ESP-IDF Programming Guide — ESP-NOW* (μορφή πλαισίου, payload, send callback, ρυθμός). https://docs.espressif.com/projects/esp-idf/en/stable/esp32c3/api-reference/network/esp_now.html
12. Espressif, *ESP-IDF Programming Guide — Wi-Fi Driver* (TX/RX buffers). https://docs.espressif.com/projects/esp-idf/en/stable/esp32c3/api-guides/wifi.html
13. Espressif, *ESP-IDF Programming Guide — System Time* (πηγές ρολογιού RTC, RC 136 kHz, ακρίβεια). https://docs.espressif.com/projects/esp-idf/en/stable/esp32c3/api-reference/system/system_time.html
14. Espressif, *ESP-IDF Programming Guide — Sleep Modes* (deep sleep, timer wakeup, RTC memory / `RTC_DATA_ATTR`). https://docs.espressif.com/projects/esp-idf/en/stable/esp32c3/api-reference/system/sleep_modes.html
15. Espressif, *ESP-IDF Programming Guide — ADC Calibration Driver*. https://docs.espressif.com/projects/esp-idf/en/stable/esp32c3/api-reference/peripherals/adc_calibration.html
16. Espressif, *Arduino core for the ESP32* (έκδοση 3.3.11 στο build μας: sdkconfig, HardwareSerial). https://github.com/espressif/arduino-esp32

**Στοχαστικά μοντέλα, ουρές, προσομοίωση**

17. G. E. P. Box, G. M. Jenkins, G. C. Reinsel, G. M. Ljung, *Time Series Analysis: Forecasting and Control*, 5th ed., Wiley, 2015. AR(1), στάσιμη διασπορά σ²/(1−ρ²), EWMA.
18. D. Gross, J. F. Shortle, J. M. Thompson, C. M. Harris, *Fundamentals of Queueing Theory*, 4th ed., Wiley, 2008. M/D/1/K, embedded Markov chain.
19. A. M. Law, *Simulation Modeling and Analysis*, 5th ed., McGraw-Hill, 2015. Discrete-event simulation, steady state, διαστήματα εμπιστοσύνης.

**Δίκτυα αισθητήρων: routing, MAC, συγχρονισμός**

20. T. Winter et al., "RPL: IPv6 Routing Protocol for Low-Power and Lossy Networks," RFC 6550, IETF, 2012 (έννοια rank).
21. P. Levis, T. Clausen, J. Hui, O. Gnawali, J. Ko, "The Trickle Algorithm," RFC 6206, IETF, 2011· και P. Levis, N. Patel, D. Culler, S. Shenker, "Trickle: A Self-Regulating Algorithm for Code Propagation and Maintenance in Wireless Sensor Networks," *USENIX NSDI*, 2004.
22. J. Elson, L. Girod, D. Estrin, "Fine-Grained Network Time Synchronization Using Reference Broadcasts," *USENIX OSDI*, 2002.
23. M. Maróti, B. Kusy, G. Simon, Á. Lédeczi, "The Flooding Time Synchronization Protocol," *ACM SenSys*, 2004.
24. G. Lu, B. Krishnamachari, C. S. Raghavendra, "An Adaptive Energy-Efficient and Low-Latency MAC for Data Gathering in Wireless Sensor Networks," *Proc. IPDPS (WMAN workshop)*, 2004 (DMAC: κλιμακωτό ξύπνημα κατά μήκος του δέντρου, η ιδέα της σκάλας).
25. W. Ye, J. Heidemann, D. Estrin, "An Energy-Efficient MAC Protocol for Wireless Sensor Networks," *IEEE INFOCOM*, 2002 (S-MAC).
26. J. Polastre, J. Hill, D. Culler, "Versatile Low Power Media Access for Wireless Sensor Networks," *ACM SenSys*, 2004 (B-MAC, low-power listening).
27. K. Scott, S. Burleigh, "Bundle Protocol Specification," RFC 5050, IETF, 2007 (custody transfer: store-and-forward με παράδοση ευθύνης ανά hop).
28. J. Banks, J. S. Carson II, B. L. Nelson, D. M. Nicol, *Discrete-Event System Simulation*, 5th ed., Pearson, 2010.

**Κρυπτογραφία**

29. Mbed TLS (Trusted Firmware), `mbedtls/cmac.h`, `mbedtls/gcm.h`. https://github.com/Mbed-TLS/mbedtls
37. J. H. Song, R. Poovendran, J. Lee, T. Iwata, "The AES-CMAC Algorithm," RFC 4493, IETF, 2006.
38. M. Dworkin, *Recommendation for Block Cipher Modes of Operation: Galois/Counter Mode (GCM) and GMAC*, NIST SP 800-38D, 2007.
39. M. Dworkin, *Recommendation for Block Cipher Modes of Operation: The CMAC Mode for Authentication*, NIST SP 800-38B, 2005 (αναθ. 2016).

**Αισθητήρες, μπαταρία, ηλιακό**

30. Aosong Electronics, *Digital-Output Relative Humidity & Temperature Sensor/Module AM2302 (DHT22)*, datasheet.
31. Sensirion, *SHT4x Datasheet*, v6.4, Nov. 2023 (Πίνακες 3/4: χρόνοι, ρεύματα, ακρίβεια).
32. Bosch Sensortec, *BME280 Combined Humidity and Pressure Sensor*, datasheet BST-BME280-DS001, rev. 1.23.
33. Adafruit, *DHT sensor library* v1.4.7. https://github.com/adafruit/DHT-sensor-library
34. T. B. Reddy (ed.), *Linden's Handbook of Batteries*, 4th ed., McGraw-Hill, 2011 (LiFePO4: τάσεις, βάθος εκφόρτισης, αυτοεκφόρτιση).
35. NanJing Top Power ASIC Corp., *TP5000: 2 A switch-mode charger for single-cell Li-ion / LiFePO4*, datasheet.
36. European Commission, Joint Research Centre, *PVGIS — Photovoltaic Geographical Information System*. https://re.jrc.ec.europa.eu/pvg_tools/

**Διάδοση σε βλάστηση και ατμόσφαιρα**

40. ITU-R Recommendation P.833, *Attenuation in vegetation*, International Telecommunication Union (τελευταία έκδοση).
41. M. A. Weissberger, *An Initial Critical Summary of Models for Predicting the Attenuation of Radio Waves by Trees*, Report ESD-TR-81-101, Electromagnetic Compatibility Analysis Center, Annapolis MD, 1982 (modified exponential decay model).
42. ITU-R Recommendation P.676, *Attenuation by atmospheric gases and related effects*, ITU (τελευταία έκδοση).
43. ITU-R Recommendation P.526, *Propagation by diffraction* (ζώνες Fresnel), ITU (τελευταία έκδοση).

**Εσωτερικά έγγραφα του repo**

- Ε1. `docs/SENSOR_NODE_POWER_AND_SOLAR.md`: προϋπολογισμός ενέργειας, μπαταρία, ηλιακό (πηγή πολλών τιμών «repo-doc»).
- Ε2. `docs/EDGE_NODE_POWER_OPTIMIZATION.md`: μετρήσεις/εκτιμήσεις ρεύματος πλακέτας.
- Ε3. `docs/superpowers/specs/2026-09-23-cart-v2-revision.md`: CART v2, κανόνας G_max, πολιτική margin, Gate 0 (μέτρηση bias).
- Ε4. `docs/superpowers/specs/2026-09-28-cart-depth-n-design.md`: CART depth N (σκάλα, custody με L2 ACK, BUF_FULL).
- Ε5. `docs/analysis/cart_sim.py`, `cart_sim_results.txt`: αρχικό μοντέλο ζεύγους ρολογιών (αναπαράγεται ακριβώς, με test).
- Ε6. `docs/superpowers/specs/2026-09-28-mesh-simulator-design.md`: spec του simulator.

Σημείωση: οι σελίδες ESP-IDF που δίνονται («stable») αλλάζουν με τις εκδόσεις. Οι τιμές του μοντέλου έχουν διαβαστεί από το sdkconfig του core 3.3.11 που χρησιμοποιούμε, όχι από την τεκμηρίωση.

---

## Παράρτημα Α: από πού βγαίνει κάθε τύπος

**Α1. Airtime (§2).** Στο 1 Mbps κάθε bit διαρκεί 1 µs. Πριν από κάθε πλαίσιο προηγείται το PLCP: preamble 144 bits + header 48 bits = 192 µs, με long preamble υποχρεωτικό στο 1 Mbps [1].
Άρα χρόνος = 192 + 8·(bytes πλαισίου), με bytes πλαισίου = 43 (ESP-NOW) + L [11].

**Α2. Αναμενόμενες απόπειρες (§2).** Η απόπειρα i+1 γίνεται μόνο αν απέτυχαν και οι i προηγούμενες, με πιθανότητα pⁱ. Άρα:
```
E[απόπειρες] = Σ_{i=0}^{r} pⁱ = (1 − p^(r+1)) / (1 − p)          (γεωμετρική σειρά)
```
Το frame χάνεται μόνο αν αποτύχουν και οι r+1: p^(r+1).

**Α3. DBPSK και PER (§3).**
- Για διαφορική BPSK με βέλτιστο φωρατή, `P_b = ½·exp(−E_b/N_0)` [2].
- Η ευαισθησία του datasheet [9] ορίζεται [1] ως η ισχύς όπου FER = 8 % για πλαίσιο 1024 B. Λύνοντας `1 − (1 − BER)^8192 = 0,08` βγαίνει το BER_s.
- Αντιστρέφοντας τον τύπο του DBPSK βρίσκουμε το E_b/N_0 που αντιστοιχεί στα −98,4 dBm.
  Έτσι ενσωματώνονται αυτόματα το noise figure του δέκτη και το κέρδος του Barker spreading, χωρίς να χρειάζεται να τα ξέρουμε.
- Κάθε dB πάνω από την ευαισθησία είναι ένα dB πάνω στο E_b/N_0.
- `PER = 1 − (1 − BER)^bits` υποθέτει ανεξάρτητα λάθη bit, που είναι λογικό χωρίς κωδικοποίηση καναλιού [8].
- Επειδή το BER πέφτει εκθετικά, το PER πέφτει από 0,8 % σε 10⁻⁷ μέσα σε 3 dB. Γι' αυτό τα links είναι σχεδόν «ναι/όχι».

**Α4. Shadowing και καλύτερος γονιός (§3).**
- Ισχύει `PL(d) = PL(d₀) + 10·n·log₁₀(d/d₀) + X_σ`, με X_σ κανονική σε dB [3]. Ο DES δεν μοντελοποιεί αποστάσεις: βάζει το μέσο link στα +10 dB και προσθέτει X_σ.
- Το μέγιστο 3 ανεξάρτητων N(0,1) έχει μέση τιμή 3/(2√π) ≈ 0,85. Άρα το link προς τον γονιό είναι κατά μέσο όρο +3,4 dB καλύτερο (σ = 4 dB), με πολύ λεπτότερη κάτω ουρά.
  Αυτό αντιστοιχεί στο ότι το firmware διαλέγει τον γονιό με το καλύτερο RSSI.

**Α5. ALOHA / επικάλυψη μέσα στο jitter (§4).**
- Δύο πλαίσια διάρκειας t με ανεξάρτητες ομοιόμορφες αρχές μέσα σε διάστημα J επικαλύπτονται όταν |Δ| < t.
- Για x = t/J ≤ 1: `P(|Δ| < t) = 1 − (1 − x)² = 2x − x²`.
- Στο κλασικό ALOHA με Poisson φορτίο G το «ευάλωτο διάστημα» είναι 2t, άρα `P(επιτυχία) = e^(−2G)` [4].

**Α6. Σύγκρουση στο πρώτο slot (§4).**
- c σταθμοί διαλέγουν ανεξάρτητα backoff ομοιόμορφα σε CW slots. Νικά ο μικρότερος, και υπάρχει σύγκρουση αν το ελάχιστο δεν είναι μοναδικό.
- `P(μοναδικό ελάχιστο στο m) = c · (1/CW) · ((CW−1−m)/CW)^(c−1)`: ένας διαλέγει m και οι άλλοι c−1 κάτι > m.
- Αθροίζοντας για m = 0..CW−1 παίρνουμε P(μοναδικό), και η σύγκρουση είναι το συμπλήρωμα [5].

**Α7. AR(1) περιπλάνηση ρολογιού (§5).**
- `w_n = ρ·w_{n−1} + ε_n` με Var(ε) = s². Στη στάσιμη κατάσταση Var(w) = ρ²·Var(w) + s², άρα **Var(w) = s²/(1−ρ²)** [17].
- Η απόκλιση χρόνου σε έναν κύκλο είναι T·w, άρα σ = T·s/√(1−ρ²).
- Με ρ = 0,98, √(1−ρ²) = 0,199: η περιπλάνηση είναι ~5× μεγαλύτερη από ένα βήμα.
- Αν το s ∝ T (υπόθεση του Ε5), τότε σ ∝ T². Διπλασιάζοντας τον κύκλο τετραπλασιάζεται η αβεβαιότητα, και γι' αυτό το 30′ βγαίνει χειρότερο.

**Α8. Πολιτική guard (§5).**
- Το παράθυρο G πρέπει να καλύπτει το σφάλμα πρόβλεψης. Κρατώντας το max|err| των 16 τελευταίων επιτυχιών και πολλαπλασιάζοντάς το με k, το παράθυρο μεγαλώνει ανάλογα με το πόσο «χορεύει» το ρολόι.
  Είναι εμπειρικός κανόνας, ελεγμένος με Monte Carlo στο Ε5.
- Σε αποτυχία το G διπλασιάζεται, όπως το binary exponential backoff [1, 5]. Έτσι το εύρος αναζήτησης καλύπτει γρήγορα κάθε απόκλιση ως το G_max.
- Η εκτίμηση της θέσης του γονιού ανανεώνεται με EWMA (βάρος 0,3 στη νέα μέτρηση) [17]. Εξομαλύνει τον θόρυβο των 20 ms χωρίς να αργεί να ακολουθήσει το bias.
- **Γιατί η αξιοπιστία πέφτει με το βάθος:** κάθε hop έχει δικό του ζεύγος ρολογιών. Αν ανά hop P(miss) = q, τότε `P(σε ώρα από βάθος r) ≈ (1−q)^(r−1)`.
  Με q = 1,3 % (k 1,5) και r = 100: 0,987⁹⁹ ≈ 0,27 για τον χειρότερο, και μέσος όρος σε όλα τα ranks 56 %.

**Α9. Ενέργεια (§9–10).**
- Φορτίο = ∫ I dt, άρα για κάθε κατάσταση mAs = διάρκεια · ρεύμα.
- Ανά ημέρα: (86400/T_c) κύκλοι, και 3600 mAs = 1 mAh.
- Ηλιακό: P·PSH δίνει Wh/ημέρα στο πάνελ. Επί απώλειες (derate) και απόδοση φορτιστή [35], διά την τάση μπαταρίας, σε mAh [34, 36].
- Αυτονομία = διαθέσιμο φορτίο / ημερήσια κατανάλωση.

**Α10. Ουρά M/D/1/K (§7).**
- Αφίξεις Poisson με ρυθμό λ, σταθερός χρόνος εξυπηρέτησης 1/μ, χωρητικότητα K, ρ = λ/μ.
- Η αλυσίδα Markov στις στιγμές αναχώρησης έχει πιθανότητες μετάβασης a_j = P(j αφίξεις σε χρόνο 1/μ) = e^(−ρ)·ρ^j/j!.
- Από την κατανομή της προκύπτει η πιθανότητα απόρριψης `P_block = 1 − 1/(π₀ + ρ)` [18].
- Χρησιμοποιείται για να ελεγχθεί ότι ο DES αναπαράγει τη θεωρία στην ουρά της γέφυρας.

**Α11. Σκάλα (§8.2).**
- Αν κάθε rank ανοίγει παράθυρο ακριβώς πριν από τον γονιό του, ένα μήνυμα από βάθος R φτάνει στη γέφυρα σε R·SLOT μέσα στον **ίδιο** κύκλο.
  Αυτή είναι η ιδέα του DMAC [24]. Χωρίς σκάλα θα χρειαζόταν ένας κύκλος ανά hop, δηλαδή R·T.
- Το k_col μετρά πόσες άλλες στήλες «πέφτουν» στο ίδιο παράθυρο.
  Με τυχαίες φάσεις, μια στήλη που ακούμε (3 ranks) επικαλύπτει τα 2·SLOT γύρω από το δικό μας παράθυρο με πιθανότητα 2·SLOT·3/T_c, οπότε ο αναμενόμενος αριθμός είναι 1 + (W−1)·6·SLOT/T_c.
