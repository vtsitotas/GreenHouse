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

- **Ταβάνι βάθους: rank 65.** Με τους TTL κανόνες του firmware, στο σενάριο 50×10 **0 κόμβοι δεν παραδίδουν ποτέ**.
- **Flood ACK: 137.200 re-broadcasts/κύκλο** (firmware TTL)· 137.200 χωρίς όριο TTL — κλιμάκωση O(N²).
- **Γέφυρα:** γραμμή UART 150 B = 13.021 ms → **μέγιστο 76.8 frames/s**· ουρά εισόδου μόνο 40 frames.
- **Airtime (1 Mbps):** data 1024 µs, beacon 752 µs, ACK 696 µs· unicast με L2 ACK 1388 µs (+backoff → 1698 µs).
- **Relay buffer:** firmware 50 frames· χωράνε έως 121 (μνήμη ύπνου μετρημένη στο ELF: 3848/8192 B), χρειάζονται τουλάχιστον 50 (subtree rank-1 στο 50×10).
- **Ενέργεια Phase-1 leaf:** 7.26 mAh/day @15′, 4.38 mAh/day @30′.

## A1. Firmware — πακέτα και μηνύματα

| Παράμετρος | Τιμή | Τι είναι | Γιατί αυτή η τιμή | Πηγή | kind | Τεχνική σημείωση |
|---|---|---|---|---|---|---|
| `MESH_PACKET_LEN` | 61 B | Μέγεθος ενός μηνύματος μέτρησης στον αέρα. | Άθροισμα των τεσσάρων κομματιών του (16+8+21+16): τόσα χρειάζονται για διεύθυνση, έλεγχο και κρυπτογράφηση. | `firmware/libraries/GreenhouseMesh/mesh_packet.h:20` | firmware | sealed data frame = header + nettag + ciphertext + apptag |
| `MESH_HEADER_LEN` | 16 B | Η «ετικέτα» του μηνύματος: ποιος το έστειλε, αριθμός, rank, TTL. | Όσα bytes χρειάζονται αυτά τα πεδία, χωρίς περιθώριο σπατάλης. | `firmware/libraries/GreenhouseMesh/mesh_packet.h:16` | firmware | magic, origin MAC, seq, boot_count, flags, rank, ttl |
| `MESH_NETTAG_LEN` | 8 B | Σύντομη υπογραφή που αποδεικνύει ότι το μήνυμα είναι από το δίκτυό μας. | 8 bytes αρκούν για να απορρίπτει κάθε relay τα ξένα μηνύματα γρήγορα, χωρίς να τα αποκρυπτογραφεί. | `firmware/libraries/GreenhouseMesh/mesh_packet.h:17` | firmware | AES-CMAC(NetKey) truncated, ελέγχεται από κάθε relay |
| `MESH_BODY_LEN` | 21 B | Οι κρυπτογραφημένες μετρήσεις (θερμοκρασία, υγρασία, έδαφος, μπαταρία, parent, RSSI). | Τόσα bytes πιάνουν αυτές οι τιμές στη δυαδική τους μορφή. | `firmware/libraries/GreenhouseMesh/mesh_packet.h:18` | firmware | AES-GCM ciphertext: T, H, soil, battery_mv, parent MAC, RSSI |
| `MESH_APPTAG_LEN` | 16 B | Υπογραφή κρυπτογράφησης που ελέγχει μόνο το Pi. | 16 bytes είναι το πρότυπο μέγεθος για AES-GCM: κανείς δεν μπορεί να πλαστογραφήσει μέτρηση. | `firmware/libraries/GreenhouseMesh/mesh_packet.h:19` | firmware | GCM tag, ανοίγει μόνο το Pi |
| `MESH_AAD_LEN` | 15 B | Το κομμάτι της ετικέτας που προστατεύεται από αλλοίωση. | Όλη η ετικέτα εκτός από το TTL, που αλλάζει σε κάθε hop και άρα δεν μπορεί να υπογραφεί. | `firmware/libraries/GreenhouseMesh/mesh_packet.h:22` | firmware | το ttl (byte 15) εξαιρείται — αλλάζει σε κάθε hop |
| `sizeof(MeshBeacon)` | 27 B | Μέγεθος του beacon: το «είμαι εδώ, είμαι σε αυτό το rank». | Όσο χρειάζονται τα πεδία του (rank, διάστημα, σημαίες) + υπογραφή δικτύου. | `firmware/libraries/GreenhouseMesh/mesh_node.h:50` | firmware | broadcast, cleartext + nettag |
| `sizeof(MeshAck)` | 20 B | Μέγεθος της επιβεβαίωσης από το Pi προς τον αισθητήρα. | Διαφορετικό από όλα τα άλλα μηνύματα, ώστε ο κόμβος να καταλαβαίνει τον τύπο μόνο από το μήκος. | `firmware/libraries/GreenhouseMesh/mesh_node.h:91` | firmware | app-level ACK, flood broadcast |
| `sizeof(MeshJoinBeacon)` | 8 B | Μέγεθος του μηνύματος «θέλω να μπω στο δίκτυο» ενός καινούργιου αισθητήρα. | Ελάχιστο: μόνο η MAC του, αφού δεν έχει ακόμα κλειδιά. | `firmware/libraries/GreenhouseMesh/mesh_node.h:66` | firmware | μόνο μη-enrolled κόμβοι |
| `MESH_PROVISION_LEN` | 33 B | Μέγεθος του μηνύματος που δίνει στον νέο αισθητήρα τα κλειδιά του δικτύου. | Κλειδί 16 + σημαίες 1 + υπογραφή 16 bytes. | `firmware/libraries/GreenhouseMesh/mesh_crypto.h:14` | firmware | NetKey 16 + flags 1 + tag 16 |
| `BEACON_V3_LEN` | 29 B | Μέγεθος beacon στο αρχικό σχέδιο CART (με 2 επιπλέον πεδία). | Σχέδιο μόνο· η υλοποίηση βάθους N κράτησε τα 27 bytes και χρησιμοποιεί σημαίες. | `docs/superpowers/specs/2026-09-23-cart-v2-revision.md §3.1` | planned | CART: + sleepy_depth + guard_hint_q |

## A2. Firmware — routing, beacons, trickle

| Παράμετρος | Τιμή | Τι είναι | Γιατί αυτή η τιμή | Πηγή | kind | Τεχνική σημείωση |
|---|---|---|---|---|---|---|
| `MESH_BEACON_INTERVAL_MIN_MS` | 2000 ms | Πόσο συχνά στέλνει beacons ένας κόμβος όταν κάτι αλλάζει στο δίκτυο. | 2 s: γρήγορη ανακάλυψη γειτόνων όταν χρειάζεται, χωρίς να γεμίζει το κανάλι. | `firmware/libraries/GreenhouseMesh/mesh_config.h:33` | firmware | trickle floor (reset target) |
| `MESH_BEACON_INTERVAL_MAX_MS` | 60000 ms | Το αραιότερο διάστημα beacons όταν το δίκτυο είναι σταθερό. | 60 s: σε ηρεμία ξοδεύει ελάχιστο χρόνο εκπομπής (τεχνική Trickle). | `firmware/libraries/GreenhouseMesh/mesh_config.h:34` | firmware | trickle ceiling· κάθε beacon διπλασιάζει το διάστημα |
| `MESH_BRIDGE_BEACON_INTERVAL_MS` | 2000 ms | Πόσο συχνά στέλνει beacon η γέφυρα. | Η γέφυρα έχει ρεύμα από το Pi, οπότε μπορεί να στέλνει συνέχεια κάθε 2 s. | `firmware/libraries/GreenhouseMesh/mesh_config.h:35` | firmware | η γέφυρα δεν κάνει backoff |
| `MESH_PARENT_TIMEOUT_FACTOR` | 3 × | Πόσα χαμένα beacons του parent σημαίνουν ότι «χάθηκε». | 3: ένα ή δύο χαμένα είναι συνηθισμένα (θόρυβος), τρία στη σειρά όχι. | `firmware/libraries/GreenhouseMesh/mesh_config.h:37` | firmware | parent χάνεται μετά από 3× το advertised interval |
| `TX_FAIL_DROP_COUNT` | 3 tx | Πόσες συνεχόμενες αποτυχίες αποστολής κάνουν τον κόμβο να αλλάξει parent. | 3: γρηγορότερο από το timeout των beacons όταν ο parent έχει όντως πεθάνει. | `firmware/libraries/GreenhouseMesh/mesh_node.h:469` | firmware | διαδοχικές αποτυχίες unicast → drop parent |
| `MESH_ORPHAN_FRESH_MS` | 60000 ms | Πότε ένας κόμβος χωρίς parent θεωρείται «νέο ορφανό» που χρειάζεται βοήθεια. | 60 s σιωπής: για να μην αντιδρούν οι γείτονες στον ίδιο κόμβο ξανά και ξανά. | `firmware/libraries/GreenhouseMesh/mesh_config.h:45` | firmware | UNROUTED beacon από MAC σιωπηλή τόσο → νέο orphan |
| `MESH_ORPHAN_RESET_MIN_GAP_MS` | 10000 ms | Ελάχιστο διάστημα μεταξύ δύο «γρήγορων απαντήσεων» σε ορφανούς κόμβους. | 10 s: προστασία από κόμβο που αναβοσβήνει και θα έκαιγε μπαταρία στους γείτονες. | `firmware/libraries/GreenhouseMesh/mesh_config.h:47` | firmware | ≤1 orphan-triggered trickle reset ανά 10 s |
| `MESH_RESCAN_AFTER_MS` | 60000 ms | Μετά από πόσο χρόνο χωρίς parent ξαναελέγχει ο κόμβος το κανάλι. | 60 s: αρκετό για να βρει parent κανονικά, πριν υποθέσει ότι άλλαξε κάτι. | `firmware/libraries/GreenhouseMesh/mesh_config.h:42` | firmware | always-on unrouted → επιβεβαίωση καναλιού |
| `MESH_WINDOW_DURATION_MS` | 3000 ms | Πεδίο του beacon για το «παράθυρο αφύπνισης». | Κρατήθηκε για το μέλλον· στο CART βάθους N μεταφέρει πόσο είναι ανοιχτό το παράθυρο. | `firmware/libraries/GreenhouseMesh/mesh_config.h:39` | firmware | μεταφέρεται στο beacon, αχρησιμοποίητο σήμερα |
| `MESH_RANK_UNROUTED` | 255  | Ειδική τιμή rank που σημαίνει «δεν έχω parent». | 255 = η μεγαλύτερη τιμή ενός byte, ώστε να μην μπερδεύεται με πραγματικό rank. | `firmware/libraries/GreenhouseMesh/mesh_config.h:13` | firmware | sentinel: χωρίς parent |
| `MESH_NEIGHBOR_SLOTS` | 16 slots | Πόσους γείτονες θυμάται ο κόμβος. | 16 έφταναν για το bench· σε πυκνό δίκτυο (30 γείτονες) γεμίζει και καλό είναι να αυξηθεί. | `firmware/libraries/GreenhouseMesh/mesh_node.h:137` | firmware | LRU πίνακας γειτόνων (orphan detection) |
| `MESH_JOIN_BEACON_INTERVAL_MS` | 3000 ms | Πόσο συχνά φωνάζει «θέλω να μπω» ένας νέος αισθητήρας. | 3 s: ο χρήστης στέκεται δίπλα με την εφαρμογή, θέλουμε γρήγορη απάντηση. | `firmware/libraries/GreenhouseMesh/mesh_node.h:60` | firmware | μόνο μη-enrolled |
| `MESH_FIXED_CHANNEL` | 1  | Το κανάλι WiFi όπου μιλάνε όλοι οι κόμβοι. | Κανάλι 1: σταθερό για όλους, αφού δεν υπάρχει router να το ορίσει. | `firmware/libraries/GreenhouseMesh/mesh_config.h:59` | firmware | όλοι οι κόμβοι στο ίδιο κανάλι (2412 MHz) |
| `PARENT_SELECTION` | strict rank < own· μετά μικρότερο rank· μετά RSSI  | Ο κανόνας με τον οποίο διαλέγει ένας κόμβος σε ποιον να στέλνει. | Μόνο σε κόμβο πιο κοντά στη γέφυρα (μικρότερο rank)· έτσι δεν γίνονται ποτέ κύκλοι. | `firmware/libraries/GreenhouseMesh/mesh_node.h:374` | firmware | RPL strict-rank → δομικά χωρίς loops |
| `SLEEPY_PARENT_RULE` | sleepy parent δεκτός όταν το beacon έχει RX_OPEN και RELAY_CAP (CART depth N)  | Αν ένας κόμβος που κοιμάται μπορεί να γίνει parent. | Ναι, όταν έχει ανοιχτό παράθυρο λήψης και δέχεται παιδιά: έτσι όλοι οι κόμβοι κάνουν relay. | `firmware/libraries/GreenhouseMesh/mesh_node.h meshHandleBeacon()` | firmware | MESH_CART_ENABLE=0 επαναφέρει το Phase 1 |

