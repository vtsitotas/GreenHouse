# 06 — Βάση Δεδομένων (SQLite)

## 1. Σχήμα

Ορίζεται στο `pi/scripts/recorder.py:95-119` (`_SCHEMA`), εφαρμόζεται με
`conn.executescript()` στο `init_db()`:

```sql
CREATE TABLE IF NOT EXISTS series (
  id     INTEGER PRIMARY KEY,
  kind   TEXT NOT NULL,      -- 'zone' ή 'weather'
  zone   TEXT,               -- π.χ. 'zone1', NULL για weather
  metric TEXT NOT NULL,      -- π.χ. 'air_temperature'
  UNIQUE(kind, zone, metric)
);

CREATE TABLE IF NOT EXISTS readings (          -- ανά λεπτό
  series_id INTEGER NOT NULL REFERENCES series(id),
  ts        INTEGER NOT NULL,                  -- unix epoch, στρογγυλεμένο σε λεπτό
  avg REAL NOT NULL, min REAL NOT NULL, max REAL NOT NULL, n INTEGER NOT NULL,
  PRIMARY KEY (series_id, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS readings_hourly (   -- ίδιες στήλες, ανά ώρα
  series_id INTEGER NOT NULL REFERENCES series(id),
  ts INTEGER NOT NULL,
  avg REAL NOT NULL, min REAL NOT NULL, max REAL NOT NULL, n INTEGER NOT NULL,
  PRIMARY KEY (series_id, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
```

### Σχεδιαστικές λεπτομέρειες σχήματος

- **`series` ως normalized lookup table:** αντί να επαναλαμβάνεται το
  string `"zone1"`/`"air_temperature"` σε κάθε γραμμή μέτρησης (εκατοντάδες
  χιλιάδες γραμμές/μήνα), κάθε μοναδικός συνδυασμός (kind, zone, metric)
  παίρνει έναν ακέραιο `id` μία φορά. Το `readings` αναφέρεται σε αυτόν με
  foreign key. Αποτέλεσμα: κάθε γραμμή `readings` είναι 1 ακέραιος +
  1 timestamp + 3 floats + 1 ακέραιος — σταθερού μεγέθους, μικρή.
- **Composite primary key `(series_id, ts)` + `WITHOUT ROWID`:** το SQLite
  φτιάχνει κανονικά ένα κρυφό `rowid` B-tree *επιπλέον* του primary key
  index. Το `WITHOUT ROWID` λέει στο SQLite να αποθηκεύσει τα δεδομένα
  **απευθείας μέσα** στο B-tree του primary key (clustered index, όπως η
  InnoDB του MySQL) — μία δομή αντί για δύο, μικρότερο αρχείο, γρηγορότερα
  ερωτήματα εύρους πάνω σε `(series_id, ts BETWEEN … AND …)` αφού τα
  δεδομένα είναι ήδη φυσικά ταξινομημένα έτσι στο δίσκο.
- **`avg/min/max/n` αντί για raw τιμή:** κάθε γραμμή είναι ήδη μια
  προ-συγκεντρωμένη κουβά (bucket) ενός λεπτού, όχι ξεχωριστή γραμμή ανά
  πακέτο MQTT — βλ. `07-recorder-service.md` για το γιατί.
- **`meta` table:** key-value store μίας γραμμής σήμερα
  (`rollup_watermark` — το timestamp μέχρι το οποίο έχει ήδη τρέξει η
  ωριαία συμπύκνωση), επεκτάσιμο χωρίς migration αν χρειαστεί άλλο ένα
  config value στο μέλλον.

## 2. PRAGMA ρυθμίσεις — τι κάνει καθεμία

```python
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA synchronous=NORMAL')
```
(`recorder.py:125-126`)

