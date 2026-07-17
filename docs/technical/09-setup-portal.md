# 09 — Captive Portal & Πρώτη Εγκατάσταση

Πηγή: `pi/portal/portal.py`, `pi/scripts/first_boot.sh`,
`pi/scripts/gen_certs.sh`, `pi/scripts/ap_up.sh`, `pi/mosquitto/*`.

## 1. Δύο λειτουργίες, ένα Flask process

Το `portal.py` τρέχει **πάντα** στη θύρα 80, αλλά συμπεριφέρεται πολύ
διαφορετικά ανάλογα με την ύπαρξη ενός sentinel αρχείου:
```python
_WIFI_SENTINEL = "/etc/greenhouse/.wifi_configured"
def _ap_mode() -> bool:
    return not os.path.exists(_WIFI_SENTINEL)
```

| Κατάσταση | Sentinel | Συμπεριφορά |
|---|---|---|
| **AP mode** (καινούργια μονάδα) | Απών | Δείχνει φόρμα ρύθμισης WiFi· `POST /connect` και `POST /api/connect` ενεργά· `/pair` κλειστό |
| **STA mode** (κανονική λειτουργία) | Παρών | `/pair` ενεργό (ζεύξη εφαρμογής)· η φόρμα WiFi δείχνει "rebooting" |

## 2. Access Point — NetworkManager, όχι hostapd

`pi/scripts/ap_up.sh`. Ρητή τεκμηρίωση στο σχόλιο κορυφής (γραμμές 5-9):
στο Raspberry Pi OS Trixie, το NetworkManager **κατέχει** το `wlan0` radio
interface — αν προσπαθήσεις να τρέξεις raw `hostapd` από πάνω, αποτυγχάνει
σιωπηλά γιατί το radio είναι ήδη claimed. Λύση: αφήνεται το ίδιο το
NetworkManager να τρέξει το hotspot (`ipv4.method shared`), που δίνει
**AP + DHCP + NAT σε μία διαχειριζόμενη σύνδεση**, χωρίς τα rfkill/
DAEMON_CONF προβλήματα ενός χειροκίνητου hostapd setup. Ρύθμιση AP:
```
802-11-wireless.mode ap
802-11-wireless.band bg      # 2.4GHz μόνο
802-11-wireless.channel 6
ipv4.addresses 192.168.4.1/24
```

Το `install.sh:74-80` γράφει επίσης ένα captive-DNS config
(`dnsmasq-shared.d/greenhouse-captive.conf`, `address=/#/192.168.4.1`) —
**κάθε** domain που ζητά η συσκευή του χρήστη ενόσω είναι στο hotspot
αναλύεται (resolve) στο ίδιο το Pi. Αυτό είναι που κάνει το λειτουργικό
σύστημα του κινητού να ανοίξει αυτόματα το captive-portal popup.

## 3. SSID μοναδικό ανά μονάδα, χωρίς ρύθμιση

```bash
DEVICE_ID=$(cat /sys/class/net/wlan0/address | tr -d ':' | tail -c 5 | tr '[:lower:]' '[:upper:]')
SSID="Greenhouse-${DEVICE_ID}"
```
(`ap_up.sh:14-15`). Το SSID παράγεται **τη στιγμή της εκκίνησης**,
απευθείας από τη φυσική MAC διεύθυνση του radio (μοναδική ανά chip, καμένη
στο silicon). Αυτό σημαίνει ότι ένα κλωνοποιημένο SD image (ίδιο firmware
byte-for-byte σε χιλιάδες μονάδες) παράγει **αυτόματα διαφορετικό** SSID σε
κάθε φυσική συσκευή — καμία τιμή δεν χρειάζεται να αλλάξει χειροκίνητα πριν
την κλωνοποίηση.

## 4. OS-level captive portal detection

`_PROBE_PATHS` (`portal.py:37-46`) — μια λίστα από συγκεκριμένα paths που
τα διάφορα λειτουργικά συστήματα καλούν αυτόματα για να ανιχνεύσουν αν
"υπάρχει πραγματικό Internet" πίσω από ένα WiFi hotspot:

