# 05 — MQTT Broker (Mosquitto)

## 1. Γιατί MQTT και όχι κάτι άλλο

Το MQTT (Message Queuing Telemetry Transport) είναι ένα **publish/subscribe**
πρωτόκολλο πάνω από TCP, σχεδιασμένο ειδικά για IoT: μικρό binary framing
overhead (2 bytes ελάχιστο header), persistent connections (όχι request/
response HTTP polling), broker-based αποσύνδεση παραγωγού/καταναλωτή.

Οι εναλλακτικές που *θα μπορούσαν* να χρησιμοποιηθούν και γιατί δεν
επιλέχθηκαν εδώ:

| Εναλλακτική | Γιατί όχι εδώ |
|---|---|
| **HTTP polling** | Κάθε client (εφαρμογή) θα έπρεπε να ρωτάει επαναλαμβανόμενα "άλλαξε κάτι;" — σπατάλη μπαταρίας/bandwidth στο κινητό, και καθυστέρηση ίση με το polling interval. Το project *έχει* HTTP (`portal.py`) αλλά μόνο για το ζευγάρωμα (`/pair`) και το ιστορικό (`/api/history`) — δηλαδή request/response ερωτήματα, όχι live data feed. |
| **CoAP** | Πιο ελαφρύ ακόμα (UDP-based), αλλά δεν έχει έτοιμο, ώριμο cloud broker-as-a-service ισοδύναμο με το HiveMQ Cloud για την απομακρυσμένη αναμετάδοση — θα χρειαζόταν custom relay infrastructure. |
| **WebSockets απευθείας (χωρίς MQTT από πάνω)** | Θα σήμαινε να ξαναχτιστεί χειροκίνητα το pub/sub μοντέλο (topics, retain, QoS) που το MQTT ήδη παρέχει έτοιμο. |
| **gRPC/custom TCP** | Απαιτεί δικό του schema-compiler toolchain (protobuf) σε firmware περιβάλλον (Arduino) που δεν έχει έτοιμη, ελαφριά υποστήριξη. |

Το MQTT κέρδισε επειδή ταιριάζει φυσικά στο μοτίβο "πολλοί κόμβοι
δημοσιεύουν μετρήσεις, πολλοί clients (εφαρμογή, recorder, weather service)
τις καταναλώνουν χωρίς να ξέρουν ο ένας για τον άλλον" — και επειδή υπάρχει
**έτοιμο cloud-hosted broker** (HiveMQ Cloud) για την απομακρυσμένη σκέλος
χωρίς να χρειαστεί να στηθεί δικό μας public-facing infrastructure.

## 2. Mosquitto — listeners

Ρύθμιση: `pi/mosquitto/mosquitto.conf`, φορτώνεται μέσω
`/etc/mosquitto/conf.d/greenhouse.conf` (αντιγράφεται από το `install.sh`).
`per_listener_settings true` επιτρέπει διαφορετική πολιτική αυθεντικοποίησης
ανά listener:

| Θύρα | Πρωτόκολλο | Δέσμευση (bind) | Auth | Ποιος τη χρησιμοποιεί |
|---|---|---|---|---|
| **1883** | MQTT plaintext (TCP) | `127.0.0.1` **μόνο** (loopback) | `allow_anonymous true` | Εσωτερικές διεργασίες Pi: `recorder.py`, `weather.py`, `cam_bridge.py`, `simulator.py` — όλες τρέχουν *στο ίδιο μηχάνημα*, άρα το plaintext loopback traffic δεν φεύγει ποτέ από τον πυρήνα του OS |
| **8883** | MQTT over TLS (TCP) | όλα τα interfaces | `allow_anonymous false`, `password_file` | Η γέφυρα ESP32 (`bridge_esp32.ino`) και η εφαρμογή Flutter — η μόνη θύρα προσβάσιμη απ' έξω στο LAN |
| ~~9001~~ | MQTT over WebSocket+TLS | — | — | **Αφαιρέθηκε** — δες §3 |

Το γεγονός ότι το 1883 δένεται αποκλειστικά στο `127.0.0.1` σημαίνει ότι
είναι **αδύνατο** να φτάσει κανείς σε αυτό από άλλη συσκευή στο δίκτυο,
ανεξάρτητα από firewall rules — το ίδιο το TCP stack του πυρήνα αρνείται τη
σύνδεση σε εξωτερικό interface. Αυτό είναι ο λόγος που το `weather.py`
μπορεί να δημοσιεύει ανώνυμα χωρίς TLS χωρίς κίνδυνο (`pi/scripts/weather.py:42-43`
σχόλιο) — η "ασφάλεια" εδώ είναι δικτυακή απομόνωση, όχι κρυπτογράφηση.

## 3. Γιατί υπήρχε το 9001, και γιατί αφαιρέθηκε