## A3. Firmware — TTL

| Παράμετρος | Τιμή | Τι είναι | Γιατί αυτή η τιμή | Πηγή | kind | Τεχνική σημείωση |
|---|---|---|---|---|---|---|
| `MESH_TTL_MARGIN` | 2 hops | Πόσα επιπλέον hops επιτρέπονται πέρα από την απόσταση του κόμβου. | 2: καλύπτει αλλαγή parent την ώρα που το μήνυμα ταξιδεύει. | `firmware/libraries/GreenhouseMesh/mesh_config.h:14` | firmware | data ttl = rank + margin, στο transmit |
| `MESH_MAX_TTL` | 64 hops | Το ανώτατο όριο hops ενός μηνύματος. | 64: το παλιό 16 σταματούσε κάθε μήνυμα πέρα από rank 17· το 64 αφήνει δίκτυο έως ~63 επίπεδα και κρατά τη δικλίδα. | `firmware/libraries/GreenhouseMesh/mesh_config.h:19` | firmware | ανώτατο όριο· relay κάνει drop ttl>max ή ttl==0 |
| `MESH_ACK_TTL` | 6 hops | TTL της επιβεβαίωσης αν το Pi δεν στείλει δικό του. | 6: παλιά εφεδρική τιμή· το Pi στέλνει πάντα rank+2. | `firmware/libraries/GreenhouseMesh/mesh_node.h:77` | firmware | fallback της γέφυρας αν το Pi δεν στείλει ttl |
| `ACK_TTL_MARGIN` | 2 hops | Περιθώριο hops για την επιβεβαίωση που γυρίζει. | 2, ίδιο με τα δεδομένα, για τον ίδιο λόγο. | `pi/scripts/serial_bridge.py:469` | firmware | Pi: ACK ttl = min(ACK_TTL_MAX, rank + margin) |
| `ACK_TTL_MAX` | 64 hops | Ανώτατο όριο hops της επιβεβαίωσης στο Pi. | 64, ίδιο με το MESH_MAX_TTL· πρέπει να αλλάζουν μαζί. | `pi/scripts/serial_bridge.py:470` | firmware |  |

## A4. Firmware — buffers και μνήμη

| Παράμετρος | Τιμή | Τι είναι | Γιατί αυτή η τιμή | Πηγή | kind | Τεχνική σημείωση |
|---|---|---|---|---|---|---|
| `MESH_DATA_BUFFER_SIZE` | 10 frames | Πόσες δικές του μετρήσεις κρατά ένας κόμβος όταν δεν μπορεί να στείλει. | 10: αρκούν για μερικούς κύκλους χωρίς σύνδεση και χωράνε άνετα στη μνήμη που επιβιώνει τον ύπνο. | `firmware/libraries/GreenhouseMesh/mesh_config.h:116` | firmware | δικές του μετρήσεις, ring drop-oldest, σε RTC |
| `MESH_INFLIGHT_MAX` | 11 frames | Πόσα μηνύματα περιμένουν επιβεβαίωση σε μία αφύπνιση. | Όλος ο buffer (10) + η νέα μέτρηση = 11. | `firmware/libraries/GreenhouseMesh/mesh_inflight.h:22` | firmware | frames που περιμένουν app-ACK σε ένα wake |
| `MESH_DEDUP_CACHE_SIZE` | 32 entries | Πόσα πρόσφατα μηνύματα θυμάται ένας relay για να πετάει τα διπλά. | 32 έφταναν για λίγους κόμβους· κοντά στη γέφυρα σε μεγάλο δίκτυο θέλει περισσότερα. | `firmware/libraries/GreenhouseMesh/mesh_config.h:103` | firmware | (origin, seq) ring σε κάθε relay και στη γέφυρα |
| `MESH_DEDUP_WINDOW_MS` | 30000 ms | Για πόσο θεωρείται ένα μήνυμα «ήδη ειδωμένο». | 30 s: μεγαλύτερο από μία αφύπνιση (10 s) και μικρότερο από τον κύκλο ύπνου. | `firmware/libraries/GreenhouseMesh/mesh_config.h:104` | firmware | static_assert: MAX_AWAKE < window < SLEEP_INTERVAL |
| `MESH_ACK_DEDUP_CACHE_SIZE` | 8 entries | Πόσες πρόσφατες επιβεβαιώσεις θυμάται ο κόμβος. | 8: οι επιβεβαιώσεις είναι λίγες ανά αφύπνιση. | `firmware/libraries/GreenhouseMesh/mesh_node.h:146` | firmware | (target, seq) ring για ACK flood |
| `sizeof(MeshRtcState)` | 632 B | Πόση μνήμη που επιβιώνει τον ύπνο πιάνει η κατάσταση του κόμβου. | Κυρίως ο buffer των 10 μετρήσεων (610 B)· μετρημένο από τον κώδικα. | `firmware/libraries/GreenhouseMesh/mesh_node.h:682` | firmware | επιβιώνει στον deep sleep (RTC FAST) |
| `sizeof(MeshInFlightEntry)` | 64 B | Μέγεθος μίας εγγραφής «περιμένω επιβεβαίωση». | Το μήνυμα (61 B) + αριθμός + κατάσταση, στρογγυλεμένο από τον compiler. | `firmware/libraries/GreenhouseMesh/mesh_inflight.h:28` | firmware | RAM μόνο |
| `RELAY_BUFFER_TODAY` | 50 frames | Πόσα μηνύματα άλλων κρατά ένας relay που κοιμάται. | 50: τα κρατά μέχρι να ανοίξει το παράθυρο του parent· χωράνε στη μνήμη ύπνου (έως ~121). | `firmware/libraries/GreenhouseMesh/mesh_config.h:97` | firmware | sleepy relays κρατούν frames σε RTC ως το παράθυρο του parent· always-on relays κάνουν cut-through |

## A5. Firmware — κύκλος αφύπνισης (sleepy κόμβος)

| Παράμετρος | Τιμή | Τι είναι | Γιατί αυτή η τιμή | Πηγή | kind | Τεχνική σημείωση |
|---|---|---|---|---|---|---|
| `MESH_SLEEP_INTERVAL_MS` | 60000 ms | Κάθε πόσο ξυπνά ο αισθητήρας να μετρήσει και να στείλει. | 60 s είναι τιμή δοκιμών στο bench· στην παραγωγή 15′ ή 30′ (ορίζεται ανά run). | `firmware/libraries/GreenhouseMesh/mesh_config.h:64` | firmware | τιμή test στο firmware σήμερα· στον sim το T ορίζεται ανά run (§F) |
| `SENSOR_WARMUP_MS` | 2000 ms | Πόσο περιμένει ο κόμβος μετά το άναμμα των αισθητήρων πριν τους διαβάσει. | 2 s: ο DHT22 χρειάζεται τόσο για σωστή μέτρηση. | `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino:40` | firmware | αισθητήρες ON· επικαλύπτεται με το radio bring-up |
| `MESH_TX_CONFIRM_WAIT_MS` | 500 ms | Πόσο περιμένει ο κόμβος να μάθει αν ο parent πήρε το μήνυμα. | 500 ms: η επιβεβαίωση έρχεται σε χιλιοστά· είναι ανώτατο όριο ασφαλείας. | `firmware/libraries/GreenhouseMesh/mesh_config.h:67` | firmware | αναμονή send-callback (L2 ACK) |
| `MESH_APP_ACK_WAIT_MS` | 2000 ms | Πόσο περιμένει ο κόμβος την απάντηση του Pi. | 2 s: αρκεί για τη διαδρομή μέσω γέφυρας και Pi σε λίγα hops. | `firmware/libraries/GreenhouseMesh/mesh_config.h:69` | firmware | αναμονή ACK από το Pi· αναπάντητα → επόμενο wake |
| `MESH_WAKE_DISCOVERY_MS` | 5000 ms | Πόσο ψάχνει νέο parent όταν αποτύχει η αποστολή. | 5 s: αρκετό για να ακούσει γείτονες, χωρίς να αδειάσει η μπαταρία. | `firmware/libraries/GreenhouseMesh/mesh_config.h:65` | firmware | αναζήτηση νέου parent μετά από αποτυχία |
| `MESH_WAKE_MAX_AWAKE_MS` | 10000 ms | Το μέγιστο που επιτρέπεται να μείνει ξύπνιος ο κόμβος σε μία αφύπνιση. | 10 s: δικλίδα ασφαλείας ώστε ένα σφάλμα να μην αδειάσει την μπαταρία. | `firmware/libraries/GreenhouseMesh/mesh_config.h:74` | firmware | σκληρό όριο αφύπνισης |
| `MESH_MIN_SLEEP_MS` | 1000 ms | Ο ελάχιστος χρόνος ύπνου. | 1 s: για να μη δοθεί ποτέ μηδενικός ή αρνητικός χρόνος στον χρονοδιακόπτη. | `firmware/libraries/GreenhouseMesh/mesh_config.h:76` | firmware | ελάχιστος ύπνος |
| `BATT_ADC_SAMPLES` | 8 δείγματα | Πόσες μετρήσεις τάσης μπαταρίας παίρνει και βγάζει μέσο όρο. | 8: μειώνει τον θόρυβο του ADC με ελάχιστο κόστος χρόνου. | `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino:99` | firmware |  |
| `BATT_ADC_SAMPLE_DELAY_MS` | 2 ms | Αναμονή ανάμεσα στις μετρήσεις τάσης. | 2 ms: αφήνει το ADC να ηρεμήσει. | `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino:101` | firmware |  |
| `COLD_BOOT_USB_WAIT_MS` | 1500 ms | Αναμονή στο πρώτο άναμμα για να προλάβει να συνδεθεί το USB. | 1,5 s για debugging· δεν γίνεται στις αφυπνίσεις από ύπνο. | `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino:287` | firmware | μόνο σε cold boot, όχι σε timer wake |
| `UNCONFIRMED_WAKES_RESCAN` | 2 wakes | Μετά από πόσες αποτυχημένες αφυπνίσεις ξαναψάχνει κανάλι. | 2: δεν αντιδρά σε μία τυχαία αποτυχία. | `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino:175` | firmware | link counter → rescan καναλιού |
| `SEND_INTERVAL_MS` | 5000 ms | Κάθε πόσο στέλνει ένας κόμβος που είναι πάντα ξύπνιος. | 5 s για το bench (γρήγορα δεδομένα για δοκιμές). | `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino:39` | firmware | always-on: περίοδος ≈ SEND_INTERVAL + WARMUP |

## A6. Γέφυρα (bridge) και Pi

