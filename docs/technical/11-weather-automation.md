# 11 — Καιρός & Μηχανή Αυτοματισμού (`greenhouse-weather`)

Πηγή: `pi/scripts/weather.py`.

## 1. Open-Meteo — γιατί αυτό το API

```
https://api.open-meteo.com/v1/forecast
  ?latitude={lat}&longitude={lon}
  &current=temperature_2m,relative_humidity_2m,wind_speed_10m,uv_index
  &hourly=temperature_2m,precipitation,uv_index
  &forecast_days=2&timezone=auto
```
(`weather.py:30-37`). Το Open-Meteo επιλέχθηκε γιατί **δεν απαιτεί API
key** — καμία εγγραφή/λογαριασμός/κόστος ανά μονάδα σε production ή σε
μαζική παραγωγή fleet, σε αντίθεση με OpenWeatherMap/AccuWeather που
απαιτούν κλειδί (και άρα quota/χρέωση) ανά deployment. Καθαρό HTTP GET,
JSON response, μέσω `urllib.request` της standard library — καμία
επιπλέον εξάρτηση Python.

## 2. Δημοσίευση χωρίς μόνιμη MQTT σύνδεση

Όπως αναλύεται στο `05-mqtt-broker.md §7`, το `weather.py` δεν κρατά
persistent `paho-mqtt` client — δημοσιεύει μέσω CLI subprocess
(`mosquitto_pub`) και "ακούει" ρυθμίσεις μέσω σύντομου one-shot
`mosquitto_sub -C 1 -W 2` σε κάθε κύκλο. Αυτό λειτουργεί επειδή τα
configuration topics που διαβάζει (`rules/update`, `weather/location/set`,
`settings/notifications`) είναι **retained** στον broker.

## 3. Μηχανή κανόνων — δύο τύποι κανόνα

Κάθε κανόνας (`/etc/greenhouse/rules.json`) έχει σχήμα:
```json
{"id": "...", "name": "...", "enabled": true, "notify": true,
 "trigger": {"metric": "zone1/soil_moisture", "op": "<", "value": 15,
             "duration_minutes": 2880},
 "action": {"actuator": "fan1", "command": "OFF"},
 "cooldown_minutes": 60}
```

### α) Live-metric κανόνες (χωρίς `duration_minutes`)
Απλή σύγκριση στην **τρέχουσα** τιμή, ελέγχεται σε κάθε κύκλο (κάθε
`INTERVAL` δευτερόλεπτα, default 1800s):
```python
if op_fn(current_value, threshold): fire(rule)
```

### β) Duration-based κανόνες (με `duration_minutes`)
Πυροδοτούν μόνο αν η συνθήκη ισχύει **επίμονα** για ένα παράθυρο χρόνου —
π.χ. "χώμα ξηρό (<15%) για 2 μέρες συνεχόμενα" (πραγματικό default rule
στο `install.sh:180-182`). Υλοποίηση σε `eval_duration_rule()`
(`weather.py:206-230`): ανοίγει read-only connection στη recorder βάση
(`sqlite3.connect(f'file:{RECORDER_DB}?mode=ro', uri=True)`), τραβάει όλα
τα `avg` σημεία μέσα στο παράθυρο, ελέγχει **coverage + ομοφωνία**:

```python
def duration_coverage(values, op, threshold, expected_buckets):
    coverage = len(values) / expected_buckets
    all_match = all(op_fn(v, threshold) for v in values)
    return (coverage >= 0.8 and all_match), coverage
```
Πυροδοτεί μόνο αν **τουλάχιστον 80%** των αναμενόμενων λεπτών-κουβάδων
υπάρχουν (`expected_buckets = duration_minutes`, δηλαδή ένα δείγμα ανά
λεπτό) **και** κάθε παρόν δείγμα ικανοποιεί τη συνθήκη. Το κατώφλι 80%
προστατεύει από ψευδώς αρνητικό αποτέλεσμα λόγω αραιών δεδομένων (π.χ.
μόλις μετά από restart του recorder), ενώ η απαίτηση "όλα τα παρόντα
δείγματα συμφωνούν" εγγυάται ότι μια σύντομη βροχή δεν "σπάει" έναν κανόνα
ξηρασίας — αν έστω ένα δείγμα μέσα στο παράθυρο δεν ικανοποιεί τη συνθήκη,
δεν πυροδοτεί.

