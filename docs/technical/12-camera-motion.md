# 12 — Κάμερα (ESP32-CAM) & Ανίχνευση Κίνησης

Πηγή: `firmware/cam_esp32/cam_esp32.ino`, `pi/scripts/cam_bridge.py`,
`pi/shared/motion.py`, `pi/shared/cam_store.py`.

## 1. Hardware — AI-Thinker ESP32-CAM

Pin mapping στο `.ino` (`PWDN_GPIO_NUM`, `Y2..Y9`, `VSYNC/HREF/PCLK`, κλπ.)
ταιριάζει με το γνωστό, τυποποιημένο layout της AI-Thinker ESP32-CAM
module (κλασικός ESP32, όχι C3) — module με ενσωματωμένη OV2640 κάμερα
αισθητήρα + υποδοχή microSD. Ρύθμιση καταγραφής:
```c
config.pixel_format = PIXFORMAT_JPEG;
config.frame_size   = FRAMESIZE_VGA;   // 640×480
config.jpeg_quality = 12;
config.fb_count     = 2;               // double-buffering
```
`fb_count = 2` σημαίνει διπλό frame buffer — ενώ ένα frame διαβάζεται/
στέλνεται, το επόμενο μπορεί ήδη να καταγράφεται, μειώνοντας frame drops.

## 2. Δύο εντελώς διαφορετικές "ποιότητες" video — σκόπιμη σχεδιαστική απόφαση

Τεκμηριωμένο ρητά στο `HANDOFF.md` (§ESP32-CAM session): **LAN view** και
**remote/απομακρυσμένη view** είναι δύο ποιοτικά διαφορετικά μονοπάτια, όχι
το ίδιο πράγμα με διαφορετικό bandwidth:

### LAN — απευθείας MJPEG stream
```c
void handleStream() {
  client.printf("HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=%s\r\n\r\n", ...);
  while (client.connected()) {
    camera_fb_t *fb = esp_camera_fb_get();
    client.printf("--%s\r\nContent-Type: image/jpeg\r\n...", ...);
    client.write(fb->buf, fb->len);
    esp_camera_fb_return(fb);
    delay(50);   // ~20fps cap
  }
}
```
(`cam_esp32.ino:86-104`). Όταν η εφαρμογή είναι στο ίδιο LAN, φορτώνει
**απευθείας** αυτό το MJPEG stream (`multipart/x-mixed-replace`, ένα
συνεχές HTTP response όπου κάθε "part" είναι ένα νέο JPEG frame) —
πραγματικά ρευστά ~20fps, καμία μεσολάβηση από το Pi.

### Remote — relay μέσω Pi, on-demand, χαμηλού ρυθμού
Καμία background πολεία (polling) όταν δεν το ζητά κανείς. Η εφαρμογή
ζητά `greenhouse/cam/live/start` → το `cam_bridge.py` ξεκινά thread
(`_live_loop()`, `cam_bridge.py:165-181`) που κάνει HTTP GET
`/capture` στην κάμερα κάθε `LIVE_POLL_INTERVAL = 0.7s` (~1.4fps) και
προωθεί κάθε frame ως **chunked MQTT** μηνύματα (§4) προς την εφαρμογή.
Αυτόματο auto-stop μετά από `LIVE_SESSION_TIMEOUT = 120s` χωρίς
keep-alive (`greenhouse/cam/live/start` ξαναστέλνεται περιοδικά όσο η
οθόνη live view είναι ανοιχτή στην εφαρμογή, `startLive()` καλείται κάθε
~30s από `greenhouse_repository.dart:304-307`).