| Παράμετρος | Τιμή | Τι είναι | Γιατί αυτή η τιμή | Πηγή | kind | Τεχνική σημείωση |
|---|---|---|---|---|---|---|
| `UART_BAUD` | 115200 baud | Ταχύτητα του καλωδίου γέφυρας–Pi. | 115200: η συνηθισμένη ταχύτητα, αξιόπιστη στο UART του Pi Zero. | `firmware/bridge_esp32/bridge_esp32.ino:15` | firmware | γέφυρα ↔ Pi, 8N1 |
| `BRIDGE_FRAME_FORMAT` | {"type":"frame","data":"%s"}  | Πώς γράφει η γέφυρα κάθε μήνυμα προς το Pi. | JSON κείμενο με hex: εύκολο στο debugging, αλλά 2,5× μεγαλύτερο από δυαδική μορφή. | `firmware/bridge_esp32/bridge_esp32.ino:153` | firmware | κάθε data frame γίνεται μία γραμμή JSON με hex |
| `BRIDGE_UART_WRITE` | println  | Η εντολή που γράφει τη γραμμή στο UART. | println: προσθέτει αλλαγή γραμμής ώστε το Pi να ξέρει πού τελειώνει κάθε μήνυμα. | `firmware/bridge_esp32/bridge_esp32.ino:37` | firmware | println → +CRLF· μπλοκάρει όσο γεμίζει το FIFO |
| `BRIDGE_USB_ECHO` | Serial.printf  | Η γέφυρα γράφει κάθε μήνυμα και στο USB για debugging. | Βολικό στο bench· αν το USB συνδεθεί σε υπολογιστή που δεν διαβάζει, μπορεί να παγώνει. | `firmware/bridge_esp32/bridge_esp32.ino:38` | firmware | δεύτερη εγγραφή ανά frame στο USB-CDC (debug) |
| `BRIDGE_ACK_BROADCASTS` | 1 tx | Πόσες φορές στέλνει η γέφυρα κάθε επιβεβαίωση. | 1: οι relays την αναμεταδίδουν μόνοι τους. | `firmware/bridge_esp32/bridge_esp32.ino:105` | firmware | ένα broadcast ανά ACK, χωρίς retry, η γέφυρα δεν κάνει re-flood |
| `BRIDGE_FRAME_QUEUE` | 0 frames | Ουρά μηνυμάτων μέσα στη γέφυρα. | Καμία: γράφει στο UART αμέσως. Σε μεγάλο δίκτυο αυτό γίνεται στενωπός. | `firmware/bridge_esp32/bridge_esp32.ino:121` | firmware | καμία ουρά εφαρμογής: η εγγραφή γίνεται μέσα στο ESP-NOW RX callback |
| `BAUD` | 115200 baud | Ταχύτητα UART από την πλευρά του Pi. | Ίδια με της γέφυρας (115200). | `pi/scripts/serial_bridge.py:43` | firmware | Pi πλευρά |
| `HEARTBEAT_INTERVAL_S` | 2 s | Κάθε πόσο λέει η γέφυρα στο Pi «είμαι ζωντανή». | 2 s: το Pi καταλαβαίνει γρήγορα αν χάθηκε η γέφυρα. | `pi/scripts/serial_bridge.py:55` | firmware |  |
| `MESH_OFFLINE_AFTER` | 3 × | Πόσα χαμένα διαστήματα κάνουν έναν αισθητήρα «offline». | 3: ανέχεται 1–2 χαμένες μετρήσεις χωρίς ψεύτικο συναγερμό. | `firmware/libraries/GreenhouseMesh/mesh_config.h:130` | firmware |  |
| `MESH_EXPECTED_REPORT_INTERVAL_MS` | 5000 ms | Κάθε πόσο περιμένει η γέφυρα μέτρηση από κόμβο που είναι πάντα ξύπνιος. | 5 s, ίδιο με το SEND_INTERVAL_MS. | `firmware/libraries/GreenhouseMesh/mesh_config.h:131` | firmware |  |
| `_LIFEPO4_CURVE` | 3400→100 · 3350→90 · 3320→80 · 3300→70 · 3280→60 · 3260→50 · 3250→40 · 3220→30 · 3200→20 · 3000→10 · 2800→0 mV → % | Πίνακας που μετατρέπει την τάση μπαταρίας σε ποσοστό. | Η καμπύλη εκφόρτισης της LiFePO4, που είναι πολύ επίπεδη γύρω στα 3,2–3,3 V. | `pi/scripts/serial_bridge.py:255` | firmware | SoC πίνακας (piecewise linear) |
| `PI_PROCESS_MS` | 20 ms | Πόσο χρόνο θέλει το Pi για να επεξεργαστεί ένα μήνυμα. | 20 ms είναι εκτίμηση (δεν έχει μετρηθεί): ανάγνωση αρχείου, αποκρυπτογράφηση, αποστολή MQTT. | `pi/scripts/serial_bridge.py:303` | model | reload nodes.json + AES-GCM + ≤6 MQTT publish ανά frame — ΜΗ μετρημένο, εύρος 5–300 |

## A7. CART v2 Part C (σχεδιασμένο, όχι υλοποιημένο)

| Παράμετρος | Τιμή | Τι είναι | Γιατί αυτή η τιμή | Πηγή | kind | Τεχνική σημείωση |
|---|---|---|---|---|---|---|
| `MESH_CART_ENABLE` | 1  | Διακόπτης: όλοι οι κόμβοι κοιμούνται ΚΑΙ κάνουν relay. | 1 = ενεργό. Με 0 το firmware γυρίζει ακριβώς στη σημερινή (Phase 1) λειτουργία. | `firmware/libraries/GreenhouseMesh/mesh_config.h:83` | firmware | 0 = Phase 1 (rollback) |
| `MESH_CART_SLOT_MS` | 2500 ms | Πόσο μένει ανοιχτό το παράθυρο λήψης κάθε κόμβου για τα παιδιά του. | 2,5 s: οι αισθητήρες ζεσταίνονται μέσα σε αυτό (2 s), οπότε η μέτρηση είναι έτοιμη όταν στείλει. | `firmware/libraries/GreenhouseMesh/mesh_config.h:87` | firmware | παράθυρο λήψης ανά κόμβο (σκάλα) |
| `MESH_RELAY_BUFFER_SIZE` | 50 frames | Πόσα μηνύματα άλλων κρατά ένας relay στη μνήμη ύπνου. | 50: καλύπτει τον πιο φορτωμένο κόμβο στο 50×10 και πιάνει 3 KB από τα 8 KB. | `firmware/libraries/GreenhouseMesh/mesh_config.h:97` | firmware | relay buffer σε RTC |
| `MESH_DRIFT_BIAS_PPM` | 1700 ppm | Πόσο διαφέρουν τα ρολόγια δύο κόμβων, στο firmware. | 1700 ppm = 0,17 %: μετρημένο στο bench (Gate 0 run 1). | `firmware/libraries/GreenhouseMesh/mesh_config.h:94` | firmware | Gate 0 run 1 |
| `MESH_DRIFT_STEP_PPM_300S` | 100 ppm | Πόσο «χορεύει» το ρολόι από κύκλο σε κύκλο, στο firmware. | 100 ppm ανά 5′: εκτίμηση μέχρι το Gate 0 run 2. | `firmware/libraries/GreenhouseMesh/mesh_config.h:95` | firmware | Gate 0: προσωρινό |
| `MESH_GUARD_CAP_MS` | 20000 ms | Το μέγιστο που ξυπνά νωρίτερα ένας κόμβος για να βρει τον parent. | 20 s: ασφάλεια ώστε ένα λάθος στο drift να μην κρατά τον κόμβο ξύπνιο για πάντα. | `firmware/libraries/GreenhouseMesh/mesh_config.h:96` | firmware | ανώτατο G_max |
| `MESH_SLEEPY_RELAY_DEPTH_MAX` | 1 hops | Πόσοι κοιμισμένοι relays επιτρέπονται στη σειρά (αρχικό CART). | 1 στο αρχικό σχέδιο για ασφάλεια· το CART βάθους N το καταργεί. | `docs/superpowers/plans/2026-09-23-cart-phase2-synced-wake.md:974` | planned | 0 = Phase 1 |
| `MESH_MAX_SLEEPY_CHILDREN` | 6  | Πόσα παιδιά δέχεται ένας κοιμισμένος relay (αρχικό CART). | 6: κρατά χαμηλές τις συγκρούσεις και τον buffer. | `docs/superpowers/plans/2026-09-23-cart-phase2-synced-wake.md:977` | planned | admission control μέσω RELAY_CAP |
| `MESH_RX_BEACON_PERIOD_MS` | 100 ms | Κάθε πόσο λέει ο relay «είμαι ξύπνιος, στείλτε» όσο είναι ανοιχτό το παράθυρο. | 100 ms: τα παιδιά τον βρίσκουν γρήγορα, με ελάχιστο χρόνο εκπομπής. | `firmware/libraries/GreenhouseMesh/mesh_config.h:92` | firmware | RX_OPEN beacons μέσα στο παράθυρο |
| `MESH_WAKE_GUARD_MIN_MS` | 250 ms | Το ελάχιστο περιθώριο χρόνου που ξυπνά νωρίτερα ένας κόμβος. | 250 ms: καλύπτει τη διακύμανση εκκίνησης του ESP32-C3. | `firmware/libraries/GreenhouseMesh/mesh_config.h:93` | firmware | radio/boot jitter floor |
| `MESH_CART_JITTER_MS` | 300 ms | Τυχαία καθυστέρηση πριν στείλει, για να μη μιλάνε όλα τα παιδιά μαζί. | 300 ms: με τα 100 ms του σχεδίου ο προσομοιωτής έβγαλε ~50 % συγκρούσεις σε 10 κόμβους ανά επίπεδο. | `firmware/libraries/GreenhouseMesh/mesh_config.h:89` | firmware | J |
| `MESH_CART_ATTEMPTS` | 3  | Πόσες φορές ξαναδοκιμάζει ένα παιδί μέσα στο παράθυρο. | 3: με τυχαία καθυστέρηση οι συγκρούσεις σχεδόν εξαφανίζονται. | `firmware/libraries/GreenhouseMesh/mesh_config.h:91` | firmware |  |
| `MESH_CART_PER_CHILD_MS` | 150 ms | Χρόνος παραθύρου που προστίθεται για κάθε παιδί. | 150 ms: αποστολή + επιβεβαίωση + περιθώριο. | `docs/superpowers/plans/2026-09-23-cart-phase2-synced-wake.md:985` | planned |  |
| `MESH_KNOCK_WINDOW_MS` | 500 ms | Χρόνος για νέο παιδί που «χτυπά την πόρτα». | 500 ms: αρκετό για να ακουστεί ένα νέο παιδί. | `docs/superpowers/plans/2026-09-23-cart-phase2-synced-wake.md:986` | planned |  |
| `MESH_RELAY_ACK_LINGER_MS` | 300 ms | Πόσο μένει ξύπνιος ο relay μετά για να περάσουν οι τελευταίες επιβεβαιώσεις. | 300 ms: η επιβεβαίωση από το Pi έρχεται σε 50–300 ms. | `docs/superpowers/plans/2026-09-23-cart-phase2-synced-wake.md:987` | planned |  |
| `MESH_SCHED_HIST` | 16 catches | Πόσες πρόσφατες αποκλίσεις ρολογιού θυμάται ο κόμβος. | 16: αρκετές για να εκτιμήσει σωστά πόσο «χορεύει» το ρολόι. | `firmware/libraries/GreenhouseMesh/mesh_sched.h:16` | firmware | margin policy window |
| `GUARD_MARGIN_K` | 1.5  | Συντελεστής ασφαλείας του περιθωρίου αφύπνισης. | 1,5× η μεγαλύτερη πρόσφατη απόκλιση: σπάνια χάνει ραντεβού χωρίς πολύ περιττό ξύπνημα. | `docs/superpowers/specs/2026-09-23-cart-v2-revision.md §3.3` | planned | G = clamp(2·k·max|err| + pad, G_min, G_max) |
| `GUARD_PAD_MS` | 50 ms | Σταθερό επιπλέον περιθώριο. | 50 ms για μικρές τυχαίες καθυστερήσεις του radio. | `docs/superpowers/specs/2026-09-23-cart-v2-revision.md §3.3` | planned |  |
| `G_MAX_FACTOR` | 1.3  | Συντελεστής ασφαλείας του μέγιστου περιθωρίου. | 1,3: 30 % πάνω από το θεωρητικό ελάχιστο. | `docs/superpowers/specs/2026-09-23-cart-v2-revision.md §3.3` | planned | G_max = 2·|b|·T·1.3 |
| `DRIFT_BIAS_PLANNING` | 0.006  | Πόσο διαφέρουν τα ρολόγια δύο κόμβων (αρχική εκτίμηση). | 0,6 %: από δημοσιευμένες μετρήσεις σε ESP32, πριν μετρήσουμε. | `docs/superpowers/specs/2026-09-23-cart-v2-revision.md §2` | repo-doc | 0,6 % σχετικό bias |
| `DRIFT_BIAS_MEASURED` | 0.0017  | Πόσο διαφέρουν τα ρολόγια δύο κόμβων (μετρημένο). | 0,17 %: μετρήθηκε στο bench (Gate 0 run 1), 3,5× καλύτερο από την εκτίμηση. | `docs/superpowers/specs/2026-09-23-cart-v2-revision.md §5 (Gate 0 run 1)` | measured | χειρότερο ζεύγος, robust stats — run 2 εκκρεμεί |
| `DRIFT_STEP_PER_300S` | 0.0001  | Πόσο αλλάζει η ταχύτητα του ρολογιού από κύκλο σε κύκλο (λόγω θερμοκρασίας). | 0,01 % ανά 5′ είναι εκτίμηση· θα μετρηθεί στο Gate 0 run 2. Επηρεάζει πολύ την ενέργεια. | `docs/analysis/cart_sim.py:32` | repo-doc | ανά κύκλο, κλιμακώνεται ∝ T |

