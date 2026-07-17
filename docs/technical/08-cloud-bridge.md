# 08 — Cloud Bridge (HiveMQ)

## 1. Το πρόβλημα: πρόσβαση εκτός LAN

Η εφαρμογή πρέπει να δείχνει live δεδομένα ακόμα κι όταν ο χρήστης δεν
είναι στο ίδιο WiFi με το θερμοκήπιο. Δύο κλασικές λύσεις εξετάστηκαν και
απορρίφθηκαν πριν καταλήξει το project στο HiveMQ Cloud (βλ.
`docs/ARCHITECTURE.md` §Σημειώσεις: "όχι Tailscale, όχι port forwarding"):

| Εναλλακτική | Γιατί όχι |
|---|---|
| **Port forwarding** στο σπιτικό router | Εκθέτει τη θύρα 8883 απευθείας στο δημόσιο Internet — απαιτεί σταθερή δημόσια IP ή Dynamic DNS, ρύθμιση σε κάθε διαφορετικό router πελάτη (ασύμβατο με μαζική παραγωγή μονάδων), και μεγαλύτερη επιφάνεια επίθεσης (ο broker γίνεται δημόσια σαρώσιμος στο 8883) |
| **Tailscale** (mesh VPN) | Θα δούλευε, αλλά προσθέτει ένα ολόκληρο δεύτερο δίκτυο-επικάλυψη (overlay network) με δικό του daemon να τρέχει μόνιμα στο Pi *και* στο κινητό, δικό του account/device-registration flow ανά μονάδα — πολυπλοκότητα που δεν χρειάζεται όταν το ίδιο το MQTT μπορεί να γεφυρωθεί απευθείας |
| **HiveMQ Cloud** (επιλέχθηκε) | Έτοιμος, δημόσια προσβάσιμος MQTT broker-as-a-service με TLS. Το Pi απλά *προωθεί* (bridge) τα ίδια topics προς τα εκεί· η εφαρμογή συνδέεται στο HiveMQ με τα ίδια credentials/πρωτόκολλο MQTT που ήδη ξέρει να μιλάει για το LAN σκέλος — καμία νέα τεχνολογία στο κινητό |

## 2. Γιατί custom paho-mqtt script αντί για το native bridge του Mosquitto

Το Mosquitto έχει built-in δυνατότητα `connection` directive στο conf file
για να γεφυρώνει (bridge) proto σε άλλον broker — θα ήταν η "προφανής"
λύση, μηδενικός επιπλέον κώδικας. **Δεν δούλεψε ποτέ εδώ**: το σχόλιο στην
κορυφή του `pi/scripts/hivemq_bridge.py:2-11` το τεκμηριώνει ρητά —
**μηδέν επιτυχή CONNACK σε 9 μέρες logs** ενάντια σε αυτό το συγκεκριμένο
HiveMQ Cloud cluster, ενώ ένα απλό `paho-mqtt` client με τα **ίδια ακριβώς**
host/credentials συνδέεται κανονικά και μένει συνδεδεμένο. Συμπέρασμα:
πραγματική ασυμβατότητα στον κώδικα TLS/CONNECT handshake του Mosquitto
bridge, όχι θέμα λογαριασμού/quota στο HiveMQ. Λύση: αντικαταστάθηκε
πλήρως με ένα μικρό Python script (`hivemq_bridge.py`) που κάνει την ίδια
δουλειά "με τα χέρια", τρέχοντας ως δικό του systemd service
(`greenhouse-hivemq-bridge.service`). Το `install.sh:98-102` αφαιρεί ρητά
οποιοδήποτε παλιό `hivemq-bridge.conf` Mosquitto directive ώστε να μην
ξαναδοκιμαστεί το σπασμένο μονοπάτι.

## 3. Αρχιτεκτονική του custom bridge

Δύο ανεξάρτητοι `paho.mqtt.Client` instances μέσα στην ίδια διεργασία:

```python
local  = mqtt.Client(client_id='greenhouse-hivemq-bridge-local')   # → 127.0.0.1:1883
remote = mqtt.Client(client_id='greenhouse-hivemq-bridge-remote')  # → HiveMQ Cloud:8883 TLS
```