**Γιατί δύο μονοπάτια αντί για ένα:** το remote σκέλος περνά μέσω MQTT
(broker-relayed, chunked, base64-encoded — σημαντικό overhead) και μέσω
του HiveMQ Cloud bridge αν είναι εκτός σπιτιού — δεν θα μπορούσε ποτέ να
πετύχει 20fps ρευστότητα με αυτό το μονοπάτι. Το documented "Phase 2"
(WebRTC μέσω `aiortc` στο Pi) είναι η μελλοντική λύση για πραγματικά
ρευστό remote video, αλλά είναι **μόνο σχεδιασμένο, όχι υλοποιημένο**
(`HANDOFF.md`: "documented, deliberately not planned").

## 3. Ανίχνευση κίνησης — grayscale frame-diff, όχι CV library

`pi/shared/motion.py`, ρητά σχολιασμένο ως σκόπιμα απλό:
```python
def downscale_grayscale(jpeg_bytes, size=(80, 60)) -> bytes:
    img = Image.open(BytesIO(jpeg_bytes)).convert('L').resize(size)
    return img.tobytes()

def diff_score(prev, curr) -> float:
    return sum(abs(p - c) for p, c in zip(prev, curr)) / len(curr)

def is_motion(score, threshold=12.0) -> bool:
    return score >= threshold
```
Κάθε snapshot (κάθε `SNAPSHOT_INTERVAL_MS = 3000` από την κάμερα,
`cam_esp32.ino:45`) υποβιβάζεται σε 80×60 grayscale (4800 pixels), και
συγκρίνεται με το προηγούμενο snapshot με **μέσο απόλυτο διαφορικό ανά
pixel** (mean absolute difference). Αν το σκορ ξεπεράσει `MOTION_THRESHOLD
= 12.0`, θεωρείται κίνηση. Καμία πραγματική computer-vision βιβλιοθήκη
(OpenCV, κλπ.) — ρητά αιτιολογημένο στο σχόλιο του module: το Pi Zero W
δεν έχει την CPU ισχύ για κάτι βαρύτερο, και το ερώτημα που χρειάζεται να
απαντηθεί είναι απλά "άλλαξε κάτι αρκετά ώστε να αξίζει ειδοποίηση", όχι
tracking αντικειμένων/ταξινόμηση.

## 4. Chunked binary πάνω από MQTT — νέο, από-το-μηδέν πρωτόκολλο

Το MQTT δεν έχει έτοιμο πρότυπο μεταφοράς μεγάλων binary blobs σε αυτό το
project (καμία προηγούμενη προηγούμενη σύμβαση). Λύση
(`_publish_chunked()`, `cam_bridge.py:105-112`):
```python
CHUNK_SIZE = 3072   # raw bytes ανά κομμάτι πριν το base64
chunks = [data[i:i+CHUNK_SIZE] for i in range(0, len(data), CHUNK_SIZE)]
for i, chunk in enumerate(chunks):
    payload = {'chunk': i, 'total': len(chunks), 'data': base64.b64encode(chunk).decode()}
    client.publish(topic, json.dumps(payload))
```
Κάθε κομμάτι είναι ένα ξεχωριστό MQTT μήνυμα JSON με `{chunk, total, data}`.
Το `3072` bytes raw (→ ~4096 bytes μετά το base64 encoding, base64 αυξάνει
το μέγεθος κατά ~33%) επιλέχθηκε **συντηρητικά κάτω** από οποιοδήποτε
γνωστό όριο μεγέθους μηνύματος broker/HiveMQ Cloud. Η εφαρμογή
αναδιατάσσει τα κομμάτια στο `_handleLiveFrameChunk()`/`fetchEventPhoto()`
(`greenhouse_repository.dart:237-251, 261-302`) — buffer λίστα μεγέθους
`total`, γεμίζει ανά `chunk` index, ολοκληρώνεται όταν όλες οι θέσεις
έχουν τιμή, τότε `base64Decode` + concatenate.

## 5. Αποθήκευση — SD κάρτα κάμερας, όχι το Pi