## B. Hardware ESP32-C3 (datasheet) και toolchain (sdkconfig)

| Παράμετρος | Τιμή | Τι είναι | Γιατί αυτή η τιμή | Πηγή | kind | Τεχνική σημείωση |
|---|---|---|---|---|---|---|
| `I_TX_MA` | 335 mA | Ρεύμα του chip όταν εκπέμπει. | 335 mA από το datasheet (στη μέγιστη ισχύ)· κρατά μόνο ~1 ms ανά μήνυμα. | ESP32-C3 Datasheet (Espressif), Table 5-7 | datasheet | 802.11b 1 Mbps @21 dBm· το firmware κόβει στα 20 dBm → άνω φράγμα |
| `I_RX_MA` | 84 mA | Ρεύμα όταν το radio είναι ανοιχτό και ακούει. | 84 mA από το datasheet· ο χρόνος ακρόασης είναι το μεγαλύτερο κόστος ενέργειας. | ESP32-C3 Datasheet (Espressif), Table 5-7 | datasheet | 802.11b/g/n HT20 RX |
| `I_CPU_RUN_MA` | 23 mA | Ρεύμα όταν δουλεύει μόνο ο επεξεργαστής, χωρίς radio. | 23 mA από το datasheet (160 MHz). | ESP32-C3 Datasheet (Espressif), Table 5-8 | datasheet | modem-sleep 160 MHz, periph off, CPU run |
| `I_CPU_IDLE_MA` | 16 mA | Ρεύμα όταν ο επεξεργαστής περιμένει, χωρίς radio. | 16 mA από το datasheet. | ESP32-C3 Datasheet (Espressif), Table 5-8 | datasheet | modem-sleep 160 MHz, periph off, CPU idle |
| `I_LIGHT_SLEEP_UA` | 130 µA | Ρεύμα σε ελαφρύ ύπνο (ξυπνά αμέσως). | 130 µA από το datasheet· χρήσιμο όσο ζεσταίνονται οι αισθητήρες. | ESP32-C3 Datasheet (Espressif), Table 5-9 | datasheet |  |
| `I_DEEP_SLEEP_CHIP_UA` | 5 µA | Ρεύμα του ίδιου του chip σε βαθύ ύπνο. | 5 µA από το datasheet· η πλακέτα προσθέτει πολύ περισσότερο. | ESP32-C3 Datasheet (Espressif), Table 5-9 | datasheet | RTC timer + RTC memory, μόνο το chip |
| `RX_SENSITIVITY_1M_DBM` | -98.4 dBm | Το πιο αδύναμο σήμα που μπορεί να λάβει ο δέκτης. | −98,4 dBm από το datasheet, στην ταχύτητα 1 Mbps του ESP-NOW. | ESP32-C3 Datasheet (Espressif), Table 6-4 | datasheet | 802.11b 1 Mbps |
| `TX_POWER_MAX_DBM` | 21 dBm | Η μέγιστη ισχύς εκπομπής του chip. | 21 dBm από το datasheet. | ESP32-C3 Datasheet (Espressif), Table 6-2 | datasheet | 802.11b |
| `RTC_FAST_MEM_B` | 8192 B | Η μνήμη που διατηρείται όσο ο κόμβος κοιμάται. | 8 KB από το datasheet· εδώ ζουν οι buffers που πρέπει να επιβιώσουν τον ύπνο. | ESP32-C3 Datasheet (Espressif), Memory | datasheet | διατηρείται στον deep sleep |
| `SRAM_KB` | 400 KB | Η κανονική μνήμη RAM του chip. | 400 KB από το datasheet· χάνεται στον βαθύ ύπνο. | ESP32-C3 Datasheet (Espressif), Memory | datasheet | 16 KB ως cache |
| `WAKE_LATENCY_S` | 0.14 – 0.23 s | Πόσο θέλει ο κόμβος από το ξύπνημα μέχρι να τρέξει ο κώδικάς μας. | 140–230 ms: μετρήσεις από τη βιβλιογραφία για το ESP32-C3. | `docs/technical/02-esp-now-protocol.md:113` | repo-doc | deep sleep → app_main, πριν το radio init |
| `CONFIG_ESP_PHY_MAX_WIFI_TX_POWER` | 20 dBm | Η ισχύς εκπομπής που επιτρέπει το λογισμικό του ESP32. | 20 dBm: ρύθμιση του Arduino core, λίγο κάτω από το μέγιστο του chip. | `Arduino15/packages/esp32/tools/esp32c3-libs/3.3.11/sdkconfig:1706` | toolchain | πραγματικό όριο TX του build |
| `CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ` | 160 MHz | Η συχνότητα του επεξεργαστή. | 160 MHz: η προεπιλογή του Arduino core. | `Arduino15/packages/esp32/tools/esp32c3-libs/3.3.11/sdkconfig:1758` | toolchain |  |
| `CONFIG_RTC_CLK_SRC_INT_RC` | True  | Ποιο ρολόι μετρά τον χρόνο στον ύπνο. | Το εσωτερικό RC (~136 kHz): το πιο οικονομικό, αλλά με το περισσότερο drift. | `Arduino15/packages/esp32/tools/esp32c3-libs/3.3.11/sdkconfig:1614` | toolchain | RTC slow clock = εσωτερικό RC ~136 kHz (drift) |
| `CONFIG_BOOTLOADER_RESERVE_RTC_SIZE` | 16 B | Μνήμη ύπνου που κρατά ο bootloader για τον εαυτό του. | 16 B: ρύθμιση του Arduino core. | `Arduino15/packages/esp32/tools/esp32c3-libs/3.3.11/sdkconfig:389` | toolchain | δεσμευμένα στη RTC από τον bootloader |
| `CONFIG_ESP_WIFI_DYNAMIC_TX_BUFFER_NUM` | 32 buffers | Πόσα μηνύματα μπορούν να περιμένουν για αποστολή στο radio. | 32: ρύθμιση του core· πάνω από αυτά η αποστολή αποτυγχάνει. | `Arduino15/packages/esp32/tools/esp32c3-libs/3.3.11/sdkconfig:1844` | toolchain | όριο για back-to-back esp_now_send |
| `CONFIG_ESP_WIFI_STATIC_RX_BUFFER_NUM` | 8 buffers | Σταθερές θέσεις λήψης μηνυμάτων. | 8: ρύθμιση του core. | `Arduino15/packages/esp32/tools/esp32c3-libs/3.3.11/sdkconfig:1839` | toolchain | ουρά εισόδου radio |
| `CONFIG_ESP_WIFI_DYNAMIC_RX_BUFFER_NUM` | 32 buffers | Επιπλέον θέσεις λήψης μηνυμάτων. | 32: ρύθμιση του core. Μαζί με τις 8 είναι όλη η «ουρά» της γέφυρας. | `Arduino15/packages/esp32/tools/esp32c3-libs/3.3.11/sdkconfig:1840` | toolchain | ουρά εισόδου radio |
| `CONFIG_ESP_WIFI_MGMT_SBUF_NUM` | 32 buffers | Θέσεις για μηνύματα διαχείρισης (το ESP-NOW χρησιμοποιεί τέτοια). | 32: ρύθμιση του core. | `Arduino15/packages/esp32/tools/esp32c3-libs/3.3.11/sdkconfig:1856` | toolchain |  |
| `HWSERIAL_TX_BUFFER_DEFAULT` | 0 B | Buffer αποστολής του UART στη γέφυρα. | 0: η γέφυρα περιμένει να φύγει κάθε γραμμή πριν συνεχίσει. | `Arduino15/packages/esp32/hardware/esp32/3.3.11/cores/esp32/HardwareSerial.cpp:142` | toolchain | 0 = χωρίς ring buffer, μόνο HW FIFO → println μπλοκάρει |
| `UART_HW_FIFO_B` | 128 B | Η μικρή μνήμη του UART μέσα στο chip. | 128 B από το datasheet: λιγότερη από μία γραμμή μηνύματος (150 B). | SOC_UART_FIFO_LEN (ESP32-C3) | datasheet |  |

## C. Πλακέτα, ενέργεια, μπαταρία, ηλιακό

| Παράμετρος | Τιμή | Τι είναι | Γιατί αυτή η τιμή | Πηγή | kind | Τεχνική σημείωση |
|---|---|---|---|---|---|---|
| `I_SLEEP_BOARD_UA` | 62.5 µA | Ρεύμα όλης της πλακέτας σε βαθύ ύπνο. | 62,5 µA: εκτίμηση (55 µA πλακέτα + 7,5 µA divider). Δεν έχει μετρηθεί. | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:137` | repo-doc | 55 µA (RTC + LDO) + 7,5 µA divider |
| `I_DIVIDER_UA` | 7.5 µA | Ρεύμα των αντιστάσεων που μετρούν την μπαταρία. | 7,5 µA = 3,3 V / 440 kΩ· ρέει συνέχεια. | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:46` | repo-doc | 2 × 220 kΩ, πάντα ON |
| `I_ACTIVE_LUMPED_MA` | 86.5 mA | Ένα μέσο ρεύμα για όλο τον χρόνο που είναι ξύπνιος ο κόμβος (απλό μοντέλο). | 86,5 mA: εκτίμηση του repo, κοντά στο ρεύμα λήψης. | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:136` | repo-doc | όλο το awake ενιαία (μοντέλο repo) |
| `I_ALWAYS_ON_MA` | 100 mA | Ρεύμα κόμβου που δεν κοιμάται ποτέ. | ~100 mA: radio συνέχεια ανοιχτό· γι' αυτό δεν γίνεται με μπαταρία. | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:183` | repo-doc |  |
| `I_DHT22_MA` | 1.5 mA | Ρεύμα του αισθητήρα θερμοκρασίας/υγρασίας DHT22. | 1,5 mA, μόνο όσο είναι αναμμένος. | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:44` | repo-doc | μόνο στο warmup (GPIO5 HIGH) |
| `I_SOIL_MA` | 5 mA | Ρεύμα του αισθητήρα υγρασίας εδάφους. | 5 mA, μόνο όσο είναι αναμμένος. | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:45` | repo-doc | μόνο στο warmup (GPIO4 HIGH) |
| `AWAKE_PHASE1_S` | 2.5 s | Πόσο μένει ξύπνιος ένας κόμβος σήμερα σε κάθε κύκλο. | 2,5 s: 2 s warm-up του DHT22 + ανάγνωση και αποστολή. | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:138` | repo-doc | 2 s warmup ∥ radio + ~0,3 s read/send/confirm |
| `BATTERY_MAH` | 1500 mAh | Χωρητικότητα της μπαταρίας. | 1500 mAh: τυπική LiFePO4 18650. | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:47` | repo-doc | LiFePO4 18650, 3,2 V, κατευθείαν στο 3V3 |
| `BATTERY_DOD` | 0.8  | Πόσο από τη μπαταρία χρησιμοποιούμε στην πράξη. | 80 %: τα τελευταία 20 % δεν είναι αξιόπιστα και φθείρουν την μπαταρία. | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:167` | repo-doc |  |
| `SOLAR_WINTER_MAH_DAY` | 437 mAh/day | Πόση ενέργεια δίνει το ηλιακό την πιο σκοτεινή εποχή. | 437 mAh/ημέρα: 2 W πάνελ, 2 ώρες ήλιο, 50 % απώλειες, 70 % φορτιστής. | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:231` | repo-doc | 6 V/2 W, 2 PSH, 50 % derate, TP5000 70 % |
| `ENERGY_MODEL` | lumped \| per-state  | Πώς υπολογίζεται η ενέργεια. | Δύο τρόποι: απλός (ένα ρεύμα για όλο το ξύπνημα) ή αναλυτικός (ρεύμα ανά κατάσταση). | meshsim | model | lumped = repo· per-state = datasheet ανά κατάσταση |
| `PER_STATE_CURRENTS` | BOOT 23 mA · LISTEN/RX 84 mA · TX 335 mA (μόνο airtime) · CPU 23 mA · +6,5 mA αισθητήρες στο warmup · SLEEP 62,5 µA  | Τα ρεύματα του αναλυτικού μοντέλου. | Από το datasheet για κάθε κατάσταση (εκκίνηση, ακρόαση, εκπομπή, ύπνος). | `ESP32-C3 Datasheet (Espressif) 5-7/5-8 + docs/SENSOR_NODE_POWER_AND_SOLAR.md` | model |  |