### Cooldown
`_last_fired: dict[rule_id, monotonic_time]` — μετά από πυροδότηση, ο
ίδιος κανόνας δεν ξαναελέγχεται πριν περάσει `cooldown_minutes`. Αποτρέπει
"καταιγισμό" ειδοποιήσεων αν η συνθήκη παραμείνει αληθής για πολλούς
συνεχόμενους κύκλους.

## 4. Ενέργειες (actions) — δημοσίευση εντολής

Αν ο κανόνας έχει `action`, δημοσιεύεται σε `greenhouse/actuators/<id>/set`
(`_fire()`, `weather.py:246-253`) — **σημείωση:** όπως αναλύεται στο
`05-mqtt-broker.md §5`, δεν υπάρχει πραγματικό firmware actuator-controller
σε αυτό το repo που να εκτελεί την εντολή· ο κανόνας-μηχανισμός είναι
πλήρως λειτουργικός στο επίπεδο MQTT, αλλά ο φυσικός ενεργοποιητής
(pump/fan relay) δεν έχει ακόμα δικό του firmware εδώ.

## 5. Ειδοποιήσεις (alerts)

Κάθε πυροδότηση δημοσιεύει JSON σε `greenhouse/weather/alert`:
```json
{"type": "rule_id", "message": "...", "severity": "warning", "rule_id": "..."}
```
Αν `rule.notify == true` (default), καλείται επίσης `send_push()`
(`pi/shared/push.py`) — δες `13-mobile-app.md` για την πλήρη ροή FCM.

## 6. Ενσωματωμένες (built-in) ειδοποιήσεις

Δύο ειδικές, όχι-μέσω-rules.json ειδοποιήσεις:

- **Πρόβλεψη παγετού** (`maybe_send_frost_alert()`): ελέγχει τις επόμενες
  12 ώρες πρόγνωσης· αν `min(hourly_temps) < 0`, στέλνει alert μία φορά
  την ημέρα (`_last_frost_alert` date-string guard).
- **Ημερήσια περίληψη** (`maybe_send_daily_summary()`): μόνο στις 07:00
  τοπικής ώρας (`now.hour != 7: return`), μία φορά την ημέρα, συνοψίζει
  min/max θερμοκρασία + αναμενόμενη βροχή για τις επόμενες 24 ώρες.

Και οι δύο σέβονται τα `notification_settings.json` toggles
(`frost_forecast`, `daily_summary`) που η εφαρμογή μπορεί να
ενεργοποιήσει/απενεργοποιήσει ανεξάρτητα.

## 7. Reload χωρίς restart

```python
if hasattr(signal, 'SIGUSR1'):
    signal.signal(signal.SIGUSR1, _handle_reload)
```
Ένα `SIGUSR1` σήμα (ή ένα νέο `rules/update` MQTT μήνυμα) γράφει ένα
sentinel αρχείο `/tmp/greenhouse-weather-reload` που ελέγχεται σε κάθε
βρόχο — επιτρέπει ενημέρωση κανόνων **χωρίς restart της υπηρεσίας**. Το
κύριο sleep loop σπάει άμεσα αν εμφανιστεί αυτό το flag
(`for _ in range(cycle_interval): ... if os.path.exists(RELOAD_FLAG): break`),
ώστε μια αλλαγή κανόνα από την εφαρμογή να εφαρμοστεί άμεσα αντί να
περιμένει έως 30 λεπτά (το default `INTERVAL`).