Το `docs/ARCHITECTURE.md` (§Σημειώσεις ακρίβειας) το καταγράφει ρητά: η
εφαρμογή δοκιμάστηκε αρχικά με MQTT-over-WebSocket στη θύρα 9001, αλλά
βρέθηκε πραγματικό bug στη συνδυασμό της βιβλιοθήκης `mqtt_client` 10.x
(Dart/Flutter) με Mosquitto 2.x. Η εφαρμογή συνδέεται πλέον αποκλειστικά
με **απευθείας TCP MQTT over TLS στη θύρα 8883**
(`mqtt_client.useWebSocket = false`, `app/lib/connection/mqtt_connection.dart:68`).

Ο listener 9001 έμεινε ενεργός στο `mosquitto.conf` για καιρό χωρίς κανέναν
client στο σύστημα να τον χρησιμοποιεί — εντοπίστηκε ως περιττή επιφάνεια
έκθεσης (`IMPROVEMENTS.md §Α6`, "κάθε ανοιχτή θύρα είναι επιφάνεια επίθεσης
χωρίς αντισταθμιστικό όφελος") και **αφαιρέθηκε** μαζί από δύο σημεία:
`pi/mosquitto/mosquitto.conf` (το ίδιο το listener block) και το ορφανό,
ποτέ-εγκατεστημένο `pi/avahi/greenhouse-mqtt.service` (διαφήμιζε mDNS για
αυτή τη θύρα, αλλά δεν το αντέγραφε ποτέ το `install.sh` στα ενεργά avahi
services — δεύτερο νεκρό σημείο για το ίδιο πράγμα).

## 4. Δέντρο Topics — πλήρης χάρτης

Οργανωμένο ανά κατηγορία, με ποιος δημοσιεύει και ποιος γράφεται συνδρομή
(subscribe):

### Αισθητήρες ζώνης
```
greenhouse/<zone>/air/temperature      bridge → recorder, app
greenhouse/<zone>/air/humidity         bridge → recorder, app
greenhouse/<zone>/soil/moisture        bridge → recorder, app
greenhouse/<zone>/light/lux            (αναμενόμενο topic· κανένα firmware
                                         δεν το δημοσιεύει σήμερα εκτός
                                         από τον simulator — δες σημείωση)
```
> Σημείωση: το `light/lux` υπάρχει στο `SUBSCRIBE_TOPICS` του recorder
> (`pi/scripts/recorder.py:35`) και στο simulator, αλλά **κανένα πραγματικό
> firmware αισθητήρα δεν το δημοσιεύει** σήμερα (μόνο θερμοκρασία/υγρασία/
> έδαφος στο `SensorPacket`) — προετοιμασία για μελλοντικό αισθητήρα φωτός.

### Καιρός (weather.py, από Open-Meteo)
```
greenhouse/weather/temperature         weather.py, retained
greenhouse/weather/humidity            weather.py, retained
greenhouse/weather/wind_kmh            weather.py, retained
greenhouse/weather/uv_index            weather.py, retained
greenhouse/weather/rain_mm_1h          weather.py, retained
greenhouse/weather/pressure            (μόνο simulator· weather.py δεν
                                         δημοσιεύει ακόμα πραγματική πίεση)
greenhouse/weather/forecast            weather.py, retained, JSON 24ωρη πρόγνωση
greenhouse/weather/alert               weather.py + cam_bridge.py, JSON alerts
greenhouse/weather/location/set        app → weather.py, retained
```

### Κανόνες αυτοματισμού
```
greenhouse/rules/update                app → weather.py, retained
greenhouse/rules/get                   app → weather.py (ζήτηση broadcast)
greenhouse/rules/current               weather.py → app, retained
greenhouse/actuators/<id>/set          weather.py/app → (actuator controller — δες §5)
greenhouse/actuators/<id>/state        (μόνο simulator σήμερα)
```

### Κόμβοι (ζωντάνια/μπαταρία)
```
greenhouse/nodes/<MAC-hex>/status      bridge → app, retained ("online"/"offline")
greenhouse/nodes/<id>/battery          (μόνο simulator σήμερα — κανένα
                                         πραγματικό firmware δεν αναφέρει μπαταρία)
```

### Ιστορικό (over-MQTT path, για εκτός-LAN πρόσβαση)
```
greenhouse/history/request             app → recorder.py
greenhouse/history/response/<req-id>   recorder.py → app
```

### Κάμερα
```
greenhouse/cam/status                  cam_bridge.py, retained, JSON
greenhouse/cam/event/request           app → cam_bridge.py
greenhouse/cam/event/response/<id>     cam_bridge.py → app, chunked JSON
greenhouse/cam/live/start              app → cam_bridge.py
greenhouse/cam/live/stop               app → cam_bridge.py
greenhouse/cam/live/frame              cam_bridge.py → app, chunked JSON
```

### Ρυθμίσεις εφαρμογής
```
greenhouse/settings/notifications              app → weather.py, retained
greenhouse/settings/notifications/current      weather.py → app, retained
greenhouse/app/fcm_token/<device-uuid>          app → push.py, retained (ένα ανά συσκευή)
```

## 5. Σημαντική σημείωση — actuators δεν έχουν πραγματικό controller

Ο κώδικας ορίζει το topic `greenhouse/actuators/<id>/set` και η εφαρμογή
(`app/lib/connection/mqtt_connection.dart:134-142`) το δημοσιεύει κανονικά
όταν ο χρήστης πατήσει διακόπτη· το `weather.py` επίσης δημοσιεύει σε αυτό
όταν πυροδοτηθεί κανόνας αυτοματισμού (`_fire()`, `weather.py:246-253`).
**Δεν υπάρχει όμως κανένα firmware relay/actuator-controller σε αυτό το
repo** που να το ακούει και να αναφέρει πίσω `greenhouse/actuators/<id>/state`
— μόνο ο `pi/tools/simulator.py` δημοσιεύει ψεύτικες τιμές κατάστασης
(`simulator.py:34-35`). Δηλαδή ο έλεγχος ενεργοποιητών (pump/fan/light) είναι
**σχεδιασμένος** στο πρωτόκολλο αλλά **δεν υλοποιημένος** σε πραγματικό
hardware ακόμα — σωστό να αναφέρεται ρητά, όχι να υποτεθεί ότι λειτουργεί.

## 6. QoS και Retain — πολιτική ανά κατηγορία

| Κατηγορία | QoS | Retain | Γιατί |
|---|---|---|---|
| Μετρήσεις αισθητήρων (bridge→broker) | 0 | **true** | Fire-and-forget αρκεί (νέα τιμή κάθε 5s)· retain εξασφαλίζει ότι η εφαρμογή βλέπει αμέσως την τελευταία τιμή σε reconnect, χωρίς να περιμένει |
| Καιρός/forecast/rules/settings (Pi→app config-like δεδομένα) | 0 (mosquitto_pub default) | **true** | Ίδια λογική — "τρέχουσα κατάσταση" πρέπει να φτάνει αμέσως σε νέο subscriber |
| Εντολές (app→Pi, `rules/update`, `location/set`, `actuators/set`) | 0/1* | **true** για `rules/update`/`location/set` | Retain εδώ λύνει ένα πραγματικό πρόβλημα timing: το `weather.py` κάνει **polling** (`mosquitto_sub -C 1 -W 2`, σύντομο one-shot subscribe) αντί να κρατά μόνιμη σύνδεση — χωρίς retain, ένα μήνυμα που φτάνει ανάμεσα σε δύο polling κύκλους θα χανόταν οριστικά |
| Chunked binary (κάμερα) | 0 | **false** | Εφήμερα frames/events, δεν έχει νόημα να "παγώσει" ο broker το τελευταίο κομμάτι μιας εικόνας |
| App subscribe (`greenhouse/#`) | **1** (`MqttQos.atLeastOnce`) | — | `mqtt_connection.dart:88` — η εφαρμογή ζητά τουλάχιστον-μία-φορά παράδοση στη δική της συνδρομή, ανεξάρτητα από το QoS που διάλεξε ο εκδότης (το ουσιαστικό QoS μιας παράδοσης είναι το min(QoS εκδότη, QoS συνδρομητή)) |

\* Ο `weather.py` δημοσιεύει μέσω CLI `mosquitto_pub` χωρίς ρητό `-q`, άρα
QoS 0· η εφαρμογή δημοσιεύει μέσω `MqttClientPayloadBuilder` με ρητό
`MqttQos.atLeastOnce` στα commands (`greenhouse_repository.dart` calls
μέσω `connection.publishRaw`/`sendCommand`).

## 7. `weather.py` — polling αντί για μόνιμη σύνδεση

Αξίζει να σημειωθεί ως αρχιτεκτονική ιδιαιτερότητα: το `weather.py` δεν
κρατά μόνιμο `paho.mqtt.Client` connection με `loop_start()` όπως το
`recorder.py`/`cam_bridge.py` — δημοσιεύει μέσω του CLI εργαλείου
`mosquitto_pub` (subprocess, `mqtt_publish()`, `weather.py:58-67`) και
"ακούει" για ενημερώσεις με σύντομα, one-shot `mosquitto_sub -C 1 -W 2`
(περίμενε το πολύ 1 μήνυμα, timeout 2 δευτερόλεπτα), καλούμενο σε κάθε
κύκλο του κύριου loop. Αυτό είναι σκόπιμα απλούστερο αντί για event-driven
MQTT client — δουλεύει επειδή τα configuration topics (rules, location,
notification settings) είναι **retained**, οπότε ένα σύντομο poll πάντα
πιάνει την τελευταία τιμή ανεξάρτητα από ακριβές timing.