## D. PHY/MAC: IEEE 802.11 DSSS + ESP-NOW frame

| Παράμετρος | Τιμή | Τι είναι | Γιατί αυτή η τιμή | Πηγή | kind | Τεχνική σημείωση |
|---|---|---|---|---|---|---|
| `PHY_RATE_MBPS` | 1 Mbps | Η ταχύτητα με την οποία στέλνει το ESP-NOW. | 1 Mbps: η προεπιλογή της Espressif, η πιο αργή αλλά με τη μεγαλύτερη εμβέλεια. | ESP-IDF ESP-NOW guide (frame format, default rate) | standard | DSSS DBPSK |
| `PLCP_LONG_US` | 192 µs | Το «προοίμιο» πριν από κάθε μήνυμα στον αέρα. | 192 µs: υποχρεωτικό από το πρότυπο WiFi στο 1 Mbps. | IEEE 802.11-2020 Clause 16 (DSSS PHY) | standard | long preamble 144 + header 48· υποχρεωτικό στο 1 Mbps |
| `ESPNOW_OVERHEAD_B` | 43 B | Τα bytes που προσθέτει το ESP-NOW γύρω από τα δικά μας δεδομένα. | 43 B: επικεφαλίδα WiFi, κωδικοί Espressif και έλεγχος σφαλμάτων. | ESP-IDF ESP-NOW guide (frame format, default rate) | standard | mac_header 24 + category_code 1 + oui 3 + random 4 + vendor_element_header 7 + fcs 4 |
| `ACK_FRAME_B` | 14 B | Μέγεθος της αυτόματης επιβεβαίωσης του WiFi. | 14 B από το πρότυπο. | IEEE 802.11-2020 Clause 16 (DSSS PHY) | standard | 802.11 ACK control frame |
| `SIFS_US` | 10 µs | Μικρή παύση πριν από την αυτόματη επιβεβαίωση. | 10 µs από το πρότυπο WiFi. | IEEE 802.11-2020 Clause 16 (DSSS PHY) | standard |  |
| `SLOT_US` | 20 µs | Η μονάδα χρόνου της τυχαίας αναμονής πριν την αποστολή. | 20 µs από το πρότυπο WiFi (1 Mbps). | IEEE 802.11-2020 Clause 16 (DSSS PHY) | standard | εναλλακτικό preset ERP: 9 µs |
| `DIFS_US` | 50 µs | Παύση που περιμένει κάθε κόμβος όταν ελευθερωθεί το κανάλι. | 50 µs = SIFS + 2 slots, από το πρότυπο. | IEEE 802.11-2020 Clause 16 (DSSS PHY) | standard | SIFS + 2·slot |
| `CW_MIN` | 31 slots | Το εύρος της τυχαίας αναμονής στην πρώτη προσπάθεια. | 31 slots από το πρότυπο· διπλασιάζεται μετά από κάθε αποτυχία. | IEEE 802.11-2020 Clause 16 (DSSS PHY) | standard | εναλλακτικό preset ERP: 15 (cart_sim: 16) |
| `CW_MAX` | 1023 slots | Το μέγιστο εύρος τυχαίας αναμονής. | 1023 slots από το πρότυπο. | IEEE 802.11-2020 Clause 16 (DSSS PHY) | standard |  |
| `MAC_RETRY_LIMIT` | 5 retx | Πόσες φορές ξαναστέλνει αυτόματα το radio ένα μήνυμα που δεν επιβεβαιώθηκε. | ~5 κατά αναφορές χρηστών· η Espressif δεν το δημοσιεύει, γι' αυτό δοκιμάζονται και άλλες τιμές. | αναφορές κοινότητας (esp-idf #9383, Instructables) | unverified | η Espressif δεν το τεκμηριώνει· sweep {0,3,5,7} |
| `MAX_PAYLOAD_B` | 250 B | Το μέγιστο μέγεθος δεδομένων σε ένα μήνυμα ESP-NOW. | 250 B από την τεκμηρίωση της Espressif. | ESP-IDF ESP-NOW guide (frame format, default rate) | standard | ESP-NOW v1 |

## E. Μοντέλο ραδιοδιάδοσης και καναλιού

| Παράμετρος | Τιμή | Τι είναι | Γιατί αυτή η τιμή | Πηγή | kind | Τεχνική σημείωση |
|---|---|---|---|---|---|---|
| `FREQ_HZ` | 2.412e+09 Hz | Η συχνότητα του καναλιού 1. | 2,412 GHz από το πρότυπο WiFi. | κανάλι 1 | standard |  |
| `PL_1M_DB` | 40.1 dB | Πόσο εξασθενεί το σήμα στο πρώτο μέτρο. | 40 dB: υπολογίζεται από τη φυσική (νόμος Friis) για 2,4 GHz. | Friis, d0 = 1 m | derived |  |
| `PATHLOSS_EXPONENT` | 2.5  | Πόσο γρήγορα εξασθενεί το σήμα με την απόσταση. | 2,5: ανάμεσα στον ελεύθερο χώρο (2) και σε χώρο με εμπόδια (3–4)· φυτά και σκελετός θερμοκηπίου. | meshsim | model | θερμοκήπιο: 2–3 |
| `SHADOWING_SIGMA_DB` | 4 dB | Τυχαίες διαφορές σήματος από εμπόδια. | 4 dB: τυπική τιμή εσωτερικού χώρου. | meshsim | model | log-normal |
| `SENS_FER` | 0.08  | Το ποσοστό λαθών με το οποίο ορίζεται η ευαισθησία του δέκτη. | 8 %: ο ορισμός του προτύπου WiFi. | IEEE 802.11-2020 Clause 16 (DSSS PHY) | standard | ορισμός sensitivity: FER 8 %, PSDU 1024 B |
| `SENS_PSDU_B` | 1024 B | Μέγεθος μηνύματος στον ορισμό της ευαισθησίας. | 1024 B: ο ορισμός του προτύπου. | IEEE 802.11-2020 Clause 16 (DSSS PHY) | standard |  |
| `CCA_THRESHOLD_DBM` | -82 dBm | Πόσο δυνατό πρέπει να είναι ένα σήμα για να θεωρηθεί το κανάλι «κατειλημμένο». | −82 dBm: τυπική τιμή WiFi· η Espressif δεν δημοσιεύει τη δική της. | meshsim | model | carrier sense· hidden terminals από την τοπολογία |
| `PER_MODEL` | SINR → BER(DBPSK) → PER(L)  | Πώς υπολογίζεται η πιθανότητα να χαθεί ένα μήνυμα. | Από την ισχύ του σήματος, με τύπους του προτύπου, ρυθμισμένους ώστε να ταιριάζουν στο datasheet. | meshsim | model | η παρεμβολή μετράει ως θόρυβος: οι συγκρούσεις προκύπτουν χωρίς αυθαίρετο capture threshold |

## F. Σενάριο (ρυθμίζεται ανά run)

| Παράμετρος | Τιμή | Τι είναι | Γιατί αυτή η τιμή | Πηγή | kind | Τεχνική σημείωση |
|---|---|---|---|---|---|---|
| `RANKS` | 50 ranks | Πόσα «επίπεδα» απόστασης από τη γέφυρα έχει το δίκτυο. | 50: το σενάριο δοκιμής αντοχής που ζητήθηκε. | σενάριο | scenario |  |
| `NODES_PER_RANK` | 10 κόμβοι | Πόσοι κόμβοι υπάρχουν σε κάθε επίπεδο. | 10: το σενάριο που ζητήθηκε (500 συνολικά). | σενάριο | scenario | σύνολο = RANKS × NODES_PER_RANK |
| `TOPOLOGY` | layered  | Πώς είναι τοποθετημένοι οι κόμβοι. | Σε επίπεδα που ακούν μόνο τα γειτονικά τους: εγγυάται ακριβώς 50 επίπεδα. | σενάριο | scenario | layered (γειτονία rank k±1) | geometric 2D |
| `T_S` | 900 s | Κάθε πόσο ξυπνά το δίκτυο. | 15′: το ένα από τα δύο standards (15′ και 30′). | σενάριο | scenario | presets: 900 (15′), 1800 (30′)· οποιαδήποτε τιμή > 30 s |
| `TECHNIQUE` | T2-flood  | Πώς ταξιδεύουν τα μηνύματα και οι επιβεβαιώσεις. | T1 = επιβεβαίωση σε κάθε hop· T2 = όλη η διαδρομή και μετά επιβεβαίωση πίσω. | σενάριο | scenario | T1-ladder | T1-per-cycle | T2-flood | T2-unicast |
| `SLEEP_MODE` | allsleepy  | Ποιοι κόμβοι κοιμούνται. | Όλοι (allsleepy): έτσι είναι το πραγματικό σύστημα, χωρίς κόμβους στο ρεύμα. | σενάριο | scenario | allsleepy = το deployment: ΟΛΟΙ οι κόμβοι μπαταρία, ύπνος, relay, συγχρονισμένη αφύπνιση· phase1 μόνο ως baseline του σημερινού firmware, ποτέ ως πρόταση |
| `ACK_RELAY_GATE` | cart  | Πότε αναμεταδίδει ένας κόμβος μια επιβεβαίωση. | Όταν έχει ανοιχτό παράθυρο· αλλιώς οι κοιμισμένοι κόμβοι δεν θα την περνούσαν ποτέ. | σενάριο | scenario | cart: !sleepy || rxOpen (απαραίτητο στο allsleepy)· firmware: !sleepy (baseline — μπλοκάρει κάθε re-flood) |
| `MAX_TTL_OVERRIDE` | — hops | Δοκιμαστική αλλαγή του ορίου hops. | Κενό = όπως το firmware (16). | σενάριο | scenario | None = firmware (16)· what-if π.χ. 255 |
| `RELAY_BUFFER` | derived (§H) frames | Πόσα μηνύματα άλλων κρατά ένας κοιμισμένος relay. | Υπολογίζεται από τη μνήμη ύπνου (§H)· 50 καλύπτει το χειρότερο rank-1 στο 50×10. | σενάριο | scenario | T1 store-and-forward |
| `RELAY_OVERFLOW` | backpressure  | Τι γίνεται όταν γεμίσει ο buffer. | Backpressure: ο relay δεν δέχεται άλλα, και τα παιδιά κρατάνε τα δικά τους για τον επόμενο κύκλο. | σενάριο | scenario | drop-oldest | drop-new | backpressure (δεν γίνεται hop-ACK) |
| `CYCLES` | 4 κύκλοι | Πόσους κύκλους προσομοιώνει. | 4: αρκούν για σταθερή εικόνα χωρίς πολύ χρόνο υπολογισμού. | σενάριο | scenario | steady-state + extrapolation σε mAh/day |
| `SEEDS` | 5  | Πόσες επαναλήψεις με διαφορετική τύχη. | 5: δίνουν διάστημα εμπιστοσύνης 95 %. | σενάριο | scenario | 95 % CI |
| `NODE_MTBF_H` | — h | Πόσο συχνά χαλάει ένας κόμβος. | Κενό = δεν χαλάει κανείς στην προσομοίωση. | σενάριο | scenario | None = χωρίς αποτυχίες κόμβων |

## G. Παράγωγες ποσότητες (υπολογίζονται)

