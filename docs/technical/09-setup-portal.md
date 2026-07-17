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

### Το πρόβλημα

Το Pi δεν έχει σταθερή IP — κάθε φορά που συνδέεται σε (διαφορετικό ή και
στο ίδιο) WiFi, το DHCP του router μπορεί να του δώσει διαφορετική
διεύθυνση. Δεν υπάρχει κεντρικός DNS server στο σπίτι που να ξέρει
"greenhouse.local = 192.168.1.54" — άρα χρειάζεται τρόπος να ρωτηθεί
**το ίδιο το τοπικό δίκτυο** "ποιος είναι το greenhouse.local;" χωρίς
προηγούμενη γνώση IP.

### mDNS (Multicast DNS, RFC 6762) — βήμα-βήμα

1. Κάθε ερώτηση mDNS στέλνεται σε **multicast** UDP διεύθυνση
   `224.0.0.251`, θύρα `5353` — multicast σημαίνει ότι το πακέτο φτάνει
   ταυτόχρονα σε **όλες** τις συσκευές του τοπικού δικτύου, όχι σε μία
   συγκεκριμένη IP (αντίθετα με το κλασικό unicast DNS ερώτημα προς έναν
   συγκεκριμένο server).
2. Στο Pi τρέχει μόνιμα ο `avahi-daemon` (πακέτο `avahi-daemon`,
   εγκαθίσταται από το `install.sh`), που ακούει σε αυτή τη multicast
   διεύθυνση σε **κάθε** ενεργό interface (WiFi client *και* AP hotspot —
   δεν εξαρτάται από AP/STA κατάσταση).
3. Όταν έρθει ερώτηση "ποιος έχει το hostname `greenhouse.local`;", μόνο
   το Pi αναγνωρίζει το όνομα ως δικό του και απαντά με τη **δική του
   τρέχουσα** IP — καμία άλλη συσκευή στο δίκτυο δεν απαντά, καμία
   κεντρική βάση δεν χρειάστηκε να ρωτηθεί.

Αυτό είναι ουσιαστικά "DNS χωρίς server" — αποκεντρωμένο, λειτουργεί μόνο
εντός ενός L2 broadcast domain (γι' αυτό δουλεύει μόνο εντός του ίδιου
τοπικού δικτύου/subnet, ποτέ μέσω Internet).

### DNS-SD (Service Discovery, RFC 6763) — το δεύτερο επίπεδο

Το mDNS από μόνο του απαντά μόνο "ποιος έχει **αυτό το όνομα**;". Το
DNS-SD πάνω από αυτό απαντά σε ένα διαφορετικό ερώτημα: "ποια συσκευή
προσφέρει **αυτή την υπηρεσία**;" — χωρίς να χρειάζεται να ξέρεις καν το
hostname εκ των προτέρων. Το Pi διαφημίζει τον εαυτό του ως τέτοια
υπηρεσία μέσω `pi/avahi/greenhouse-http.service`:
```xml
<service>
  <type>_greenhouse._tcp</type>
  <port>80</port>
</service>
```
Η ανακάλυψη γίνεται σε **τρία διαδοχικά** ερωτήματα (η κλασική DNS-SD
αλυσίδα PTR→SRV→A):

| Βήμα | Ερώτημα | Απάντηση |
|---|---|---|
| 1. PTR | "ποιες instances υπάρχουν της υπηρεσίας `_greenhouse._tcp.local`;" | π.χ. `Greenhouse on raspberrypi._greenhouse._tcp.local` |
| 2. SRV | "σε ποιο hostname/θύρα βρίσκεται αυτή η συγκεκριμένη instance;" | hostname + θύρα (80) |
| 3. A | "ποια είναι η IPv4 αυτού του hostname;" | η πραγματική τρέχουσα IP |

### Η υλοποίηση στην εφαρμογή — δύο επίπεδα fallback

`_discover()` (`app/lib/screens/pairing/pairing_screen.dart:77-117`),
καλείται από το κουμπί **"Find my greenhouse"**:

```dart
// 1η προσπάθεια: απευθείας ανάλυση ονόματος μέσω του OS resolver
final res = await http.get(Uri.parse('http://greenhouse.local/pair'))
    .timeout(const Duration(seconds: 5));
// δουλεύει αξιόπιστα σε iOS/macOS (εγγενής υποστήριξη Bonjour/mDNS
// στο ίδιο το λειτουργικό)· "sometimes" σε Android — σχόλιο στον κώδικα,
// γιατί το native DNS resolver του Android δεν υποστηρίζει πάντα
// συνεπώς το .local resolution

// 2η προσπάθεια (fallback): ρητό DNS-SD PTR→SRV→A μέσω του πακέτου
// multicast_dns — αξιόπιστο σε Android
final client = MDnsClient();
await client.start();
await for (ptr in client.lookup<PtrResourceRecord>(
    ResourceRecordQuery.serverPointer('_greenhouse._tcp.local'))) {
  await for (srv in client.lookup<SrvResourceRecord>(
      ResourceRecordQuery.service(ptr.domainName))) {
    await for (a in client.lookup<IPAddressResourceRecord>(
        ResourceRecordQuery.addressIPv4(srv.target))) {
      ip = a.address.address;   // βρέθηκε η πραγματική IP
    }
  }
}
// μετά: GET http://<ip>/pair απευθείας, παρακάμπτοντας hostname resolution
```

Η πρώτη προσπάθεια είναι απλούστερη και γρηγορότερη όταν δουλεύει (το OS
κάνει όλη τη δουλειά DNS-SD "από κάτω" διαφανώς)· η δεύτερη είναι πιο
αργή (τρία διαδοχικά multicast round-trips) αλλά πιο αξιόπιστη
cross-platform, γιατί μιλάει απευθείας το πρωτόκολλο αντί να βασίζεται σε
OS-level resolver support που διαφέρει ανά κατασκευαστή Android.

### Γιατί δουλεύει το ίδιο και στο setup hotspot

Ο `avahi-daemon` δεν ελέγχει καθόλου αν το Pi είναι σε AP mode ή STA
mode — απαντά σε mDNS ερωτήματα σε **κάθε** ενεργό δικτυακό interface.
Άρα ένα κινητό συνδεδεμένο απευθείας στο hotspot `Greenhouse-XXXX`
(subnet `192.168.4.0/24`) μπορεί να βρει το `greenhouse.local` με
ακριβώς την ίδια διαδικασία — καμία διαφορά κώδικα, απλά διαφορετικό
δίκτυο.

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