Ρητή σχεδιαστική απόφαση (`HANDOFF.md`): οι φωτογραφίες γεγονότων κίνησης
αποθηκεύονται στη **δική της SD κάρτα της κάμερας**, όχι στο Pi. Το Pi
κρατά μόνο **metadata** (`cam_store.py`):
```sql
CREATE TABLE events (event_id TEXT PRIMARY KEY, ts INTEGER, diff_score REAL);
```
**Καμία εικόνα bytes** μέσα σε αυτή τη βάση — ρητά σχολιασμένο στην
κορυφή του αρχείου. Ροή αποθήκευσης: όταν το `cam_bridge.py` ανιχνεύσει
κίνηση σε ένα εισερχόμενο snapshot (`POST /cam/frame`), απαντά με
`"save:<event_id>"` (`cam_bridge.py:248-252`) — η **ίδια η κάμερα** τότε
γράφει το τρέχον frame της (που ήδη έχει στη μνήμη) στη δική της SD
(`sendSnapshotToPi()`, `cam_esp32.ino:141-172`). Trade-off ρητά συζητημένο:
το Pi έχει ήδη τα bytes στο χέρι τη στιγμή της ανίχνευσης (θα μπορούσε να
τα αποθηκεύσει το ίδιο, γλιτώνοντας ένα round-trip fetch αργότερα) — αλλά
επιλέχθηκε αποθήκευση στην κάμερα γιατί το hardware ήδη έχει υποδοχή SD.

## 6. Retention — 7 μέρες, Pi-driven

`EVENT_MAX_AGE_DAYS = 7`. Ο έλεγχος τρέχει σε background thread
(`maintenance_loop()`, κάθε 60s): `cam_store.expired_events()` βρίσκει
ληγμένα IDs, το Pi καλεί `DELETE /event/<id>` στην κάμερα (§7) για να
σβήσει το πραγματικό αρχείο, **μετά** σβήνει το δικό του metadata row —
με αυτή τη σειρά ώστε μια αποτυχημένη διαγραφή στην κάμερα (π.χ.
προσωρινά offline) να αφήσει το metadata ώστε να ξαναδοκιμαστεί στον
επόμενο κύκλο, αντί να "ξεχαστεί" ένα ορφανό αρχείο στην SD της κάμερας.
Το "Pi-driven" (αντί για RTC-based στην ίδια την κάμερα) σημαίνει ότι η
κάμερα δεν χρειάζεται καν ρολόι πραγματικού χρόνου (RTC) — απλά εκτελεί
ό,τι της πει το Pi.

## 7. Μικρό HTTP API στην ίδια την κάμερα

```
GET    /capture       → ένα JPEG frame (χρησιμοποιείται και από το live-relay poll)
GET    /stream        → συνεχές MJPEG (LAN view)
GET    /event/<id>    → σερβίρει αποθηκευμένη φωτογραφία γεγονότος
DELETE /event/<id>    → διαγράφει (retention)
```
Το `eventPath()` (`cam_esp32.ino:107-115`) **sanitize-άρει** το `<id>` σε
αυστηρά αλφαριθμητικούς χαρακτήρες πριν το χρησιμοποιήσει ως filename —
απορρίπτει οτιδήποτε άλλο, αποτρέποντας path-traversal μέσω ενός
κακόβουλα διαμορφωμένου event id (π.χ. `../../etc/passwd`).

## 8. Heartbeat / online status

`cam_bridge.py` ενημερώνει `_last_seen`/`_camera_ip` σε **κάθε**
εισερχόμενο snapshot POST (`_update_heartbeat()`, καλείται σε κάθε `POST
/cam/frame`, κάθε ~3s). Θεωρείται offline αν δεν έχει ακουστεί για
`HEARTBEAT_STALE_SECONDS = 9` (3× το αναμενόμενο interval snapshot) —
ίδια λογική 3× multiplier με το offline detection της γέφυρας mesh (δες
`03-mesh-routing.md §9`). Δημοσιεύεται retained σε `greenhouse/cam/status`.
