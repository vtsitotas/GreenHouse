# 03 — Αλγόριθμος Mesh Routing (Rank / Beacon / Trickle)

Τεχνική εκδοχή του `docs/MESH_RELAY_EXPLAINED.md`, σε επίπεδο δομών δεδομένων
και αλγορίθμου. Πηγή: `firmware/libraries/GreenhouseMesh/mesh_node.h` +
`mesh_config.h`.

## 1. Δομές δεδομένων επί του σύρματος (wire format)

```c
// Broadcast, πάντα plaintext. Μόνο ανακάλυψη γειτόνων + διαφήμιση rank.
typedef struct __attribute__((packed)) {
  uint8_t  magic;               // MESH_MAGIC = 0x47 ('G') — sanity marker
  uint8_t  mac[6];               // MAC αποστολέα (πληροφοριακό)
  uint8_t  rank;                 // τρέχον rank αποστολέα (255 = unrouted)
  uint16_t seq;                  // μονοτονικός μετρητής ανά αποστολέα
  uint32_t beacon_interval_ms;   // πότε θα στείλει το ΕΠΟΜΕΝΟ beacon
  uint32_t window_duration_ms;   // forward-compat για μελλοντικό deep sleep
} MeshBeacon;                    // 18 bytes

// Unicast προς τον επιλεγμένο γονέα, πάντα κρυπτογραφημένο (PMK/LMK).
typedef struct __attribute__((packed)) {
  uint8_t      magic;
  uint8_t      origin_mac[6];    // ΠΟΙΟΣ μέτρησε (όχι ο ενδιάμεσος relay)
  uint8_t      origin_rank;      // rank του origin τη στιγμή αποστολής (diagnostics)
  uint8_t      ttl;               // μειώνεται ανά hop, drop στο 0
  uint16_t     seq;               // ανά-origin μετρητής, για de-dup
  SensorPacket payload;           // {temperature, humidity, soil_moisture}
} MeshDataPacket;                 // 23 bytes
```

Το μέγεθος (18 vs 23 bytes) λειτουργεί ως έμμεσος διαχωριστής τύπου
μηνύματος στο receive callback — δες `02-esp-now-protocol.md §Layer 2`.

## 2. Καταστάσεις ανά κόμβο (per-node state)

Κάθε non-bridge κόμβος κρατά (static globals στο `mesh_node.h:50-73`):

| Μεταβλητή | Σημασία |
|---|---|
| `meshMyRank` | τρέχον rank (255 = `MESH_RANK_UNROUTED`) |
| `meshParentIdx` | index στο `TRUSTED_NODES[]` του τρέχοντος γονέα, -1 = κανένας |
| `meshParentRank`, `meshParentRssi`, `meshParentIntervalMs` | στοιχεία του γονέα, ανανεώνονται σε κάθε ακουσμένο beacon |
| `meshBeaconIntervalMs` | τρέχον trickle interval (2000–60000ms) |
| `meshNeighborLastHeard[]` | timestamp τελευταίου beacon ανά trusted γείτονα |
| `meshDedup[]` | δαχτυλίδι 32 εγγραφών `(mac, seq)` για de-dup |
| `meshBuf[]` | δαχτυλίδι 10 εγγραφών `MeshDataPacket` — δικές του μετρήσεις όσο είναι unrouted |

Η γέφυρα δεν έχει καθόλου αυτή τη λογική επιλογής γονέα — είναι πάντα
`rank = 0`, hardcoded άγκυρα.

## 3. Αλγόριθμος επιλογής γονέα (parent selection)

Pseudocode βασισμένο στο `meshHandleBeacon()` (`mesh_node.h:181-231`):

```
όταν ληφθεί beacon B από MAC src:
  αν src ΔΕΝ είναι στο TRUSTED_NODES[]:
      αγνόησέ το εντελώς (δεν γίνεται ποτέ candidate)
      return

  αν src == τρέχων_γονέας:
      ανανέωσε liveness/rssi/interval του γονέα
      αν B.rank == UNROUTED:
          drop τον γονέα, ξαναγίνε unrouted
      αλλιώς αν B.rank + 1 != το_δικό_μου_rank:
          ακολούθησε την αλλαγή rank του γονέα (rank = B.rank + 1)
      return

  // src είναι πιθανός νέος γονέας
  αν B.rank >= το_δικό_μου_rank:
      απόρριψε — ΚΑΝΟΝΑΣ ΑΥΣΤΗΡΗΣ ΤΑΞΗΣ (βλ. §4)
      return

  αν έχω ήδη γονέα ΚΑΙ ο νέος δεν είναι καλύτερος
     (χαμηλότερο rank, ή ίδιο rank με καλύτερο RSSI):
      απόρριψε
      return

  υιοθέτησε τον src ως νέο γονέα:
      rank = B.rank + 1
      επαναφορά trickle timer στο ελάχιστο (2s)
```

