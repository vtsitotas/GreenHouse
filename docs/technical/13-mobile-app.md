# 13 — Εφαρμογή Κινητού (Flutter)

Πηγή: `app/lib/**`. Android-tested (thesis device: Redmi Note 13 Pro+),
iOS **untested** (`HANDOFF.md` backlog).

## 1. Στρωματοποίηση (layering)

```
UI (screens/)
   ↓ Riverpod providers (providers/)
GreenhouseRepository (repository/)   ← in-memory state + streams
   ↓
GreenhouseConnection interface (connection/greenhouse_connection.dart)
   ↓
MqttConnection (connection/mqtt_connection.dart)   ← μοναδική υλοποίηση σήμερα
```

Το `GreenhouseConnection` είναι μια **abstract interface** — το
`MqttConnection` είναι η μόνη υλοποίηση σήμερα, αλλά η στρωματοποίηση
επιτρέπει θεωρητικά εναλλακτική μεταφορά (π.χ. HTTP-only fallback) χωρίς
να αγγιχτεί το repository/UI layer.

## 2. State management — Riverpod

Κάθε "κομμάτι" κατάστασης είναι ένας ξεχωριστός `Provider`
(`connection_provider.dart`):
```dart
final mqttConnectionProvider = Provider((_) => MqttConnection());
final repositoryProvider = Provider((ref) =>
    GreenhouseRepository(connection: ref.watch(mqttConnectionProvider)));
final connectOnStartProvider = FutureProvider<void>((ref) async { ... });
final weatherAlertsProvider = StreamProvider<WeatherAlert>((ref) { ... });
```
Πρότυπο: κάθε τύπος δεδομένων (alerts, rules, forecast, notification
settings) έχει το δικό του `StreamProvider` που **πρώτα** εξαρτάται από
`connectOnStartProvider` (εγγυάται ότι η MQTT σύνδεση έχει ξεκινήσει πριν
προσπαθήσει να ακούσει events) και μετά επιστρέφει το αντίστοιχο stream
από το repository. Τα widgets ακούνε (`ref.watch`) μόνο το συγκεκριμένο
provider που χρειάζονται — re-render μόνο όταν αλλάξει αυτό το κομμάτι,
όχι ολόκληρη η κατάσταση εφαρμογής.

## 3. `GreenhouseRepository` — κεντρικό in-memory state

Δεν είναι απλό passthrough του connection layer — κρατά **το τελευταίο
γνωστό στιγμιότυπο** κάθε τύπου δεδομένων (`_readings`, `_nodes`,
`_actuators`, `_rules`, `_lastForecast`, `_notificationSettings`,
`greenhouse_repository.dart:19-41`) και εκθέτει streams που **πρώτα
εκπέμπουν την τρέχουσα cached τιμή, μετά συνεχίζουν με live ενημερώσεις**:
```dart
Stream<Map<String, Map<String, double>>> get readings async* {
  yield Map.from(_readings);        // άμεση τρέχουσα κατάσταση
  yield* _readingsCtrl.stream;      // μετά, ζωντανές ενημερώσεις
}
```
Αυτό λύνει ένα πραγματικό UX πρόβλημα: ένα widget που μόλις
"τοποθετήθηκε" (mounted) στο δέντρο δεν βλέπει μόνιμα κενή οθόνη μέχρι το
επόμενο MQTT μήνυμα — παίρνει αμέσως ό,τι ήδη ξέρει η εφαρμογή.

## 4. `MqttConnection` — σύνδεση, δύο hosts, αυτόματη εναλλαγή

`_attempt()` (`mqtt_connection.dart:35-50`) δοκιμάζει **σειριακά** δύο
ζεύξεις:
```dart
final hosts = [
  (config.lanHost, config.username, config.password),        // πρώτα LAN
  (config.remoteHost, config.remoteUsername, config.remotePassword), // μετά HiveMQ
];
```
Αν το LAN host αποτύχει (π.χ. η εφαρμογή είναι εκτός σπιτιού), δοκιμάζει
αμέσως το remote (HiveMQ) — **χωρίς την ανάγκη ο χρήστης να επιλέξει
χειροκίνητα** "τοπικό" ή "απομακρυσμένο" mode. Η κατάσταση σύνδεσης
(`ConnectionStatus.local`/`.remote`/`.reconnecting`/`.offline`) εκτίθεται
στην εφαρμογή ώστε το UI να δείχνει ένα διακριτικό banner.

### Exponential backoff σε αποτυχία
```dart
Future<void> _scheduleRetry(config, gen) async {
  int delay = 10;
  while (_generation == gen) {
    await Future.delayed(Duration(seconds: delay));
    if (await _attempt(config, gen)) return;
    delay = (delay * 2).clamp(10, 60);
  }
}
```
(`mqtt_connection.dart:52-61`). Το `_generation` counter είναι το μοτίβο
που ακυρώνει "παλιές" retry λούπες: αν ο χρήστης αποσυνδεθεί χειροκίνητα
ή ξανασυνδεθεί ενόσω μια retry λούπα είναι ήδη σε εξέλιξη, η παλιά λούπα
βλέπει `_generation != gen` και σταματά μόνη της — αποτρέπει δύο
ταυτόχρονες retry λούπες να τρέχουν παράλληλα.

## 5. TLS στο κινητό — MQTT απευθείας TCP, όχι WebSocket

