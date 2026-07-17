# 02 — ESP-NOW σε Επίπεδα OSI

Το ESP-NOW είναι ένα proprietary πρωτόκολλο της Espressif πάνω από το
802.11 radio, **χωρίς** IP/association/DHCP. Δεν είναι Wi-Fi με την κλασική
έννοια (δεν υπάρχει router/AP ενδιάμεσα) — είναι peer-to-peer επικοινωνία
απευθείας πάνω στο MAC layer. Παρακάτω η ανάλυση σε OSI layers, στο βαθμό
που έχει νόημα (τα layers 3-4 ουσιαστικά δεν υπάρχουν σε αυτό το πρωτόκολλο).

## Layer 1 — Physical

- **Φάσμα:** 2.4 GHz ISM band, το ίδιο PHY hardware με 802.11b/g/n.
- **Διαμόρφωση:** επαναχρησιμοποιεί το 802.11 PHY (DSSS για 802.11b rates,
  OFDM για g/n rates) — το ESP-NOW **δεν** ορίζει δικό του PHY, "δανείζεται"
  αυτό του 802.11.
- **Κανάλι:** 1 από τα 13 κανάλια 2.4GHz (Ελλάδα/ΕΕ regulatory domain,
  ρυθμισμένο ρητά με `iw reg set GR` στο side του Pi/AP — δες
  `pi/scripts/ap_up.sh:21`). Όλοι οι κόμβοι + η γέφυρα πρέπει να μοιράζονται
  το ίδιο κανάλι για να ακούγονται (βλ. `01-sensor-node-hardware.md §2`).

## Layer 2 — Data Link (εδώ ζει το ESP-NOW)

- **Frame type:** το ESP-NOW μεταφέρει το payload του μέσα σε 802.11
  **Action Frames** (vendor-specific management frames) — όχι σε κανονικά
  data frames όπως το IP-over-WiFi. Αυτός είναι ο λόγος που δουλεύει χωρίς
  association: τα management frames δεν απαιτούν προηγούμενο 4-way handshake.
- **Διευθυνσιοδότηση:** αμιγώς MAC-based, 6-byte διευθύνσεις. Δύο τρόποι
  αποστολής:
  - **Unicast** σε συγκεκριμένη MAC — απαιτεί προηγούμενη εγγραφή peer
    (`esp_now_add_peer()`) πριν σταλεί οτιδήποτε.
  - **Broadcast** στη διεύθυνση `FF:FF:FF:FF:FF:FF`
    (`mesh_node.h:47`, `MESH_BCAST`) — φτάνει σε όλους τους δέκτες στο ίδιο
    κανάλι, χρησιμοποιείται αποκλειστικά για τα beacons.
- **Μέγιστο μέγεθος payload:** το ESP-NOW (v1, IDF ≤4.x / Arduino core
  τρέχουσας γενιάς) περιορίζει το payload σε **250 bytes**. Τα δύο structs
  του project χωράνε άνετα:
  - `MeshBeacon` = 18 bytes (`mesh_node.h:35`)
  - `MeshDataPacket` = 23 bytes (`mesh_node.h:45`)

  Το μέγεθος του πακέτου (18 vs 23 bytes) χρησιμοποιείται και ως **φτηνός
  διαχωριστής τύπου** στο callback λήψης, αντί για ξεχωριστό πεδίο "τύπος
  μηνύματος" (`onDataRecv()`, `edge_node_esp32_c3.ino:58-69`
  και `bridge_esp32.ino:74-79`): `len == sizeof(MeshBeacon)` → beacon,
  `len == sizeof(MeshDataPacket)` → data.
- **Επιβεβαίωση παράδοσης (ACK):** το ESP-NOW δίνει ένα L2 delivery-status
  callback ανά unicast αποστολή (`esp_now_register_send_cb()`) — δυαδικό
  success/fail, **όχι** αυτόματο retry. Ο κώδικας το αξιοποιεί στο
  `onDataSent()` → `meshNotifyTxStatus()` (`edge_node_esp32_c3.ino:54-56`,
  `mesh_node.h:246-252`): 3 συνεχόμενες αποτυχίες unicast → ο γονέας
  θεωρείται νεκρός άμεσα, χωρίς να περιμένει το πλήρες beacon-timeout.
  Broadcast frames δεν έχουν ποτέ αυτό το callback ως failure (πάντα
  αναφέρουν επιτυχία στο επίπεδο ESP-NOW, καθώς δεν υπάρχει συγκεκριμένος
  παραλήπτης να επιβεβαιώσει).
- **Κρυπτογράφηση:** AES-128-CTR (CCMP-style) σε επίπεδο ESP-NOW peer, με
  δύο κλειδιά:
  - **PMK** (Primary Master Key, 16 bytes) — καθολικό, ένα ανά συσκευή,
    ορίζεται με `esp_now_set_pmk()` πριν από κάθε `add_peer` (`mesh_node.h:100`).
  - **LMK** (Local Master Key, 16 bytes) — το πραγματικό κλειδί κρυπτογράφησης
    ανά peer, ίδιο και δίκτυο-ευρείας-εμβέλειας εδώ (καμία ανά-ζεύγος
    μοναδικότητα — βλ. `10-security.md` για την πλήρη ανάλυση trade-off).
  - **Broadcast frames δεν κρυπτογραφούνται ποτέ** — hardware/protocol
    περιορισμός, όχι επιλογή σχεδιασμού (`bcast.encrypt = false`,
    `mesh_node.h:105`). Γι' αυτό τα beacons (broadcast) είναι πάντα
    plaintext, ενώ το πραγματικό sensor data (πάντα unicast προς τον γονέα)
    είναι πάντα κρυπτογραφημένο.