Κριτήριο επιλογής μεταξύ πολλαπλών έγκυρων υποψηφίων: **πρώτα χαμηλότερο
rank, μετά (ισοπαλία rank) καλύτερο RSSI** (`mesh_node.h:227-229`). Δεν
μετράται ποτέ round-trip time/latency — μόνο η ισχύς λαμβανόμενου σήματος,
που παρέχεται δωρεάν από το ESP-NOW receive callback
(`info->rx_ctrl->rssi`, `edge_node_esp32_c3.ino:60`).

## 4. Γιατί οι βρόχοι (loops) είναι δομικά αδύνατοι

Ο κανόνας: **ένας κόμβος επιτρέπεται να επιλέξει γονέα ΜΟΝΟ αν το
διαφημιζόμενο rank του υποψηφίου είναι αυστηρά μικρότερο από το δικό του
τρέχον rank** (`mesh_node.h:226`, `if (b->rank >= meshMyRank) return;`).

Απόδειξη ότι αυτό αποκλείει βρόχους: αν υπήρχε κύκλος A→B→C→A, θα
σήμαινε ότι rank(B) < rank(A), rank(C) < rank(B), rank(A) < rank(C) —
δηλαδή rank(A) < rank(A), άτοπο. Άρα κανένας κύκλος δεν μπορεί ποτέ να
σχηματιστεί, ανεξάρτητα από timing/race conditions. Αυτό είναι
εμπνευσμένο από το RPL (RFC 6550) strict rank-ordering, αναφέρεται ρητά
στο design spec ως το πρότυπο δανεισμού.

Το **TTL** (`MESH_MAX_TTL = 4`) παραμένει ως δεύτερη γραμμή άμυνας
("defense in depth"), όχι ο κύριος μηχανισμός — προστατεύει μόνο από
θεωρητικά transient race conditions σε αλλαγή γονέα (route flap), όχι από
πραγματικούς δομικούς βρόχους.

## 5. Trickle-style adaptive beacon backoff

Αλγόριθμος στο `meshBeaconTick()` (`mesh_node.h:139-146`):

```
κάθε φορά που περάσει meshBeaconIntervalMs χρόνος:
    στείλε beacon με rank = meshMyRank
    next = meshBeaconIntervalMs × 2
    αν next > MESH_BEACON_INTERVAL_MAX_MS (60000):
        next = MESH_BEACON_INTERVAL_MAX_MS
    meshBeaconIntervalMs = next
```

Το interval **μηδενίζεται** (`meshTrickleReset()`) σε κάθε ένα από τα εξής
γεγονότα:
- Ο κόμβος μόλις εκκίνησε.
- Χάθηκε ο τρέχων γονέας (`meshDropParent()`).
- Άλλαξε το rank του γονέα (ο ίδιος ο γονέας μετακινήθηκε).
- Ακούστηκε νέος γείτονας για πρώτη φορά (`meshNeighborLastHeard[idx] == 0`).
- Υιοθετήθηκε νέος γονέας (`meshAdoptParent()`).

Αποτέλεσμα: ένα **σταθερό** δίκτυο συγκλίνει σε 1 beacon ανά κόμβο ανά
60 δευτερόλεπτα· ένα δίκτυο **σε αναταραχή** (μόλις άναψε, μόλις έχασε
γείτονα) εκπέμπει κάθε 2 δευτερόλεπτα μέχρι να ξανασταθεροποιηθεί. Το
κόστος αερομεταφοράς (και άρα μπαταρίας, όταν προστεθεί deep sleep) είναι
ανάλογο της **αστάθειας**, όχι του χρόνου ρολογιού.

Η γέφυρα (rank 0) **εξαιρείται** από αυτή τη λογική — beacon κάθε
σταθερά 2000ms πάντα (`MESH_BRIDGE_BEACON_INTERVAL_MS`,
`bridge_esp32.ino:202-205`), γιατί τροφοδοτείται από το Pi, όχι μπαταρία·
δεν υπάρχει λόγος να κάνει backoff.

## 6. Ανίχνευση απώλειας γονέα (parent timeout)

Δύο ανεξάρτητοι μηχανισμοί, ο δεύτερος γρηγορότερος από τον πρώτο:

**α) Beacon timeout** (`meshCheckParentTimeout()`, `mesh_node.h:233-238`):
```
αν (now − τελευταίο_beacon_γονέα) > 3 × interval_τελευταίου_beacon_γονέα:
    drop τον γονέα
```
Ο πολλαπλασιαστής `MESH_PARENT_TIMEOUT_FACTOR = 3` σημαίνει ότι αν ο
γονέας έχει ήδη κάνει backoff σε 60s interval, χρειάζονται έως 180s
σιωπής πριν θεωρηθεί νεκρός.