| Παράμετρος | Τιμή | Τι είναι | Γιατί αυτή η τιμή | Πηγή | kind | Τεχνική σημείωση |
|---|---|---|---|---|---|---|
| `AIRTIME_JOIN_US` | 600 µs | Πόσο κρατά στον αέρα το μήνυμα «θέλω να μπω». | Υπολογίζεται: 192 + 8 × (43 + 8). | 192 + 8·(43 + L) | derived | L = 8 B |
| `AIRTIME_ACK_US` | 696 µs | Πόσο κρατά στον αέρα η επιβεβαίωση του Pi. | Υπολογίζεται: 192 + 8 × (43 + 20). | 192 + 8·(43 + L) | derived | L = 20 B |
| `AIRTIME_BEACON_US` | 752 µs | Πόσο κρατά στον αέρα ένα beacon. | Υπολογίζεται: 192 + 8 × (43 + 27). | 192 + 8·(43 + L) | derived | L = 27 B |
| `AIRTIME_BEACON_V3_US` | 768 µs | Πόσο θα κρατούσε το beacon του αρχικού CART. | Υπολογίζεται: 192 + 8 × (43 + 29). | 192 + 8·(43 + L) | derived | L = 29 B |
| `AIRTIME_PROVISION_US` | 800 µs | Πόσο κρατά στον αέρα το μήνυμα με τα κλειδιά. | Υπολογίζεται: 192 + 8 × (43 + 33). | 192 + 8·(43 + L) | derived | L = 33 B |
| `AIRTIME_DATA_US` | 1024 µs | Πόσο κρατά στον αέρα ένα μήνυμα μέτρησης. | Υπολογίζεται: 192 + 8 × (43 + 61) = ~1 ms. | 192 + 8·(43 + L) | derived | L = 61 B |
| `AIRTIME_80211_ACK_US` | 304 µs | Πόσο κρατά η αυτόματη επιβεβαίωση του WiFi. | Υπολογίζεται: 192 + 8 × 14. | 192 + 8·14 | derived |  |
| `UNICAST_DATA_NO_BACKOFF_US` | 1388 µs | Όλος ο χρόνος μιας αποστολής με επιβεβαίωση, χωρίς αναμονή. | Παύση + μήνυμα + παύση + επιβεβαίωση. | DIFS + DATA + SIFS + ACK | derived |  |
| `UNICAST_DATA_MEAN_US` | 1698 µs | Ο μέσος χρόνος μιας αποστολής, με τη μέση τυχαία αναμονή. | Προσθέτει τη μέση αναμονή (15,5 slots × 20 µs). | `+ μέσο backoff CW/2·slot` | derived |  |
| `CART_SPEC_AIRTIME_ERROR` | beacon 0,63 → 0,752 ms· data+ACK 1,2 → 1,338 ms  | Διόρθωση στους χρόνους του παλιού σχεδίου CART. | Το σχέδιο είχε ξεχάσει 15 bytes του ESP-NOW· οι σωστοί χρόνοι είναι λίγο μεγαλύτεροι. | `docs/superpowers/specs/2026-09-23-cart-v2-revision.md §2` | derived | το spec παρέλειψε 15 B vendor action header |
| `UART_FRAME_LINE_B` | 150 B | Πόσα bytes γράφει η γέφυρα στο Pi για κάθε μήνυμα. | Υπολογίζεται: JSON κείμενο + 122 χαρακτήρες hex + αλλαγή γραμμής = 150. | format + 2·61 hex + CRLF | derived |  |
| `UART_FRAME_LINE_MS` | 13.021 ms | Πόσο κρατά η αποστολή μιας γραμμής στο καλώδιο. | 150 bytes × 10 bits / 115200. | `10 bit/byte @ UART_BAUD` | derived |  |
| `BRIDGE_MAX_FRAMES_S` | 76.8 frames/s | Πόσα μηνύματα το δευτερόλεπτο χωράνε από τη γέφυρα στο Pi. | 1 / χρόνο γραμμής = ~77. Είναι το όριο όλου του δικτύου. | `1 / line time` | derived | ανώτατος ρυθμός γέφυρας (χωρίς USB echo) |
| `UART_ACK_LINE_B` | 62 – 68 B | Πόσα bytes στέλνει το Pi στη γέφυρα για κάθε επιβεβαίωση. | Υπολογίζεται από τη μορφή JSON· αλλάζει λίγο με τον αριθμό μηνύματος. | compact json.dumps + \n | derived | εύρος seq/ttl/ok |
| `BRIDGE_INGRESS_QUEUE` | 40 frames | Πόσα μηνύματα μπορούν να περιμένουν μέσα στη γέφυρα. | 8 + 32 θέσεις λήψης του radio. Αν γεμίσουν, τα επόμενα χάνονται. | static + dynamic RX buffers | derived | όσο το println μπλοκάρει, τα frames περιμένουν εδώ |
| `DEPTH_CEILING_RANK` | 65 rank | Το βαθύτερο επίπεδο από το οποίο φτάνουν ακόμα μηνύματα. | Υπολογίζεται από τους κανόνες TTL: με 16 σταματούσε στο 17, με 64 φτάνει το 65. | TTL κανόνες firmware + Pi | derived | βαθύτεροι κόμβοι δεν παραδίδουν / δεν παίρνουν ACK |
| `UNDELIVERED_NODES_SCENARIO` | 0 κόμβοι | Πόσοι κόμβοι του σεναρίου δεν θα έφταναν ποτέ στο Pi. | Όσοι είναι βαθύτερα από το όριο TTL (με 64 κανένας στο 50×10). | 50×10 με firmware TTL | derived |  |
| `FLOOD_REBROADCASTS_FW` | 137200 broadcasts/κύκλο | Πόσες αναμεταδόσεις επιβεβαιώσεων γίνονται σε κάθε κύκλο με τη σημερινή μέθοδο. | Υπολογίζεται: κάθε κόμβος αναμεταδίδει κάθε επιβεβαίωση μία φορά· μεγαλώνει με το τετράγωνο. | 50×10, όλοι relay, χωρίς απώλειες | derived | O(N²) |
| `FLOOD_REBROADCASTS_NO_TTL` | 137200 broadcasts/κύκλο | Το ίδιο, αν αφαιρεθεί το όριο TTL. | Δείχνει πόσο χειρότερο γίνεται σε βάθος 50. | 50×10, what-if MAX_TTL=255 | derived |  |
| `RANK1_SUBTREE` | 50 κόμβοι | Πόσα μηνύματα περνά κάθε κόμβος του πρώτου επιπέδου ανά κύκλο. | 500 κόμβοι / 10 του πρώτου επιπέδου = 50. | `N / W σε ισορροπημένο layered δέντρο` | derived | frames/κύκλο που περνά κάθε rank-1 relay |
| `BER_AT_SENSITIVITY` | 1.018e-05  | Πιθανότητα λάθους ανά bit στο πιο αδύναμο σήμα. | Υπολογίζεται από τον ορισμό του 8 %. | `1 − (1−FER)^(1/8192)` | derived |  |
| `EBN0_AT_SENSITIVITY_DB` | 10.335 dB | Πόσο πάνω από τον θόρυβο είναι το σήμα στο όριο. | Υπολογίζεται από τον τύπο της διαμόρφωσης. | `DBPSK: ln(1/(2·BER))` | derived |  |
| `PER_DATA_AT_SENSITIVITY` | 0.00843  | Πιθανότητα να χαθεί ένα μήνυμα μέτρησης στο πιο αδύναμο σήμα. | Υπολογίζεται· μικρότερη από 8 % γιατί το μήνυμά μας είναι μικρό. | 61 B frame στο −98,4 dBm | derived |  |
| `LINK_BUDGET_DB` | 118.4 dB | Πόση εξασθένηση αντέχει η σύνδεση. | Ισχύς εκπομπής (20) − ευαισθησία (−98,4) = 118 dB. | TX 20 dBm − sensitivity | derived |  |
| `MAH_DAY_PHASE1_LEAF_T300` | 18.79 mAh/day | Κατανάλωση ενός σημερινού κόμβου με κύκλο 5′. | Υπολογίζεται με το απλό μοντέλο. | lumped model | derived | έλεγχος: docs/analysis/cart_sim_results.txt |
| `MAH_DAY_PHASE1_LEAF_T900` | 7.26 mAh/day | Κατανάλωση ενός σημερινού κόμβου με κύκλο 15′. | Υπολογίζεται· ίδιο με το 7,26 του repo. | lumped model | derived | έλεγχος: docs/analysis/cart_sim_results.txt |
| `MAH_DAY_PHASE1_LEAF_T1800` | 4.38 mAh/day | Κατανάλωση ενός σημερινού κόμβου με κύκλο 30′. | Υπολογίζεται με το απλό μοντέλο. | lumped model | derived |  |
| `G_MAX_T900_MEASURED` | 3.98 s | Μέγιστο περιθώριο αφύπνισης στα 15′ με το μετρημένο drift. | 2 × 0,17 % × 900 s × 1,3. | 2·|b|·T·1,3 | derived | b = 0.17 % |
| `G_MAX_T900_PLANNING` | 14.04 s | Το ίδιο με την αρχική εκτίμηση drift. | 2 × 0,6 % × 900 s × 1,3. | 2·|b|·T·1,3 | derived | b = 0.60 % |
| `G_MAX_T1800_MEASURED` | 7.96 s | Μέγιστο περιθώριο αφύπνισης στα 30′ με το μετρημένο drift. | 2 × 0,17 % × 1800 s × 1,3. | 2·|b|·T·1,3 | derived | b = 0.17 % |
| `G_MAX_T1800_PLANNING` | 28.08 s | Το ίδιο με την αρχική εκτίμηση drift. | 2 × 0,6 % × 1800 s × 1,3. | 2·|b|·T·1,3 | derived | b = 0.60 % |
| `ALWAYS_ON_MAH_DAY` | 2400 mAh/day | Κατανάλωση κόμβου που δεν κοιμάται ποτέ. | 100 mA × 24 ώρες = 2400 mAh: αδειάζει την μπαταρία σε λιγότερο από μία μέρα. | 100 mA × 24 h | derived |  |

## H. Relay buffer: προϋπολογισμός RTC μνήμης

