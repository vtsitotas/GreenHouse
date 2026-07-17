# 07 — Υπηρεσία Καταγραφής (`greenhouse-recorder`)

Πηγή: `pi/scripts/recorder.py`. Τρέχει ως systemd service, μοναδικός
writer της βάσης `greenhouse.db`.

## 1. Γιατί όχι μία εγγραφή ανά μήνυμα MQTT

Οι κόμβοι στέλνουν μέτρηση κάθε **5 δευτερόλεπτα**
(`SEND_INTERVAL_MS = 5000`, firmware). Αν κάθε μήνυμα MQTT γινόταν
απευθείας μία γραμμή/`INSERT` στη βάση, θα ήταν ~12 εγγραφές το λεπτό ανά
series — σε μια microSD κάρτα, κάθε write-transaction είναι μια πραγματική
φυσική εγγραφή flash με φθορά (limited write-cycles ανά κελί). Η λύση:
**buffering στη RAM ανά λεπτό**, μία μαζική (batched) transaction ανά λεπτό
αντί για ανά πακέτο — ~12× λιγότερες φυσικές εγγραφές στην κάρτα.

## 2. `MinuteBucketBuffer` — δομή buffering

`recorder.py:46-91`. In-memory dictionary:
```python
_buckets: dict[(series_key, minute_ts), [sum, min, max, n]]
```

- `add(series_key, timestamp, value)`: στρογγυλεύει το timestamp στο
  λεπτό (`ts - (ts % 60)`), και είτε αρχικοποιεί μια νέα κουβά είτε
  ενημερώνει `sum += value`, `min = min(min, value)`, `max = max(max, value)`,
  `n += 1`. Καλείται από το MQTT `on_message` callback.
- `flush_ready(now)`: επιστρέφει (και αφαιρεί) **μόνο** κουβάδες των
  οποίων το λεπτό έχει ήδη ολοκληρωθεί (`minute_ts + 60 <= now`) — ποτέ
  δεν στέλνει στη βάση ένα λεπτό που ακόμα δέχεται δεδομένα, αλλιώς μια
  καθυστερημένη μέτρηση θα χανόταν.
- `flush_all()`: αδειάζει τα πάντα ανεξαρτήτως ολοκλήρωσης — χρησιμοποιείται
  **μόνο** στο shutdown path, ώστε να μη χαθεί το τρέχον-σε-εξέλιξη λεπτό
  όταν η υπηρεσία τερματίζεται καθαρά (`SIGTERM`/`SIGINT`).

### Θέμα συγχρονισμού (threading)