**β) TX-failure backstop** (`meshNotifyTxStatus()`, `mesh_node.h:246-252`):
```
σε κάθε αποτυχία unicast αποστολής στον γονέα:
    μέτρησε
    αν φτάσουν 3 συνεχόμενες αποτυχίες:
        drop τον γονέα ΑΜΕΣΩΣ (χωρίς να περιμένει το beacon timeout)
```
Ο μετρητής μηδενίζεται σε κάθε επιτυχημένο ή απλά ακουσμένο beacon από
τον γονέα. Αυτό υπάρχει ακριβώς επειδή το beacon-timeout backstop θα
μπορούσε να πάρει έως 3 λεπτά σε σταθεροποιημένο δίκτυο — πολύ αργό αν ο
γονέας έχει ήδη πεθάνει στην πράξη.

**Orphan beacon:** μόλις χαθεί γονέας, ο κόμβος **δεν περιμένει** το
επόμενο προγραμματισμένο trickle beacon — στέλνει αμέσως ένα beacon με
`rank = UNROUTED` (`meshDropParent()`, `mesh_node.h:154-163`). Σκοπός:
να μικρύνει το παράθυρο όπου τα (πρώην) παιδιά του τον θεωρούν ακόμα
έγκυρο γονέα, από "μέχρι ένα πλήρες trickle interval" σε "όσο χρειάζεται
ένα πακέτο ραδιοκύματος να διαδοθεί".

## 7. De-dup cache και relay forwarding

`meshDedupSeen()` (`mesh_node.h:256-265`): δαχτυλίδι 32 εγγραφών
`(origin_mac, seq)`. Κάθε φορά που ένα πακέτο περνά (είτε ως relay είτε
στη γέφυρα), ελέγχεται αν το ζευγάρι έχει ξαναφανεί — αν ναι, πετιέται
σιωπηλά. Αυτό προστατεύει από αντίγραφα λόγω route-flap (π.χ. ο κόμβος
Β άλλαξε γονέα ανάμεσα στην αποστολή ενός πακέτου και ενός retry).

`meshRelayData()` (`mesh_node.h:316-329`): όταν ένας κόμβος λάβει
`MeshDataPacket` απευθυνόμενο στον εαυτό του (κάποιο παιδί τον επέλεξε
γονέα), το προωθεί **αμετάβλητο εκτός** από `ttl--`, στον **δικό του**
τρέχοντα γονέα. Το `origin_mac` ΔΕΝ αλλάζει ποτέ κατά μήκος της αλυσίδας
relay — παραμένει πάντα ο κόμβος που πραγματικά μέτρησε.

## 8. Local buffering όσο ο κόμβος είναι απομονωμένος

`meshSendReading()` (`mesh_node.h:292-310`): αν δεν υπάρχει γονέας
(`meshParentIdx < 0`), η μέτρηση **δεν χάνεται** — μπαίνει σε δαχτυλίδι 10
θέσεων στη RAM (`meshBufferPush()`, παλιότερη διαγράφεται όταν γεμίσει).
Μόλις βρεθεί γονέας ξανά, το επόμενο κάλεσμα αδειάζει πρώτα τον buffer
(`meshFlushBuffer()`) πριν στείλει τη νέα μέτρηση. Ο buffer **δεν
επιβιώνει reboot** — είναι αμιγώς RAM, καμία persistence σε flash/SD.

## 9. Πλήρης ροή — bridge-side ζωντάνια (offline detection)

Η γέφυρα κρατά `lastSeenMs[]`/`nodeOnline[]` ανά `origin_mac` (**όχι** ανά
άμεσο αποστολέα — σημαντικό όταν υπάρχει relay). Ελέγχεται κάθε 1000ms
(`checkOfflineNodes()`, `bridge_esp32.ino:132-149`):

```
για κάθε trusted κόμβο (εκτός της ίδιας της γέφυρας):
    αν (now − lastSeenMs) > MESH_OFFLINE_AFTER(3) × MESH_EXPECTED_REPORT_INTERVAL_MS(5000):
        αν ήταν online πριν:
            δημοσίευσε "offline" στο MQTT
```

Αυτό είναι **data-arrival-based**, όχι beacon-based — δηλαδή ένας κόμβος
που στέλνει δεδομένα μέσω relay (πολλαπλά hops) ανανεώνει κανονικά τη
ζωντάνια του, ακριβώς επειδή η γέφυρα κοιτάζει το `origin_mac`, όχι τον
τελευταίο ενδιάμεσο σταθμό. Γνωστός, αποδεκτός περιορισμός (σχόλιο στο
`bridge_esp32.ino:96-99`): μια σειρά αποτυχημένων αναγνώσεων DHT (NaN)
μπορεί να δείξει ψευδώς "offline" έναν κόμβο που στην πραγματικότητα
ζει και συνεχίζει να κάνει beacon κανονικά.