| Path | OS |
|---|---|
| `hotspot-detect.html` | Apple iOS/macOS |
| `library/test/success.html` | παλιότερο Apple |
| `generate_204` | Android / Chrome OS |
| `connecttest.txt`, `ncsi.txt` | Windows NCSI |

Η λογική `index()` (`portal.py:119-130`): αν το request path ταιριάζει σε
ένα από αυτά **και** είμαστε σε AP mode, επιστρέφει `302 redirect` στο `/`.
Αυτό το ίδιο το 302 (**όχι** ένα κανονικό 200 στο probe path) είναι αυτό
που πυροδοτεί αξιόπιστα το popup captive-portal browser σε iOS/Android/
Windows — ένα απλό 200 σε αυτά τα paths συχνά δεν ανοίγει τίποτα.

## 5. Provisioning στο πρώτο boot — τι παράγεται μοναδικό

`pi/scripts/first_boot.sh`, τρέχει μία φορά (sentinel
`/etc/greenhouse/.provisioned`):

1. **TLS certs** (μέσω `gen_certs.sh`, δες §6 παρακάτω) — μοναδικό CA +
   server cert ανά μονάδα.
2. **MQTT password**: `openssl rand -base64 21 | tr -d '/+=\n' | head -c 20`
   — 20 χαρακτήρες URL-safe τυχαίο password, ξεχωριστό ανά μονάδα.
3. **Device ID**: τελευταία 4-5 hex χαρακτήρες της MAC (ίδια λογική με το
   AP SSID).
4. Γράφει `/etc/greenhouse/device.json` (username/password/port/tls
   fingerprint) — **αναγνώσιμο από `pi` user, όχι world-readable**
   (`chmod 640`, `chown root:pi`).

## 6. TLS certificate generation — αυτο-υπογεγραμμένο, μοναδικό ανά μονάδα

`pi/scripts/gen_certs.sh` (idempotent — `[ -f server.crt ] && exit 0`):
```bash
openssl genrsa -out ca.key 2048
openssl req -new -x509 -days 3650 -key ca.key -out ca.crt -subj "/CN=GreenhouseCA"
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr -subj "/CN=greenhouse.local"
openssl x509 -req -days 3650 -in server.csr -CA ca.crt -CAkey ca.key \
  -CAcreateserial -out server.crt
```
Κάθε μονάδα φτιάχνει τη **δική της** αυτο-υπογεγραμμένη Certificate
Authority (RSA-2048, ισχύς 10 χρόνια) και υπογράφει το δικό της
server certificate με αυτήν — **καμία κοινή CA/private key ανάμεσα σε
μονάδες**. Αυτό είναι κρίσιμο για την ασφάλεια μαζικής παραγωγής: ακόμα κι
αν το ίδιο byte-for-byte SD image κλωνοποιηθεί σε 1000 μονάδες, το script
τρέχει σε **κάθε** πρώτο boot και κάθε μονάδα καταλήγει με διαφορετικό
ζευγάρι κλειδιών — η παραβίαση μιας μονάδας δεν εκθέτει τις υπόλοιπες.
Η δημόσια βαθμονόμηση: `openssl x509 -fingerprint -sha256` παράγει το
SHA-256 fingerprint που αποθηκεύεται στο `device.json` και επιστρέφεται
από το `/pair` endpoint (§7).

## 7. `/pair` — η ζεύξη εφαρμογής