| Παράμετρος | Τιμή | Τι είναι | Γιατί αυτή η τιμή | Πηγή | kind | Τεχνική σημείωση |
|---|---|---|---|---|---|---|
| `RTC:meshRelayMagic` | 4 B | Μνήμη ύπνου που πιάνει η μεταβλητή meshRelayMagic του firmware. | Διαβάζεται από τον κώδικα: μέγεθος τύπου × πλήθος στοιχείων. | `firmware/libraries/GreenhouseMesh/mesh_node.h:171` | firmware | uint32_t |
| `RTC:meshRelayBuf` | 3050 B | Μνήμη ύπνου που πιάνει η μεταβλητή meshRelayBuf του firmware. | Διαβάζεται από τον κώδικα: μέγεθος τύπου × πλήθος στοιχείων. | `firmware/libraries/GreenhouseMesh/mesh_node.h:172` | firmware | uint8_t |
| `RTC:meshRelayCount` | 1 B | Μνήμη ύπνου που πιάνει η μεταβλητή meshRelayCount του firmware. | Διαβάζεται από τον κώδικα: μέγεθος τύπου × πλήθος στοιχείων. | `firmware/libraries/GreenhouseMesh/mesh_node.h:173` | firmware | uint8_t |
| `RTC:meshRelayHead` | 1 B | Μνήμη ύπνου που πιάνει η μεταβλητή meshRelayHead του firmware. | Διαβάζεται από τον κώδικα: μέγεθος τύπου × πλήθος στοιχείων. | `firmware/libraries/GreenhouseMesh/mesh_node.h:174` | firmware | uint8_t |
| `RTC:meshRtcState` | 632 B | Μνήμη ύπνου που πιάνει η κατάσταση του κόμβου. | Μετρημένη από τον κώδικα. | `firmware/libraries/GreenhouseMesh/mesh_node.h:684` | firmware | MeshRtcState |
| `RTC:meshSchedMagic` | 4 B | Μνήμη ύπνου που πιάνει η μεταβλητή meshSchedMagic του firmware. | Διαβάζεται από τον κώδικα: μέγεθος τύπου × πλήθος στοιχείων. | `firmware/libraries/GreenhouseMesh/mesh_cart.h:27` | firmware | uint32_t |
| `RTC:meshSchedParent` | 6 B | Μνήμη ύπνου που πιάνει η μεταβλητή meshSchedParent του firmware. | Διαβάζεται από τον κώδικα: μέγεθος τύπου × πλήθος στοιχείων. | `firmware/libraries/GreenhouseMesh/mesh_cart.h:28` | firmware | uint8_t |
| `RTC:meshSched` | 44 B | Μνήμη ύπνου που πιάνει η μεταβλητή meshSched του firmware. | Διαβάζεται από τον κώδικα: μέγεθος τύπου × πλήθος στοιχείων. | `firmware/libraries/GreenhouseMesh/mesh_cart.h:29` | firmware | MeshSchedState |
| `RTC:meshCartParentCycle` | 4 B | Μνήμη ύπνου που πιάνει η μεταβλητή meshCartParentCycle του firmware. | Διαβάζεται από τον κώδικα: μέγεθος τύπου × πλήθος στοιχείων. | `firmware/libraries/GreenhouseMesh/mesh_cart.h:30` | firmware | uint32_t |
| `RTC:meshCartSweepFails` | 1 B | Μνήμη ύπνου που πιάνει η μεταβλητή meshCartSweepFails του firmware. | Διαβάζεται από τον κώδικα: μέγεθος τύπου × πλήθος στοιχείων. | `firmware/libraries/GreenhouseMesh/mesh_cart.h:31` | firmware | uint8_t |
| `RTC:meshCartTxFailCycles` | 1 B | Μνήμη ύπνου που πιάνει η μεταβλητή meshCartTxFailCycles του firmware. | Διαβάζεται από τον κώδικα: μέγεθος τύπου × πλήθος στοιχείων. | `firmware/libraries/GreenhouseMesh/mesh_cart.h:32` | firmware | uint8_t |
| `RTC:g_unconfirmedWakes` | 1 B | Μνήμη ύπνου του μετρητή αποτυχημένων αφυπνίσεων. | 1 byte. | `firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino:63` | firmware | uint8_t |
| `RTC_OURS_B` | 3749 B | Πόση μνήμη ύπνου δηλώνει ο δικός μας κώδικας. | Άθροισμα όλων των μεταβλητών που επιβιώνουν τον ύπνο. | Σ RTC_DATA_ATTR | derived | ό,τι δηλώνει ο δικός μας κώδικας |
| `RTC_MEASURED_TOTAL_B` | 3848 B | Πόση μνήμη ύπνου πιάνει συνολικά το firmware, μετρημένη. | Μετρήθηκε στο πραγματικό compiled αρχείο (ELF) του αισθητήρα. | riscv32-esp-elf-size -A edge_node_esp32_c3.ino.elf (core 3.3.11, 2026-09-28) | measured | .rtc.text 20 + .rtc.data 3756 + .rtc.force_slow 32 + .rtc_reserved 40 |
| `RTC_IDF_MEASURED_B` | 92 B | Πόση μνήμη ύπνου παίρνει το λογισμικό της Espressif και του Arduino. | 92 B: μετρημένο· πολύ λιγότερο από όσο φοβόμασταν. | ίδια μέτρηση | measured | ό,τι παίρνουν ESP-IDF/Arduino/bootloader (όλα τα RTC sections εκτός .rtc.data) |
| `RTC_FREE_UPPER_BOUND_B` | 4351 B | Πόση μνήμη ύπνου μένει ελεύθερη. | 8192 − Espressif (μετρημένο) − ό,τι χρησιμοποιούμε. | 8192 − ESP-IDF (μετρημένο) − δικά μας | derived | ελεύθερη μνήμη ύπνου με τον τρέχοντα relay buffer |
| `RELAY_BUFFER_MAX_UPPER_BOUND` | 121 frames | Το μέγιστο που θα χωρούσε ο relay buffer στη μνήμη ύπνου. | Ελεύθερη μνήμη + σημερινός buffer, διά 61 B ανά μήνυμα (~121). | `⌊(ελεύθερη + σημερινός relay buffer) / 61⌋` | derived | πόσα frames θα χωρούσε ο relay buffer το πολύ |
| `RELAY_BUFFER_MIN_REQUIRED` | 50 frames | Το ελάχιστο που πρέπει να χωράει για το σενάριο 50×10. | Όσα μηνύματα περνά ο πιο φορτωμένος relay (50). | RANK1_SUBTREE | derived | T1 per-cycle: ένα rank-1 relay πρέπει να χωρέσει όλο το subtree του |
| `RELAY_FLUSH_TIME_S_AT_MIN` | 0.0849 s | Πόσο χρόνο θέλει ένας relay για να στείλει 50 μηνύματα. | 50 × 1,7 ms ≈ 0,085 s: πολύ λιγότερο από το όριο αφύπνισης. | B × UNICAST_DATA_MEAN | derived | έναντι MESH_WAKE_MAX_AWAKE_MS = 10000 ms |
| `BACK_TO_BACK_TX_LIMIT` | 32 frames | Πόσα μηνύματα μπορεί να στείλει στη σειρά χωρίς να περιμένει. | 32 θέσεις αποστολής του radio· πέρα από αυτές η αποστολή αποτυγχάνει. | dynamic TX buffers | derived | flush > 32 χωρίς αναμονή callback → ESP_ERR_ESPNOW_NO_MEM |

## I. Μητρώο εξαρτημάτων (επιλογές hardware για το calculator)

### Πλακέτα / sleep floor (`boards`)

| επιλογή | τιμές | πηγή | kind | σημείωση |
|---|---|---|---|---|
| `supermini_stock` | sleep_ma=3.05 | `docs/EDGE_NODE_POWER_OPTIMIZATION.md:39` | repo-doc | πλακέτα όπως έρχεται: power LED (~3 mA) + LDO + idle αισθητήρες |
| `supermini_led_removed` | sleep_ma=0.055 | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:137` | repo-doc | LED αφαιρεμένο· 55 µA = RTC + LDO quiescent (εκτίμηση, ΔΕΝ έχει μετρηθεί) |
| `supermini_ldo_bypass` | sleep_ma=0.01 | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:266-270 + ESP32-C3 Datasheet (Espressif) Table 5-9` | unverified | LiFePO4 κατευθείαν στο 3V3 (χωρίς LDO quiescent): chip 5 µA + ~5 µA διαρροές πλακέτας — εκτίμηση, πρέπει να μετρηθεί (PPK2/µCurrent) |
| `bare_chip_ideal` | sleep_ma=0.005 | ESP32-C3 Datasheet (Espressif), Table 5-9 | datasheet | θεωρητικό όριο: μόνο το chip σε deep sleep (RTC timer + RTC memory) |

### Divider μπαταρίας (`dividers`)

| επιλογή | τιμές | πηγή | kind | σημείωση |
|---|---|---|---|---|
| `220k_x2` | sleep_ma=0.0075 | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:46` | repo-doc | 3,3 V / 440 kΩ, πάντα ON (σημερινό) |
| `1M_x2` | sleep_ma=0.00165 | `3,3 V / 2 MΩ (Ohm)` | derived | χρειάζεται πυκνωτή 100 nF στο ADC pin για σωστή ανάγνωση |
| `switched` | sleep_ma=0.0001 | P-MOSFET high-side switch (εκτίμηση διαρροής) | unverified | ρεύμα μόνο κατά την ανάγνωση (16 ms)· +1 εξάρτημα |
| `none` | sleep_ma=0 | — | model | χωρίς μέτρηση μπαταρίας |

### Ρολόι RTC (`rtc_clocks`)

| επιλογή | τιμές | πηγή | kind | σημείωση |
|---|---|---|---|---|
| `rc136k` | sleep_ma=0 | sdkconfig CONFIG_RTC_CLK_SRC_INT_RC | toolchain | σημερινό· drift 0,17 % (Gate 0 run 1) |
| `rc_fast_d256` | sleep_ma=0.005 | `docs/superpowers/specs/2026-09-23-cart-v2-revision.md §2` | datasheet | +5 µA, καλύτερη σταθερότητα (Gate 0 run 2 θα το μετρήσει) |

### Αισθητήρας αέρα (`climate_sensors`)

| επιλογή | τιμές | πηγή | kind | σημείωση |
|---|---|---|---|---|
| `dht22` | active_ma=1.5, settle_s=2, read_s=0.006 | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:44 + firmware SENSOR_WARMUP_MS` | repo-doc | σημερινό· το 2 s warm-up είναι το 80 % του ξύπνιου χρόνου |
| `sht40` | active_ma=0.32, settle_s=0.001, read_s=0.0083 | `Sensirion SHT4x datasheet v6.4 (Nov 2023), Table 3/4` | datasheet | power-up ≤1 ms, μέτρηση high repeatability ≤8,3 ms @ 320 µA· I²C |
| `bme280` | active_ma=0.714, settle_s=0.002, read_s=0.0093 | Bosch BME280 datasheet BST-BME280-DS001-23 rev 1.23, Table 1 + §9.1 | datasheet | forced mode T+P+H ×1: ≤9,3 ms· 714 µA (χειρότερη φάση)· + βαρομετρική πίεση |

### Αισθητήρας εδάφους (`soil_sensors`)

| επιλογή | τιμές | πηγή | kind | σημείωση |
|---|---|---|---|---|
| `capacitive_v12` | active_ma=5, settle_s=0.1 | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:45` | unverified | 5 mA (repo)· χρόνος σταθεροποίησης 0,1 s ΔΕΝ είναι μετρημένος (σήμερα 'κρύβεται' στα 2 s του DHT22) |
| `none` | active_ma=0, settle_s=0 | — | model |  |

### MCU στο warm-up (`warmup_modes`)

| επιλογή | τιμές | πηγή | kind | σημείωση |
|---|---|---|---|---|
| `radio_on` |  |  | firmware | σημερινό firmware: ESP-NOW ανοιχτό κατά το warm-up (I_RX) |
| `cpu_idle` |  |  | datasheet | radio κλειστό, CPU σε delay (modem-sleep idle) |
| `light_sleep` |  |  | datasheet | light sleep με GPIO hold στους αισθητήρες (130 µA chip) |

### Μπαταρία (`batteries`)

| επιλογή | τιμές | πηγή | kind | σημείωση |
|---|---|---|---|---|
| `lifepo4_18650_1500` | mah=1500, v=3.2, dod=0.8, self_discharge_pct_month=3 | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:47,167` | repo-doc | σημερινό· αυτοεκφόρτιση 3 %/μήνα = τυπική LiFePO4 (μη μετρημένη) |
| `lifepo4_26650_3000` | mah=3000, v=3.2, dod=0.8, self_discharge_pct_month=3 | τυπική χωρητικότητα αγοράς 26650 LiFePO4 | unverified | ίδια χημεία/τάση → ίδιο firmware, μεγαλύτερη θήκη |

### Ηλιακό πάνελ (`solar_panels`)

| επιλογή | τιμές | πηγή | kind | σημείωση |
|---|---|---|---|---|
| `none` | w=0 | — | model |  |
| `6v_2w` | w=2 | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:222-231` | repo-doc | σημερινό |
| `5v_1w` | w=1 | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:49` | repo-doc | 5 V: κίνδυνος να πέσει κάτω από το dropout του TP5000 σε ζέστη |

### Ηλιοφάνεια (`sun`)

| επιλογή | τιμές | πηγή | kind | σημείωση |
|---|---|---|---|---|
| `athens_winter` | psh=2 | `docs/SENSOR_NODE_POWER_AND_SOLAR.md:224-226` | repo-doc |  |
| `athens_summer` | psh=6.5 | τυπική τιμή PVGIS για Αθήνα (Ιούλιος) — προσέγγιση | unverified |  |
| `indoor_shade` | psh=0.3 | `υπόθεση: σκιά φυλλώματος/εσωτερικό θερμοκηπίου` | model |  |

### Pi (`pi`)

| επιλογή | τιμές | πηγή | kind | σημείωση |
|---|---|---|---|---|
| `pi_zero_w` | wh_day=30 | `docs/HARDWARE_PARTS_LIST.md:182` | repo-doc | ~30 Wh/ημέρα ≈ 1,25 W μέσος όρος |

### Γέφυρα (`bridge_board`)

| επιλογή | τιμές | πηγή | kind | σημείωση |
|---|---|---|---|---|
| `esp32c3_supermini` | i_ma=84, v=5 | ESP32-C3 Datasheet (Espressif), Table 5-7 (RX) | datasheet | πάντα σε RX· τροφοδοσία από τα 5 V του Pi μέσω LDO (ίδιο ρεύμα) |

### Off-grid gateway (`offgrid`)

| επιλογή | τιμές | πηγή | kind | σημείωση |
|---|---|---|---|---|
| `pack_12v_lifepo4_6ah` | wh=76.8 | `docs/HARDWARE_PARTS_LIST.md:182` | repo-doc |  |
| `pack_12v_lifepo4_12ah` | wh=153.6 | `docs/HARDWARE_PARTS_LIST.md:182` | repo-doc |  |
| `panel_20w` | w=20 | `docs/HARDWARE_PARTS_LIST.md:190` | repo-doc |  |

## J. Μεταβλητές run (`--set key=value`)

Κάθε run: defaults ← preset ← `--set`. Όλα καταγράφονται στο `sim/runs/<run>/config.json`.