Και οι δύο κάνουν subscribe στο **ίδιο wildcard** `greenhouse/#`
(`hivemq_bridge.py:20,65,69`). Κάθε πλευρά έχει το δικό της
`on_message` handler (`_make_forwarder()`) που προωθεί οτιδήποτε λάβει
στην **άλλη** πλευρά:

```
local.on_message  → forwards to remote.publish(...)
remote.on_message → forwards to local.publish(...)
```

Το remote client κάνει το TLS handshake ρητά:
```python
remote.tls_set(ca_certs='/etc/ssl/certs/ca-certificates.crt',
                tls_version=ssl.PROTOCOL_TLSv1_2)
```
δηλαδή **επικυρώνει κανονικά** το πιστοποιητικό του HiveMQ Cloud ενάντια
στο δημόσιο CA trust store του συστήματος (Let's Encrypt/δημόσια CA
αλυσίδα) — σε αντίθεση με τη γέφυρα ESP32 προς το τοπικό Mosquitto, που
κάνει `setInsecure()` (βλ. `10-security.md` για την πλήρη σύγκριση).

## 4. Το πρόβλημα echo/infinite-loop και η λύση

Αν το μήνυμα Α φτάσει τοπικά → προωθηθεί στο HiveMQ → ο remote client δει
το ίδιο μήνυμα (τώρα ερχόμενο *από* το HiveMQ) → το προωθήσει πίσω στο
local → κλειστός βρόχος επ' άπειρον.

Λύση: κοινόχρηστο `_last_seen` dictionary (`hivemq_bridge.py:27`):
```python
key = (msg.topic, msg.retain)
if _last_seen.get(key) == msg.payload:
    return  # echo από ό,τι μόλις προωθήσαμε εμείς οι ίδιοι
_last_seen[key] = msg.payload
target.publish(msg.topic, msg.payload, qos=1, retain=msg.retain)
```
Κάθε φορά που μια πλευρά προωθεί ένα μήνυμα, το καταγράφει. Αν η άλλη
πλευρά στείλει πίσω **το ίδιο ακριβώς payload** στο ίδιο topic, αναγνωρίζεται
ως ηχώ και αγνοείται — όχι re-προώθηση, όχι βρόχος. Αν έρθει διαφορετική
τιμή στο ίδιο topic (πραγματική νέα μέτρηση), προωθείται κανονικά.

## 5. Ανθεκτικότητα σύνδεσης

```python
remote.reconnect_delay_set(min_delay=1, max_delay=30)
local.reconnect_delay_set(min_delay=1, max_delay=30)
```
Exponential backoff ενσωματωμένο στο `paho-mqtt` — καμία χειροκίνητη
retry λογική δεν χρειάστηκε να γραφτεί εδώ (σε αντίθεση με το bridge
firmware ESP32 όπου έγινε χειροκίνητα σε C++, βλ. `04-bridge-gateway.md §4`,
γιατί το `PubSubClient` δεν έχει ενσωματωμένο αυτόματο backoff).
Και οι δύο clients τρέχουν σε δικό τους thread
(`local.loop_start()`, `remote.loop_start()`) — το κύριο thread απλά
κοιμάται (`time.sleep(60)`) επ' άπειρον μετά την αρχικοποίηση.

## 6. Τι ρέει μέσα από αυτό το bridge

Ολόκληρο το namespace `greenhouse/#` — δηλαδή **όλα** τα topics που
αναφέρονται στο `05-mqtt-broker.md §4` περνούν και τα δύο κατευθύνσεις.
Αυτό επιτρέπει στην εφαρμογή, όταν συνδέεται μέσω HiveMQ (απομακρυσμένα),
να έχει **ακριβώς την ίδια εμπειρία** με τη σύνδεση LAN — ίδια topics, ίδιο
μοντέλο δεδομένων — με μία εξαίρεση: το HiveMQ γεφυρώνει μόνο **MQTT**, όχι
**HTTP**. Το `/api/history` (Flask, θύρα 80) δεν είναι ποτέ προσβάσιμο
εκτός LAN μέσω αυτού του μηχανισμού — γι' αυτό υπάρχει το ξεχωριστό MQTT
request/response μονοπάτι ιστορικού (`06-database.md`/`07-recorder-service.md §6`),
ειδικά για να καλύψει αυτό το κενό.