```python
@app.route("/pair")
def pair():
    if time.time() - _START_TIME > _PAIR_WINDOW:   # 600 δευτ.
        return jsonify({"error": "..."}), 403
    ...
    return jsonify({
        "host_lan": "greenhouse.local", "host_remote": hivemq_host,
        "port": c["port"], "tls_fingerprint": c["tls_fingerprint"],
        "username": c["username"], "password": c["password"],
        "remote_username": ..., "remote_password": ...,
    })
```
(`portal.py:198-217`). Αυτό το endpoint επιστρέφει **σε καθαρό κείμενο,
χωρίς αυθεντικοποίηση**, τα MQTT credentials + TLS fingerprint + HiveMQ
credentials. Η μοναδική προστασία είναι το **χρονικό παράθυρο** — μόνο τα
πρώτα 600 δευτερόλεπτα μετά την εκκίνηση της υπηρεσίας. Ο χρήστης το
ανοίγει ξανά με `sudo systemctl restart greenhouse-portal`. Αυτό είναι
ρητά καταγεγραμμένο ως γνωστός, αποδεκτός περιορισμός για εμβέλεια
διπλωματικής/LAN-only χρήση (`HANDOFF.md` backlog: "`/pair` and
`/api/history*` are unauthenticated... would need a PIN/QR/token before
any public or multi-customer deployment") — δες πλήρη ανάλυση στο
`10-security.md`.

## 8. Avahi / mDNS — πώς βρίσκεται το `greenhouse.local`

`pi/avahi/greenhouse-http.service` εγκαθίσταται σε
`/etc/avahi/services/`. Ο `avahi-daemon` (mDNS/DNS-SD, RFC 6762/6763)
διαφημίζει το hostname `greenhouse.local` στο τοπικό δίκτυο — καμία
κεντρική DNS εγγραφή δεν χρειάζεται· η εφαρμογή/browser το επιλύει με
multicast UDP ερώτημα στο `224.0.0.251:5353`. Αυτό είναι που επιτρέπει
στο κουμπί **"Find my greenhouse"** της εφαρμογής να λειτουργεί χωρίς ο
χρήστης να ξέρει την τοπική IP της μονάδας.

## 9. Πλήρης ροή πρώτης εγκατάστασης

```
1. Χρήστης ενεργοποιεί το Pi (χωρίς .wifi_configured) → AP mode
2. Κινητό συνδέεται στο "Greenhouse-XXXX" (ανοιχτό δίκτυο)
3. OS probe → 302 redirect → captive-portal popup ανοίγει
4. Χρήστης εισάγει SSID+password σπιτικού WiFi → POST /connect ή /api/connect
5. _save_wifi(): nmcli δημιουργεί νέο connection profile "greenhouse-home",
   απενεργοποιεί αυτόματη σύνδεση σε κάθε άλλο WiFi profile
6. _reboot_soon(): reboot μετά από 3 δευτερόλεπτα (bash subprocess background)
7. Νέα εκκίνηση: .wifi_configured πλέον υπάρχει → STA mode, χωρίς AP
8. Pi συνδέεται στο σπιτικό WiFi, avahi διαφημίζει greenhouse.local
9. Χρήστης πατά "Find my greenhouse" → GET /pair (εντός 600s παραθύρου)
10. Εφαρμογή αποθηκεύει credentials (flutter_secure_storage) → συνδέεται
    MQTT TLS :8883 → live dashboard
```

## 10. Μαζική παραγωγή — `prep_image.sh` / κλωνοποίηση

Η λογική "κάθε μονάδα μοναδική" (§5-6) είναι αυτό που κάνει ασφαλή τη
διαδικασία: φτιάχνεται μία "master" μονάδα, τρέχει `install.sh`
(εγκαθιστά όλα τα services + περιμένει το πρώτο-boot provisioning να
τρέξει), μετά `prep_image.sh` "καθαρίζει" την ταυτότητα (αφαιρεί sentinels
όπως το `.wifi_configured` ώστε κάθε κλώνος να ξεκινήσει σε AP mode) πριν
διαβαστεί η SD κάρτα σε `.img` αρχείο για μαζική εγγραφή σε νέες κάρτες
(`INSTRUCTIONS.md` Part 2-3). Κάθε νέος κλώνος περνά ξανά από ολόκληρο το
`first_boot.sh` provisioning στο δικό του πρώτο boot, παράγοντας τα δικά
του μοναδικά certs/passwords/SSID.