| key | default | Τι είναι | Γιατί αυτή η default | επιλογές |
|---|---|---|---|---|
| `net.ranks` | 50 | Πόσα επίπεδα απόστασης από τη γέφυρα. | 50: το σενάριο αντοχής που ζητήθηκε· τα presets βάζουν ρεαλιστικά 2–4. |  |
| `net.per_rank` | 10 | Κόμβοι σε κάθε επίπεδο. | 10: το σενάριο που ζητήθηκε. |  |
| `net.max_children` | 3 | Πόσα παιδιά έχει ο πιο φορτωμένος parent. | 3: συντηρητική εκτίμηση για τις συγκρούσεις. |  |
| `net.hidden_frac` | 0.3 | Τι ποσοστό γειτόνων δεν ακούνε ο ένας τον άλλο. | 30 %: τυπικό σε χώρο με εμπόδια· δεν έχει μετρηθεί. |  |
| `timing.T_s` | 900 | Κάθε πόσα δευτερόλεπτα ξυπνά το δίκτυο. | 900 (15′): το ένα από τα δύο standards. |  |
| `timing.report_every` | 1 | Κάθε πόσες μετρήσεις στέλνει. | 1: στέλνει κάθε μέτρηση, όπως σήμερα. |  |
| `timing.t_boot_s` | 0.2 | Χρόνος εκκίνησης μετά τον ύπνο. | 0,2 s: μέση της βιβλιογραφίας (140–230 ms). |  |
| `timing.radio_init_s` | 0.1 | Χρόνος για να ανοίξει το radio. | 0,1 s: εκτίμηση, δεν έχει μετρηθεί. |  |
| `timing.awake_cap_s` | 10 | Μέγιστος χρόνος ξύπνιος. | 10 s: από το firmware. |  |
| `timing.app_ack_wait_s` | 2 | Πόσο περιμένει την απάντηση του Pi. | 2 s: από το firmware. |  |
| `scheme.technique` | T1-ladder | Η τεχνική (T1 σκάλα, T2 παράθυρο, ή το σημερινό phase1). | T1: ο προσομοιωτής έδειξε ότι κλιμακώνεται σε βάθος. | phase1 \| T1-ladder \| T2-window |
| `scheme.t2_ack` | unicast | Πώς γυρίζει η επιβεβαίωση στην T2. | unicast: πολύ λιγότερη κίνηση από το flood. | flood \| unicast \| aggregate |
| `scheme.t1_hop_ack` | per_frame | Επιβεβαίωση ανά μήνυμα ή ανά ριπή στην T1. | Ανά μήνυμα: πιο απλό και ασφαλές. | per_frame \| batch |
| `scheme.max_ttl` | 64 | Όριο hops. | 64: όπως το firmware· βάλε 16 για να δεις το παλιό ταβάνι στο rank 17. |  |
| `scheme.ttl_margin` | 2 | Περιθώριο hops. | 2: από το firmware. |  |
| `scheme.relay_buffer` | 50 | Buffer του parent σε μηνύματα. | 50: χωράει στη μνήμη ύπνου και καλύπτει το 50×10. |  |
| `scheme.own_buffer` | 10 | Buffer δικών μετρήσεων. | 10: από το firmware. |  |
| `sync.bias` | 0.0017 | Σταθερή διαφορά ρολογιών. | 0,17 %: μετρημένο στο bench. |  |
| `sync.step_per_300s` | 0.0001 | Πόσο αλλάζει το ρολόι από κύκλο σε κύκλο. | 0,01 %: εκτίμηση μέχρι το Gate 0 run 2. |  |
| `sync.policy` | margin | Πώς προσαρμόζεται το περιθώριο αφύπνισης. | margin: καλύτερο σε όλες τις δοκιμές του cart_sim. | margin \| aimd |
| `sync.g_min_s` | 0.25 | Ελάχιστο περιθώριο. | 0,25 s: η διακύμανση εκκίνησης. |  |
| `sync.g_max_s` | — | Μέγιστο περιθώριο (κενό = από τον κανόνα). | Κενό: υπολογίζεται από το drift. |  |
| `sync.g_max_rule` | cart_v2 | Κανόνας για το μέγιστο περιθώριο. | cart_v2 όπως το σχέδιο· το bias_wander (διόρθωση) μειώνει την ενέργεια στο μισό. | cart_v2 \| bias_wander |
| `sync.cycles` | 20000 | Κύκλοι της προσομοίωσης ρολογιών. | 20.000: σταθερά στατιστικά σε κλάσματα του δευτερολέπτου. |  |
| `sync.z` | 3 | Πόσες τυπικές αποκλίσεις κάλυψη. | 3: καλύπτει το 99,7 %. |  |
| `radio.link_margin_db` | 10 | Πόσο δυνατότερο από το όριο είναι το σήμα σε κάθε σύνδεση. | 10 dB: συνηθισμένο περιθώριο σχεδιασμού. |  |
| `radio.mac_retry` | 5 | Αυτόματες επαναλήψεις του radio. | 5: αναφορές χρηστών (όχι επίσημο). |  |
| `radio.cw` | 31 | Εύρος τυχαίας αναμονής. | 31: από το πρότυπο WiFi. |  |
| `radio.jitter_s` | 0.3 | Τυχαία καθυστέρηση πριν την αποστολή. | 0,3 s: όπως το firmware· περισσότερο = λιγότερες συγκρούσεις. |  |
| `radio.attempts` | 3 | Προσπάθειες μέσα στο παράθυρο. | 3: από το σχέδιο CART. |  |
| `radio.hop_proc_s` | 0.002 | Χρόνος επεξεργασίας ανά hop. | 2 ms: εκτίμηση για τον έλεγχο υπογραφής. |  |
| `bridge.baud` | 115200 | Ταχύτητα γέφυρας–Pi. | 115200: όπως σήμερα. |  |
| `bridge.framing` | hex_json | Μορφή γραμμής UART. | hex_json: όπως σήμερα. | hex_json \| binary |
| `bridge.usb_echo` | False | Αν η γέφυρα γράφει και στο USB. | Όχι: στην εγκατάσταση δεν είναι συνδεδεμένο USB. |  |
| `bridge.ingress_queue` | 40 | Ουρά εισόδου γέφυρας. | 40: οι θέσεις λήψης του radio. |  |
| `pi.process_s` | 0.02 | Χρόνος Pi ανά μήνυμα. | 0,02 s: εκτίμηση, δεν έχει μετρηθεί. |  |
| `hw.board` | supermini_led_removed | Ποια πλακέτα και πόσο ρεύμα τραβά στον ύπνο. | Χωρίς LED (55 µA): όπως είναι σήμερα οι κόμβοι. | supermini_stock \| supermini_led_removed \| supermini_ldo_bypass \| bare_chip_ideal |
| `hw.divider` | 220k_x2 | Πώς μετράει την μπαταρία. | 220k: όπως σήμερα. | 220k_x2 \| 1M_x2 \| switched \| none |
| `hw.rtc_clock` | rc136k | Ρολόι ύπνου. | rc136k: όπως σήμερα. | rc136k \| rc_fast_d256 |
| `hw.climate_sensor` | dht22 | Αισθητήρας αέρα. | dht22: όπως σήμερα. | dht22 \| sht40 \| bme280 |
| `hw.soil_sensor` | capacitive_v12 | Αισθητήρας εδάφους. | capacitive_v12: όπως σήμερα. | capacitive_v12 \| none |
| `hw.warmup_mode` | radio_on | Τι κάνει το chip όσο ζεσταίνονται οι αισθητήρες. | radio_on: όπως το σημερινό firmware. | radio_on \| cpu_idle \| light_sleep |
| `hw.battery` | lifepo4_18650_1500 | Μπαταρία. | LiFePO4 18650 1500 mAh: όπως σήμερα. | lifepo4_18650_1500 \| lifepo4_26650_3000 |
| `hw.battery_mah` | — | Αλλαγή χωρητικότητας. | Κενό: από τη μπαταρία που επιλέχθηκε. |  |
| `hw.solar` | 6v_2w | Ηλιακό πάνελ. | 6 V / 2 W: όπως σήμερα. | none \| 6v_2w \| 5v_1w |
| `hw.sun` | athens_winter | Ηλιοφάνεια. | Αθήνα χειμώνας: η χειρότερη περίπτωση. | athens_winter \| athens_summer \| indoor_shade |
| `hw.solar_derate` | 0.5 | Απώλειες ηλιακού. | 50 %: σύννεφα, σκόνη, γωνία. |  |
| `hw.charger_eff` | 0.7 | Απόδοση φορτιστή. | 70 %: τυπική για TP5000. |  |
| `energy.model` | per_state | Τρόπος υπολογισμού ενέργειας. | Αναλυτικός: πιο ακριβής, με ρεύματα datasheet. | per_state \| lumped |
| `energy.i_tx_ma` | 335 | Ρεύμα εκπομπής. | 335 mA: datasheet. |  |
| `energy.i_rx_ma` | 84 | Ρεύμα ακρόασης. | 84 mA: datasheet. |  |
| `energy.i_cpu_ma` | 23 | Ρεύμα επεξεργαστή. | 23 mA: datasheet. |  |
| `energy.i_cpu_idle_ma` | 16 | Ρεύμα επεξεργαστή σε αναμονή. | 16 mA: datasheet. |  |
| `energy.i_light_sleep_ma` | 0.13 | Ρεύμα ελαφρού ύπνου. | 0,13 mA: datasheet. |  |
| `energy.i_active_lumped_ma` | 86.5 | Ενιαίο ρεύμα του απλού μοντέλου. | 86,5 mA: από το repo. |  |
| `energy.phase1_lumped_awake_s` | 2.5 | Χρόνος ξύπνιος στο απλό μοντέλο. | 2,5 s: από το repo. |  |
| `des.cycles` | 3 | Κύκλοι της προσομοίωσης γεγονότων. | 3: γρήγορο· βάλε 20+ για πιο σταθερά νούμερα. |  |
| `des.seed` | 1 | Αριθμός τύχης. | 1: ίδιο seed δίνει ίδιο αποτέλεσμα. |  |
| `des.seeds` | 1 | Επαναλήψεις με διαφορετική τύχη. | 1: γρήγορο· 5+ δίνει διάστημα εμπιστοσύνης. |  |
| `des.trace_cycles` | 1 | Κύκλοι που φαίνονται στο timeline. | 1: αρκεί για να δεις τη μορφή. |  |
| `des.shadow_sigma_db` | 4 | Τυχαίες διαφορές σήματος. | 4 dB: τυπική τιμή. |  |
| `des.window_s` | — | Μήκος παραθύρου στην T2. | Κενό: από τον calculator. |  |
| `des.clock_burnin` | 64 | Κύκλοι «προθέρμανσης» των ρολογιών. | 64: ώστε τα ρολόγια να ξεκινούν σε σταθερή κατάσταση. |  |
| `des.sweep_energy` | expected | Πώς μετράει την ενέργεια των σπάνιων σαρώσεων. | Αναμενόμενη: λίγοι κύκλοι δεν αρκούν για να τις δεις. | expected \| observed |
| `req.lifetime_days` | 365 | Στόχος αυτονομίας χωρίς ήλιο. | 365 ημέρες: ένας χρόνος χωρίς αλλαγή μπαταρίας. |  |
| `req.latency_s` | 900 | Μέγιστη αποδεκτή καθυστέρηση. | 900 s: μία μέτρηση ανά κύκλο. |  |
| `req.pdr` | 0.99 | Ελάχιστο ποσοστό μετρήσεων που πρέπει να φτάνουν. | 99 %: συνήθης στόχος για αισθητήρες. |  |

### Presets

- **`firmware_today`** — Baseline: ό,τι τρέχει σήμερα στο bench (Phase 1, 3 αισθητήρες, T = 60 s test): `net.ranks=1`, `net.per_rank=3`, `timing.T_s=60`, `scheme.technique=phase1`
- **`stress_50x10`** — Το stress-test: 50 ranks × 10 κόμβοι, όλοι sleepy + relay, 15′: `net.ranks=50`, `net.per_rank=10`, `timing.T_s=900`, `scheme.technique=T1-ladder`
- **`greenhouse`** — Θερμοκήπιο: ~40 κόμβοι, 4 hops, 15′ (αρχικό σημείο — επιβεβαίωση από γεωπόνο): `net.ranks=4`, `net.per_rank=10`, `timing.T_s=900`, `scheme.technique=T2-window`, `req.latency_s=900`
- **`nursery`** — Φυτώριο: πυκνό, ρηχό (3 hops × 15), 15′: `net.ranks=3`, `net.per_rank=15`, `timing.T_s=900`, `scheme.technique=T2-window`, `req.pdr=0.995`
- **`field`** — Χωράφι: αραιό, αργή δυναμική εδάφους, 30′ (μεγάλες αποστάσεις → LoRa ανά τμήμα): `net.ranks=2`, `net.per_rank=10`, `timing.T_s=1800`, `scheme.technique=T2-window`, `req.latency_s=1800`, `hw.sun=athens_winter`