Το `paho-mqtt` τρέχει το `on_message` callback σε **ξεχωριστό background
thread** (μέσω `client.loop_start()`), ενώ το `flush_ready()`/`flush_all()`
καλούνται από το **κύριο thread** (το `while _running: time.sleep(1)` loop).
Χωρίς κλείδωμα, ένα ταυτόχρονο `add()` + `pop()` στο ίδιο dictionary key θα
μπορούσε να χάσει σιωπηλά μια ενημέρωση (race condition σε python dict
mutation). Λύση: ένα `threading.Lock()` γύρω από κάθε πρόσβαση στο
`_buckets` (`recorder.py:58, 63, 76, 87`) — ρητά τεκμηριωμένο στο σχόλιο
του κώδικα ως fix ενός πραγματικού bug που βρέθηκε ("task-4-report.md,
Finding 2").

## 3. Εγγραφή στη βάση — batched transaction

`write_buckets()` (`recorder.py:143-162`): παίρνει τη λίστα ready buckets,
κάνει lookup/δημιουργία `series_id` για κάθε νέο (kind, zone, metric)
συνδυασμό, μετά **ένα** `BEGIN` / `executemany(INSERT OR REPLACE ...)` /
`COMMIT` για **όλες** τις γραμμές μαζί. Σε αποτυχία: ρητό `ROLLBACK`, μετά
re-raise — ο caller (`_flush_tick()`) πιάνει την εξαίρεση, καταγράφει, και
συνεχίζει (`recorder.py:280-293`) αντί να ρίξει όλη τη διεργασία. Trade-off
ρητά τεκμηριωμένο: χάνεται το πολύ ένα batch (~1 λεπτό δεδομένων), ποτέ η
υπηρεσία.

## 4. Hourly rollup — αλγόριθμος με watermark

`rollup_and_prune()` (`recorder.py:178-206`), τρέχει μία φορά την ώρα
(`_rollup_tick()`, καλείται όταν `now - last_rollup >= 3600`).

```python
watermark = meta['rollup_watermark']  # τελευταία ήδη-συμπυκνωμένη ώρα
current_hour_start = now - (now % 3600)
rollup_end = current_hour_start        # ΑΠΟΚΛΕΙΣΤΙΚΟ άνω όριο
```

**Κρίσιμη λεπτομέρεια:** η τρέχουσα, εν εξελίξει ώρα **δεν** συμπυκνώνεται
ποτέ — μόνο ώρες που έχουν ήδη ολοκληρωθεί πλήρως. Αν συμπυκνωνόταν η
τρέχουσα ώρα, μια μέτρηση που φτάνει αργότερα μέσα στην ίδια ώρα θα χανόταν
από τον υπολογισμό του μέσου όρου. Το `watermark` (αποθηκευμένο στον πίνακα
`meta`) εξασφαλίζει ότι το rollup είναι **idempotent και ασφαλές σε
επανεκκίνηση**: αν η υπηρεσία σταματήσει και ξανατρέξει, συνεχίζει ακριβώς
από εκεί που έμεινε, χωρίς να ξαναϋπολογίσει ό,τι ήδη έγινε ούτε να
προσπεράσει κάποια ώρα.

Η ίδια η συγχώνευση:
```sql
INSERT INTO readings_hourly (series_id, ts, avg, min, max, n)
SELECT series_id, ts - (ts % 3600) AS hour_ts,
       SUM(avg * n) / SUM(n),   -- σταθμισμένος μέσος όρος
       MIN(min), MAX(max), SUM(n)
FROM readings
WHERE ts >= watermark AND ts < rollup_end
GROUP BY series_id, hour_ts
ON CONFLICT(series_id, ts) DO UPDATE SET ...
```
Το `SUM(avg * n) / SUM(n)` — **όχι** απλό `AVG(avg)` — υπολογίζει σωστά
σταθμισμένο μέσο όρο όταν κάθε λεπτό-κουβά μπορεί να έχει διαφορετικό
αριθμό δειγμάτων `n` (π.χ. αν ένα λεπτό είχε λιγότερα πακέτα λόγω
προσωρινής απώλειας σύνδεσης mesh).

## 5. Retention (διαγραφή παλιών δεδομένων)

Στην ίδια transaction με το rollup:
```sql
DELETE FROM readings        WHERE ts < now − raw_days×86400      -- 90 μέρες default
DELETE FROM readings_hourly WHERE ts < now − hourly_days×86400   -- 730 μέρες default
```
Ρυθμιζόμενο μέσω `/etc/greenhouse/recorder.json` (`DEFAULT_CONFIG`,
`recorder.py:24-29`). Μετά ακολουθεί `PRAGMA wal_checkpoint(TRUNCATE)`
(γραμμή 206) — συγχωνεύει και μηδενίζει το WAL αρχείο, ώστε το physical
μέγεθος στο δίσκο να μην μεγαλώνει ασύγχρονα με τις διαγραφές.

## 6. Δύο μεταφορές, μία υλοποίηση ερωτήματος

Το ίδιο ερώτημα ιστορικού ("δώσε μου σημεία για (kind, zone, metric) σε
ένα χρονικό παράθυρο") χρειάζεται να απαντηθεί από **δύο εντελώς
διαφορετικές μεταφορές**:
- HTTP `GET /api/history` (`portal.py`, μόνο LAN)
- MQTT request/response `greenhouse/history/request` →
  `.../response/<id>` (`recorder.py::_handle_history_request()`, δουλεύει
  και εκτός LAN μέσω του HiveMQ bridge — βλ. `08-cloud-bridge.md`)

Και οι δύο καλούν την **ίδια** συνάρτηση `query_points()` από
`pi/shared/history_query.py`, ώστε η λογική επιλογής πίνακα
(minute/hour ανάλυση βάσει εύρους) να μη μπορεί ποτέ να αποκλίνει μεταξύ
των δύο μεταφορών (ιστορικό bug που διορθώθηκε ρητά — βλ. commit history/
design docs).

## 7. Signal handling — καθαρό shutdown

```python
signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT,  _handle_signal)
```
Θέτουν `_running = False`· το κύριο loop βγαίνει στην επόμενη επανάληψη
του `while _running` (μέγιστη καθυστέρηση 1 δευτερόλεπτο, το μέγεθος του
`time.sleep(1)` tick). Πριν το τελικό `conn.close()`, γίνεται
`_flush_shutdown()` — αδειάζει **όλες** τις κουβάδες (ακόμα και το
εν-εξελίξει λεπτό) ώστε ένα `systemctl restart greenhouse-recorder` να μη
χάσει ποτέ δεδομένα.