- **`journal_mode=WAL`** (Write-Ahead Logging): αντί το SQLite να γράφει
  αλλαγές απευθείας στο κύριο αρχείο `.db` και να κρατά ένα rollback-journal
  για ανάκτηση σε κράση, οι αλλαγές γράφονται πρώτα σε ξεχωριστό αρχείο
  `.db-wal` και "συγχωνεύονται" περιοδικά (checkpoint) στο κύριο αρχείο.
  **Γιατί εδώ έχει σημασία:** επιτρέπει **ταυτόχρονη ανάγνωση κατά τη
  διάρκεια εγγραφής** — το `portal.py` ανοίγει τη βάση σε **read-only mode**
  (`sqlite3.connect(f"file:{DB}?mode=ro", uri=True)`, `portal.py:70`) για
  να απαντήσει σε `/api/history` requests, ενώ ταυτόχρονα το `recorder.py`
  μπορεί να γράφει νέες κουβάδες κάθε 60 δευτερόλεπτα — χωρίς WAL, ο κάθε
  writer θα κλείδωνε ολόκληρη τη βάση και ο portal θα έβλεπε `SQLITE_BUSY`.
- **`synchronous=NORMAL`**: λέει στο SQLite να μην κάνει `fsync()` σε κάθε
  εγγραφή στο WAL αρχείο (μόνο στα checkpoints) — πιο γρήγορο από το
  ασφαλέστερο `FULL`, με το trade-off ότι μια απότομη διακοπή ρεύματος
  (**όχι** crash της ίδιας της διεργασίας) θα μπορούσε θεωρητικά να χάσει
  τις τελευταίες λίγες συναλλαγές. Αποδεκτό εδώ γιατί το χειρότερο σενάριο
  είναι απώλεια ~1 λεπτού ιστορικών δεδομένων, όχι διαφθορά δομής βάσης.
- **`wal_checkpoint(TRUNCATE)`** καλείται ρητά μετά από κάθε rollup+prune
  κύκλο (`recorder.py:206`) — συγχωνεύει το WAL αρχείο πίσω στο κύριο
  αρχείο και το μηδενίζει, ώστε το `.db-wal` να μην μεγαλώνει επ' άπειρον.

## 3. Γιατί SQLite και όχι MariaDB / PostgreSQL / InfluxDB

Αυτή είναι η ερώτηση-κλειδί για τη διπλωματική. Ανάλυση κατά κριτήριο:

### α) Πόροι υλικού

Το Pi είναι **Raspberry Pi Zero W**: single-core ARMv6 @ 1GHz, **512MB RAM
συνολικά** (μοιρασμένα με το OS + Mosquitto + Flask + όλες τις άλλες
υπηρεσίες). Ένας πλήρης RDBMS server όπως η MariaDB τρέχει ως ξεχωριστή
διεργασία daemon που καταναλώνει μόνιμα δεκάδες MB RAM ό,τι κι αν κάνει
(connection pool, query cache, InnoDB buffer pool) — ακόμα κι αν
ρυθμιστεί στο ελάχιστο. Το SQLite είναι μια **βιβλιοθήκη** (`libsqlite3`),
όχι διεργασία· "τρέχει" μέσα στη διεργασία Python που την καλεί
(`recorder.py`, `portal.py`), μηδενικό επιπλέον resident process, μηδενικό
δικό της network-stack overhead.

### β) Τοπολογία πρόσβασης

Μόνο **δύο** διεργασίες αγγίζουν ποτέ αυτή τη βάση, και οι δύο τρέχουν
**στο ίδιο μηχάνημα**: ο `recorder.py` (μοναδικός writer) και ο
`portal.py`/`recorder.py`'s ίδιο process (readers, μέσω `mode=ro`). Δεν
υπάρχει ποτέ ανάγκη για δικτυακή πρόσβαση στη βάση από άλλο μηχάνημα — ένας
client/server RDBMS (MariaDB/PostgreSQL) λύνει ένα πρόβλημα (πολλαπλοί
απομακρυσμένοι clients, network protocol, auth over δίκτυο) που **δεν
υπάρχει εδώ καθόλου**. Το SQLite είναι σχεδιασμένο ακριβώς για αυτό το
προφίλ: ένα αρχείο, τοπική πρόσβαση, embedded.

### γ) Λειτουργική πολυπλοκότητα σε ένα field-deployed IoT unit