## Layer 3 — Network

**Δεν υπάρχει.** Το ESP-NOW δεν έχει IP addressing, δεν έχει subnetting,
δεν έχει routing πρωτόκολλο στο δικό του επίπεδο. Η "δρομολόγηση" που κάνει
το mesh (ποιος στέλνει σε ποιον) είναι αμιγώς **application-layer λογική**
πάνω από αυτό το επίπεδο-2 πρωτόκολλο — βλ. `03-mesh-routing.md`. Αυτό
είναι θεμελιωδώς διαφορετικό από IP routing (πχ RIP/OSPF), όπου η
δρομολόγηση συμβαίνει στο layer 3 με δικές του διευθύνσεις· εδώ ο "επόμενος
κόμβος" προσδιορίζεται απευθείας με MAC address σε ένα custom struct.

## Layer 4 — Transport

**Ουσιαστικά δεν υπάρχει.** Δεν υπάρχει connection state, δεν υπάρχει flow
control, δεν υπάρχει windowing, δεν υπάρχει αυτόματο retransmission πέρα
από το single ACK/NACK ανά πακέτο του layer 2. Οτιδήποτε μοιάζει με
αξιοπιστία (buffering αναγνώσεων όταν δεν υπάρχει γονέας, retry στο επόμενο
κύκλο) είναι χτισμένο **στο application layer** (`meshBufferPush()`,
`meshFlushBuffer()`, `mesh_node.h:274-288`).

## Layers 5-7 — Session / Presentation / Application

Custom, stateless-per-πακέτο πρωτόκολλο ορισμένο εξ ολοκλήρου στο
`mesh_node.h`:
- **Session:** ανύπαρκτη έννοια — κάθε πακέτο είναι ανεξάρτητο, δεν υπάρχει
  "handshake" πέρα από την αρχική εγγραφή peer.
- **Presentation:** raw C structs, `__attribute__((packed))` ώστε να μην
  προστίθεται padding από τον compiler — το byte layout είναι
  προβλέψιμο και ίδιο σε όλες τις συσκευές (ίδιο chip family, ίδιο
  little-endian, άρα δεν χρειάζεται δικό του serialization format όπως
  Protobuf/CBOR).
- **Application:** δύο τύποι μηνυμάτων —
  `MeshBeacon` (ανακάλυψη γειτόνων + διαφήμιση rank) και
  `MeshDataPacket` (μεταφορά πραγματικής μέτρησης, με δυνατότητα πολλαπλών
  hops). Πλήρης ανάλυση στο `03-mesh-routing.md`.

## Γιατί ESP-NOW και όχι κάτι άλλο

Το design spec (`docs/superpowers/specs/2026-07-09-dynamic-mesh-relay-design.md`,
ενότητα "Methods considered and ruled out") καταγράφει ρητά τι εξετάστηκε
και γιατί απορρίφθηκε:

| Εναλλακτική | Γιατί απορρίφθηκε |
|---|---|
| **BLE Mesh (Friend/LPN)** | Το πιο ενεργειακά αποδοτικό, αλλά απαιτεί ασύμμετρους ρόλους (dedicated πάντα-ξύπνιους "Friend" κόμβους) — έρχεται σε αντίθεση με την απαίτηση "οποιοσδήποτε κόμβος μπορεί να κάνει relay" |
| **802.11s / πλήρες WiFi mesh** | Απαιτεί πλήρη association + WPA handshake ανά ζεύγος, πολύ μεγαλύτερη κατανάλωση ενέργειας και πολυπλοκότητα από αυτό που χρειάζεται ένα θερμοκήπιο λίγων κόμβων |
| **Zigbee / Thread** | Διαφορετικό radio band/hardware (802.15.4) — το ESP32 δεν το έχει built-in, θα χρειαζόταν ξεχωριστό chip ραδιοεπικοινωνίας |
| **LoRa** | Χαμηλότερο bandwidth, μεγαλύτερο latency, θα χρειαζόταν ξεχωριστό module/chip — η εμβέλεια ενός LoRa δεν χρειάζεται καν σε ένα θερμοκήπιο |
| **Preamble-sampling MAC (X-MAC/ContikiMAC/WiseMAC)** | Υποθέτουν κόστος αφύπνισης σε μικρο-δευτερόλεπτα· το ESP32-C3 χρειάζεται ~140-230ms deep-sleep→app_main πριν καν αρχικοποιηθεί το radio — ασύμβατο μέγεθος κλίμακας |
| **Συγχρονισμένο flooding (Glossy/LWB/Chaos)** | Απαιτεί sub-millisecond, interrupt-level χρονοσυγχρονισμό που δεν εκθέτει το Arduino/ESP-NOW API |

Το ESP-NOW κέρδισε επειδή: (α) είναι **ήδη ενσωματωμένο** σε κάθε ESP32
δωρεάν (καμία επιπλέον δαπάνη hardware), (β) connectionless με πολύ χαμηλό
latency (χιλιοστά του δευτερολέπτου, όχι δευτερόλεπτα handshake), (γ) το
peer-to-peer μοντέλο του ταιριάζει φυσικά με την απαίτηση "οποιοσδήποτε
κόμβος να μπορεί να γίνει relay για οποιονδήποτε άλλον".