```dart
client.useWebSocket = false;
client.secure = true;
client.onBadCertificate = (Object _) => true;
client.keepAlivePeriod = 30;
client.connectTimeoutPeriod = 5000;
```
(`mqtt_connection.dart:68-73`). Απευθείας TCP MQTT πάνω από TLS στη θύρα
του `config.port` (8883) — **όχι** WebSocket στο 9001, όπως αναλύεται στο
`05-mqtt-broker.md §3` (γνωστό bug βιβλιοθήκης). Δες `10-security.md §4`
για την ανάλυση του `onBadCertificate`.

## 6. Δρομολόγηση μηνυμάτων — από topic string σε τυποποιημένο event

`_route()` (`mqtt_connection.dart:105-131`) είναι ένα μεγάλο chain από
στατικές συναρτήσεις αναγνώρισης topic (`isWeatherAlertTopic()`,
`isSensorTopic()`, `isNodeStatusTopic()`, κλπ., regex-based για τα
δυναμικά μέρη όπως `<node-id>`), κάθε μία αντιστοιχίζει το raw
`(topic, payload)` string ζεύγος σε ένα **τυποποιημένο Dart object**
(`SensorReading`, `NodeStatus`, `WeatherAlert`, κλπ.) πριν φτάσει καν στο
repository. Το repository (§3) δουλεύει αποκλειστικά με αυτά τα
τυποποιημένα events, ποτέ με raw strings.

## 7. Request/response πάνω από pub/sub — το μοτίβο "αίτημα με ID"

Το MQTT δεν έχει εγγενή έννοια request/response (είναι καθαρά
fire-and-forget publish/subscribe). Δύο σημεία στο repository το
προσομοιώνουν με το ίδιο μοτίβο:

```dart
Future<Map<String, dynamic>?> fetchHistoryViaMqtt({...}) async {
  final id = 'h${DateTime.now().microsecondsSinceEpoch}';   // μοναδικό αίτημα ID
  ...
  final completer = Completer<Map<String, dynamic>?>();
  sub = _historyRespCtrl.stream.listen((event) {
    if (event.id != id) return;      // αγνόησε απαντήσεις άλλων αιτημάτων
    completer.complete(...);
  });
  await connection.publishRaw('greenhouse/history/request', payload);
  return completer.future.timeout(Duration(seconds: 8), onTimeout: () => null);
}
```
(`greenhouse_repository.dart:195-235`, ίδιο μοτίβο και στο
`fetchEventPhoto()`, γραμμές 264-302). Το αίτημα φέρει μοναδικό `id`
(timestamp-based), δημοσιεύεται σε ένα "request" topic, η απάντηση
έρχεται σε ένα topic με το ίδιο `id` ενσωματωμένο
(`greenhouse/history/response/<id>`) — το broker δεν ξέρει τίποτα για
"αιτήματα", απλά προωθεί μηνύματα κανονικά· η αντιστοίχιση request↔response
γίνεται εξ ολοκλήρου στο application layer και των δύο άκρων. `Completer`
+ `.timeout()` μετατρέπει αυτό το ασύγχρονο pub/sub σε ένα κανονικό
`Future` που μπορεί να γίνει `await`-αρισμένο σαν κανονικό HTTP request.

## 8. LAN vs remote επιλογή μεταφοράς για ιστορικό

Η εφαρμογή διαλέγει HTTP (`/api/history`, γρηγορότερο, απλούστερο) όταν
`connectionStatus == local`, και MQTT request/response (§7) όταν
`connectionStatus == remote` — γιατί το HiveMQ bridge γεφυρώνει μόνο MQTT,
ποτέ HTTP (`08-cloud-bridge.md §6`). Αυτή η επιλογή γίνεται στο επίπεδο
`history_service.dart`/`history_provider.dart`, εκτός εμβέλειας αυτού του
εγγράφου σε λεπτομέρεια — η αρχή είναι η ίδια με το §7.

## 9. Ασφαλής αποθήκευση credentials

`PairingService` (`services/pairing_service.dart`) χρησιμοποιεί
`flutter_secure_storage` — Android Keystore-backed αποθήκευση, όχι απλό
`SharedPreferences` plaintext — για να κρατήσει το `ConnectionConfig`
(συμπεριλαμβανομένων MQTT usernames/passwords) μόνιμα στη συσκευή μετά
το πρώτο `/pair`.

## 10. FCM Push — γιατί χρειάστηκε πέρα από το in-app MQTT listener

Πριν, οι ειδοποιήσεις έφταναν **μόνο** μέσω του ζωντανού MQTT listener
(`weatherAlertsProvider`) — δηλαδή τίποτα δεν έφτανε αν η εφαρμογή ήταν
κλειστή/background (το MQTT socket κλείνει όταν το process τερματιστεί
από το OS). Λύση: `FcmTokenService` καταχωρεί/ανανεώνει το FCM token της
συσκευής μέσω ενός **retained MQTT topic ανά συσκευή**
(`greenhouse/app/fcm_token/<device-uuid>`, §`11-weather-automation.md`
δεν το καλύπτει, δες `pi/shared/push.py` — `05-mqtt-broker.md §4`
Ρυθμίσεις εφαρμογής). Το Pi διαβάζει όλα τα τρέχοντα καταχωρημένα tokens
(`mosquitto_sub` σε wildcard) και στέλνει μέσω Firebase Cloud Messaging —
αυτό το κανάλι δουλεύει ανεξάρτητα από το αν η εφαρμογή/MQTT σύνδεση είναι
ενεργή, γιατί το FCM delivery γίνεται από τους δικούς του servers της
Google, όχι από το Pi απευθείας στη συσκευή.