Κάθε μονάδα Pi είναι μια **αυτόνομη** συσκευή που κλωνοποιείται μαζικά
(SD card image, βλ. `09-setup-portal.md`) και τοποθετείται σε θερμοκήπιο
χωρίς μόνιμη τεχνική επίβλεψη. Ένας RDBMS server σημαίνει: ξεχωριστό
systemd service που μπορεί να αποτύχει να ξεκινήσει, δικά του credentials/
users να διαχειριστούν, δικό του port να ασφαλιστεί, δικές του αναβαθμίσεις
σχήματος (migrations) να τρέξουν σε κάθε νέα μονάδα. Το SQLite είναι
**ένα αρχείο** (`greenhouse.db`) — κανένα service να ξεκινήσει/αποτύχει,
κανένα credential, backup = αντιγραφή ενός αρχείου.

### δ) Ο πραγματικός φόρτος εγγραφής δεν το δικαιολογεί

Ο recorder γράφει **το πολύ 1 batch transaction ανά 60 δευτερόλεπτα**
(`_flush_tick()`, `recorder.py:280-293`) — όχι ανά μέτρηση (οι κόμβοι
στέλνουν κάθε 5"). Αυτό είναι δεκάδες γραμμές ανά λεπτό, όχι χιλιάδες.
Αυτή η κλίμακα φόρτου είναι ακριβώς εκεί που το SQLite είναι πρακτικά
εξίσου γρήγορο με έναν πλήρη server-based RDBMS, αφού το bottleneck δεν
είναι ποτέ query throughput.

### ε) Γιατί όχι InfluxDB/TimescaleDB (specialized time-series DB)

Το `HANDOFF.md` καταγράφει ρητά ότι το project **ξεκίνησε** με σχέδιο
InfluxDB + Node-RED + Grafana (βλ. `HANDOFF.md:179`, "No InfluxDB/Node-RED/
Grafana — replaced by the lighter local SQLite recorder") και εγκαταλείφθηκε.
Λόγοι: η InfluxDB (ακόμα και η OSS single-node έκδοση) είναι σημαντικά πιο
βαριά σε RAM/CPU baseline απ' ό,τι αντέχει άνετα ένα Pi Zero W, και προσθέτει
ένα ολόκληρο δεύτερο query language (Flux/InfluxQL) + δικό της HTTP API
server για ένα πρόβλημα (μερικές δεκάδες time-series, χαμηλός ρυθμός
εγγραφής, μονο-μηχανή ανάγνωση) που το SQL+SQLite λύνει εξίσου καλά με
πολύ λιγότερο λειτουργικό βάρος.

### Σύνοψη σε πίνακα

| Κριτήριο | SQLite (επιλέχθηκε) | MariaDB/PostgreSQL | InfluxDB |
|---|---|---|---|
| Ξεχωριστή διεργασία server | Όχι — embedded | Ναι | Ναι |
| RAM baseline | ~ μηδενικό (μοιράζεται τη διεργασία caller) | Δεκάδες MB+ μόνιμα | Δεκάδες-εκατοντάδες MB |
| Δικτυακή πρόσβαση πολλών μηχανών | Δεν χρειάζεται (δεν υπάρχει) | Παρέχεται, αχρησιμοποίητη εδώ | Παρέχεται, αχρησιμοποίητη εδώ |
| Λειτουργική πολυπλοκότητα σε field unit | Ένα αρχείο | Service+users+auth να ρυθμιστούν ανά μονάδα | Service+auth+retention policies |
| Ταιριάζει στον πραγματικό φόρτο (1 batch write/60s) | Ναι, άνετα | Overkill | Overkill |
| Concurrent read/write | Ναι, μέσω WAL | Ναι (εγγενές, βαρύτερο μηχανισμό) | Ναι |

## 4. Rollup / retention — λεπτομέρεια αλγορίθμου

Αναλύεται πλήρως στο `07-recorder-service.md §3`. Σχετικό εδώ: το
`readings` κρατά 90 μέρες σε ανάλυση λεπτού, το `readings_hourly` κρατά
730 μέρες (2 χρόνια) σε ανάλυση ώρας — δύο ξεχωριστοί πίνακες αντί για ένα
με "resolution" στήλη, ώστε τα ερωτήματα ιστορικού να διαλέγουν πίνακα
απευθείας βάσει ζητούμενου εύρους (`table = 'readings' if span_seconds <=
48*3600 else 'readings_hourly'`, `pi/shared/history_query.py:33`) χωρίς
GROUP BY σε query time.
