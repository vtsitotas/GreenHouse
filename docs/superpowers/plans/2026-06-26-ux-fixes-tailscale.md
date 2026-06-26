# UX Fixes + Tailscale Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix control screen stuck loading, add connection verification to pairing, simplify pairing UX, fix simulator paho-mqtt 2.x compat, and add Tailscale remote access.

**Architecture:** App-side fixes are all in `app/lib/`. Pi-side fixes are scripts run over SSH. Tailscale gives the Pi a persistent `100.x.x.x` IP that works over any network — the app already has a `tailscaleHost` field in `ConnectionConfig`, it just needs to be populated and tried.

**Tech Stack:** Flutter/Dart, Riverpod 2.x, mqtt_client 10.x, Mosquitto 2.0.21, paho-mqtt 2.1.0, Tailscale

## Global Constraints

- Flutter SDK at `C:\Users\billy\flutter\bin` — always add to PATH: `$env:PATH = "C:\Users\billy\flutter\bin;$env:PATH"`
- ADB at `C:\Users\billy\AppData\Local\Android\Sdk\platform-tools\adb.exe`
- MQTT transport: TCP TLS port 8883, `useWebSocket = false` — do NOT revert to WebSocket (known bug with mqtt_client 10.x + Mosquitto 2.x)
- Pi at `192.168.1.88`, user `pi`, password `greenhouse2026`
- Build command: `flutter build apk --debug` from `app/` directory
- Install command: `adb install -r app\build\app\outputs\flutter-apk\app-debug.apk`
- All Flutter changes require rebuild + reinstall before testing on device

---

### Task 1: Fix simulator paho-mqtt 2.x compatibility

**Problem:** Pi has paho-mqtt 2.1.0 installed. `simulator.py` uses the old 1.x API: `mqtt.Client(client_id=..., clean_session=True)`. In 2.x, the first argument must be `CallbackAPIVersion`. This causes a `TypeError` or `DeprecationWarning` that may silently prevent publishing.

**Files:**
- Modify: `pi/tools/simulator.py`

- [ ] **Step 1: Check if simulator is actually publishing**

SSH into Pi and run:
```bash
mosquitto_sub -h 127.0.0.1 -p 1883 -t "greenhouse/#" -v --count 5
```
If no output after 5 seconds, the simulator isn't publishing. Then check nohup.out:
```bash
cat ~/nohup.out
```

- [ ] **Step 2: Fix the paho-mqtt 2.x API call**

Edit `pi/tools/simulator.py` line 28. Change:
```python
c = mqtt.Client(client_id="simulator", clean_session=True)
```
To:
```python
c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="simulator", clean_session=True)
```

- [ ] **Step 3: Copy fixed simulator to Pi and restart**

From Windows PowerShell:
```powershell
scp C:\Users\billy\Desktop\diplomatikh\pi\tools\simulator.py pi@192.168.1.88:/home/pi/greenhouse/tools/simulator.py
```

On the Pi SSH session:
```bash
pkill -f simulator.py
sleep 2
nohup python3 /home/pi/greenhouse/tools/simulator.py &
sleep 3
mosquitto_sub -h 127.0.0.1 -p 1883 -t "greenhouse/#" -v --count 5
```
Expected: 5 lines of sensor/actuator data appear within 2 seconds.

- [ ] **Step 4: Commit**
```bash
git add pi/tools/simulator.py
git commit -m "fix: update simulator for paho-mqtt 2.x CallbackAPIVersion API"
```

---

### Task 2: Fix control screen loading forever

**Problem:** `actuatorsProvider` is a `StreamProvider` whose underlying stream (`_actuatorsCtrl`) never emits an initial value. The control screen shows `CircularProgressIndicator()` until the first actuator MQTT message arrives. If retained messages are missing or late, it loads forever.

**Root cause:** `GreenhouseRepository._actuatorsCtrl` is a broadcast `StreamController` — it emits nothing until `_handle()` processes an `ActuatorState` event. Same issue exists for readings and nodes but they get data sooner.

**Files:**
- Modify: `app/lib/repository/greenhouse_repository.dart`
- Modify: `app/lib/screens/control/control_screen.dart`

**Interfaces:**
- `GreenhouseRepository.actuators` → `Stream<Map<String, ActuatorState>>`
- `GreenhouseRepository.readings` → `Stream<Map<String, Map<String, double>>>`
- `GreenhouseRepository.nodes` → `Stream<Map<String, NodeStatus>>`

- [ ] **Step 1: Emit initial empty maps from all three streams**

In `app/lib/repository/greenhouse_repository.dart`, change the three stream getters to prepend an empty map:

```dart
Stream<Map<String, Map<String, double>>> get readings =>
    Stream.value(Map.from(_readings)).asyncExpand((_) => _readingsCtrl.stream);

Stream<Map<String, NodeStatus>> get nodes =>
    Stream.value(Map.from(_nodes)).asyncExpand((_) => _nodesCtrl.stream);

Stream<Map<String, ActuatorState>> get actuators =>
    Stream.value(Map.from(_actuators)).asyncExpand((_) => _actuatorsCtrl.stream);
```

This immediately emits the current state (empty `{}` on first connect, or last-known data on reconnect) so `StreamProvider` transitions from `loading` → `data` instantly.

- [ ] **Step 2: Update control screen empty state message**

In `app/lib/screens/control/control_screen.dart`, the `data` case already handles empty maps:
```dart
data: (map) => map.isEmpty
    ? const Center(child: Text('No actuators discovered yet'))
    : ...
```
This is correct. No change needed here.

- [ ] **Step 3: Build and test**

```powershell
$env:PATH = "C:\Users\billy\flutter\bin;$env:PATH"
cd C:\Users\billy\Desktop\diplomatikh\app
flutter build apk --debug 2>&1 | Select-Object -Last 3
& "C:\Users\billy\AppData\Local\Android\Sdk\platform-tools\adb.exe" install -r "build\app\outputs\flutter-apk\app-debug.apk"
```

Open app → Control tab. Expected: shows actuator toggles (pump1, fan1, light1) within 2 seconds, not a spinner. If still empty, verify simulator is publishing (Task 1).

- [ ] **Step 4: Commit**
```bash
git add app/lib/repository/greenhouse_repository.dart
git commit -m "fix: emit initial empty maps from repository streams so UI doesn't load forever"
```

---

### Task 3: Add connection verification to pairing screen

**Problem:** The "Connect" button in `PairingScreen._save()` calls `saveConfig()` and navigates to dashboard regardless of whether MQTT credentials are correct. Any password is accepted.

**Fix:** Before saving and navigating, attempt an MQTT connection. If it fails, show an error. If it succeeds, save and navigate.

**Files:**
- Modify: `app/lib/screens/pairing/pairing_screen.dart`
- Modify: `app/lib/connection/mqtt_connection.dart` — expose a one-shot test method

**Interfaces:**
- New: `MqttConnection.testConnect(ConnectionConfig) → Future<bool>` — tries to connect once, disconnects immediately, returns success/failure

- [ ] **Step 1: Add `testConnect` to `MqttConnection`**

In `app/lib/connection/mqtt_connection.dart`, add after the `disconnect()` method:

```dart
Future<bool> testConnect(ConnectionConfig config) async {
  for (final host in [config.lanHost, config.tailscaleHost]) {
    if (await _tryConnect(host, config)) {
      await disconnect();
      return true;
    }
  }
  return false;
}
```

- [ ] **Step 2: Add `mqttConnectionProvider` export**

In `app/lib/providers/connection_provider.dart`, expose the mqtt connection so pairing screen can call testConnect:

```dart
final mqttConnectionProvider = _mqttConnectionProvider;
```

Change the line:
```dart
final _mqttConnectionProvider = Provider((_) => MqttConnection());
```
To:
```dart
final mqttConnectionProvider = Provider((_) => MqttConnection());
```

And update the line that references it:
```dart
final repositoryProvider = Provider((ref) =>
    GreenhouseRepository(connection: ref.watch(mqttConnectionProvider)));
```

- [ ] **Step 3: Update `_save()` in PairingScreen to verify connection first**

In `app/lib/screens/pairing/pairing_screen.dart`, replace `_save()`:

```dart
Future<void> _save() async {
  if (!_formKey.currentState!.validate()) return;
  setState(() { _busy = true; _error = null; });
  final config = ConnectionConfig(
    lanHost: _lan.text.trim(),
    tailscaleHost: _ts.text.trim(),
    port: int.parse(_port.text.trim()),
    tlsFingerprint: _fp.text.trim(),
    username: _user.text.trim(),
    password: _pass.text,
  );
  try {
    final ok = await ref.read(mqttConnectionProvider).testConnect(config);
    if (!ok) {
      setState(() {
        _error = 'Could not connect. Check the address and password.';
        _busy = false;
      });
      return;
    }
    await ref.read(pairingServiceProvider).saveConfig(config);
    ref.invalidate(connectOnStartProvider);
    if (mounted) context.go('/dashboard');
  } catch (e) {
    setState(() { _error = e.toString(); _busy = false; });
  }
}
```

Also add the import at the top if not present:
```dart
import 'package:greenhouse_app/providers/connection_provider.dart';
```

- [ ] **Step 4: Build and test**

```powershell
$env:PATH = "C:\Users\billy\flutter\bin;$env:PATH"
cd C:\Users\billy\Desktop\diplomatikh\app
flutter build apk --debug 2>&1 | Select-Object -Last 3
& "C:\Users\billy\AppData\Local\Android\Sdk\platform-tools\adb.exe" install -r "build\app\outputs\flutter-apk\app-debug.apk"
```

Test 1 — wrong password: Go to Settings → Re-pair, enter wrong password, tap Connect. Expected: error message "Could not connect. Check the address and password." — does NOT navigate to dashboard.

Test 2 — correct credentials (`192.168.1.88`, port `8883`, user `app`, password `greenhouse2026`): tap Connect. Expected: brief delay (~2s), then navigates to dashboard.

- [ ] **Step 5: Commit**
```bash
git add app/lib/connection/mqtt_connection.dart app/lib/providers/connection_provider.dart app/lib/screens/pairing/pairing_screen.dart
git commit -m "fix: verify MQTT connection before accepting pairing credentials"
```

---

### Task 4: Simplify pairing UX

**Problem:** The pairing form shows 6 technical fields (LAN host, Tailscale IP, Port, TLS fingerprint, Username, Password). A non-technical user doesn't know what "mDNS", "TLS fingerprint", or "Tailscale IP" mean.

**Fix:** Show only "Pi address" and "Password" by default. Move the rest to a collapsible "Advanced" section with sensible defaults pre-filled.

**Files:**
- Modify: `app/lib/screens/pairing/pairing_screen.dart`

- [ ] **Step 1: Rewrite the form layout**

Replace the entire `_PairingScreenState` class body in `app/lib/screens/pairing/pairing_screen.dart`:

```dart
class _PairingScreenState extends ConsumerStatefulWidget {
  const PairingScreen({super.key});
  @override ConsumerState<PairingScreen> createState() => _PairingScreenState();
}

class _PairingScreenState extends ConsumerState<PairingScreen> {
  final _formKey = GlobalKey<FormState>();
  final _host   = TextEditingController(text: '192.168.1.88');
  final _pass   = TextEditingController();
  final _tsHost = TextEditingController();
  final _port   = TextEditingController(text: '8883');
  final _fp     = TextEditingController();
  final _user   = TextEditingController(text: 'app');
  bool _busy = false;
  bool _showAdvanced = false;
  String? _error;

  @override
  void dispose() {
    for (final c in [_host, _pass, _tsHost, _port, _fp, _user]) c.dispose();
    super.dispose();
  }

  void _applyQr(String raw) {
    try {
      final j = jsonDecode(raw) as Map<String, dynamic>;
      _host.text   = j['host_lan']        ?? '';
      _tsHost.text = j['host_tailscale']  ?? '';
      _port.text   = (j['port'] ?? 8883).toString();
      _fp.text     = j['tls_fingerprint'] ?? '';
      _user.text   = j['username']        ?? 'app';
      _pass.text   = j['password']        ?? '';
    } catch (_) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Invalid QR code')));
    }
  }

  Future<void> _save() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() { _busy = true; _error = null; });
    final config = ConnectionConfig(
      lanHost: _host.text.trim(),
      tailscaleHost: _tsHost.text.trim(),
      port: int.parse(_port.text.trim()),
      tlsFingerprint: _fp.text.trim(),
      username: _user.text.trim(),
      password: _pass.text,
    );
    try {
      final ok = await ref.read(mqttConnectionProvider).testConnect(config);
      if (!ok) {
        setState(() {
          _error = 'Could not connect. Check the address and password.';
          _busy = false;
        });
        return;
      }
      await ref.read(pairingServiceProvider).saveConfig(config);
      ref.invalidate(connectOnStartProvider);
      if (mounted) context.go('/dashboard');
    } catch (e) {
      setState(() { _error = e.toString(); _busy = false; });
    }
  }

  Widget _field(TextEditingController c, String label,
      {bool obscure = false, TextInputType? type, String? Function(String?)? validator, String? hint}) =>
      Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: TextFormField(
          controller: c,
          decoration: InputDecoration(labelText: label, hintText: hint),
          obscureText: obscure,
          keyboardType: type,
          validator: validator ?? (v) => v!.isEmpty ? 'Required' : null,
        ),
      );

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Connect to your greenhouse')),
        body: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: Form(
            key: _formKey,
            child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              FilledButton.icon(
                onPressed: () async {
                  final result = await context.push<String>('/pair/qr');
                  if (result != null) _applyQr(result);
                },
                icon: const Icon(Icons.qr_code_scanner),
                label: const Text('Scan QR from Pi'),
              ),
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 16),
                child: Row(children: [
                  Expanded(child: Divider()),
                  Padding(padding: EdgeInsets.symmetric(horizontal: 8), child: Text('or enter manually')),
                  Expanded(child: Divider()),
                ]),
              ),
              _field(_host, 'Pi address', hint: '192.168.1.x or pi.local'),
              _field(_pass, 'Password', obscure: true),
              // Advanced section
              InkWell(
                onTap: () => setState(() => _showAdvanced = !_showAdvanced),
                child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 8),
                  child: Row(children: [
                    Icon(_showAdvanced ? Icons.expand_less : Icons.expand_more, size: 18),
                    const SizedBox(width: 4),
                    Text('Advanced', style: Theme.of(context).textTheme.bodySmall),
                  ]),
                ),
              ),
              if (_showAdvanced) ...[
                _field(_tsHost, 'Tailscale IP (for remote access)', validator: (_) => null, hint: '100.x.x.x'),
                _field(_port, 'Port', type: TextInputType.number,
                    validator: (v) => int.tryParse(v ?? '') == null ? 'Must be a number' : null),
                _field(_user, 'Username'),
                _field(_fp, 'TLS fingerprint', validator: (_) => null),
              ],
              const SizedBox(height: 8),
              if (_error != null) ...[
                Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
                const SizedBox(height: 12),
              ],
              FilledButton(
                onPressed: _busy ? null : _save,
                child: _busy ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white)) : const Text('Connect'),
              ),
            ]),
          ),
        ),
      );
}
```

- [ ] **Step 2: Build and test**

```powershell
$env:PATH = "C:\Users\billy\flutter\bin;$env:PATH"
cd C:\Users\billy\Desktop\diplomatikh\app
flutter build apk --debug 2>&1 | Select-Object -Last 3
& "C:\Users\billy\AppData\Local\Android\Sdk\platform-tools\adb.exe" install -r "build\app\outputs\flutter-apk\app-debug.apk"
```

Expected: Pairing screen shows only "Pi address" + "Password". Tapping "Advanced" reveals the extra fields. QR scan still populates all fields.

- [ ] **Step 3: Commit**
```bash
git add app/lib/screens/pairing/pairing_screen.dart
git commit -m "feat: simplify pairing UX — show only address+password, hide advanced fields"
```

---

### Task 5: Tailscale setup on Pi + app

**Goal:** Install Tailscale on the Pi so the app can reach it from any network (4G, other WiFi, etc.) using a persistent `100.x.x.x` IP.

**Pi side (run over SSH):**

- [ ] **Step 1: Install Tailscale on Pi**

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

After `tailscale up`, the terminal shows a URL like:
```
To authenticate, visit: https://login.tailscale.com/a/xxxxx
```

Open that URL on any device and log in with a Google/GitHub/email account. The Pi will show as connected in your Tailscale dashboard.

- [ ] **Step 2: Get the Pi's Tailscale IP**

```bash
tailscale ip -4
```

Expected output: something like `100.64.x.x`. Write this down.

- [ ] **Step 3: Install Tailscale on the phone**

Download Tailscale from the Play Store and sign in with the **same account** you used in Step 1. The Pi should appear in your Tailscale device list.

- [ ] **Step 4: Verify Pi is reachable via Tailscale**

On your phone (with WiFi turned OFF, using 4G), open Tailscale app and confirm the Pi shows as "Connected". Then test SSH from a terminal to `100.x.x.x` to confirm connectivity.

- [ ] **Step 5: Update pairing in the app**

Open the app → Settings → Re-pair. In the "Advanced" section, enter the Tailscale IP (`100.x.x.x`) in the "Tailscale IP" field. Tap Connect.

The `MqttConnection.connect()` already tries LAN first, then Tailscale:
```dart
for (final host in [config.lanHost, config.tailscaleHost]) {
```

So on home WiFi → connects via LAN. On 4G → LAN fails → tries Tailscale IP → connects.

- [ ] **Step 6: Make Tailscale start automatically on Pi boot**

```bash
sudo systemctl enable tailscaled
```

(Already enabled by the installer, but confirm.)

- [ ] **Step 7: Update show_qr.py to include Tailscale IP**

The QR generation script already accepts `--tailscale` arg. After Tailscale is set up, generate a new QR:

```bash
python3 /home/pi/greenhouse/tools/show_qr.py \
  --tailscale $(tailscale ip -4) \
  --pass greenhouse2026
```

This QR now contains both LAN host and Tailscale IP — scanning it fills all fields automatically.

- [ ] **Step 8: Update show_qr.py to use port 8883 by default**

The current `show_qr.py` defaults to `--port 9001` (WebSocket). Change to 8883:

In `pi/tools/show_qr.py`, change:
```python
ap.add_argument("--port", type=int, default=9001)
```
To:
```python
ap.add_argument("--port", type=int, default=8883)
```

Also update `host_lan` to use the actual Pi hostname:
```python
"host_lan": "192.168.1.88",   # or pass as --lan arg
```

Actually, add a `--lan` argument:
```python
ap.add_argument("--lan", default="192.168.1.88")
```
And use it:
```python
"host_lan": args.lan,
```

Copy updated script to Pi:
```powershell
scp C:\Users\billy\Desktop\diplomatikh\pi\tools\show_qr.py pi@192.168.1.88:/home/pi/greenhouse/tools/show_qr.py
```

- [ ] **Step 9: Commit**
```bash
git add pi/tools/show_qr.py
git commit -m "fix: show_qr.py defaults to port 8883 (TCP TLS), add --lan arg"
```

---

## Summary of all changes

| File | Change |
|------|--------|
| `pi/tools/simulator.py` | paho-mqtt 2.x API fix |
| `pi/tools/show_qr.py` | default port 8883, add --lan arg |
| `app/lib/repository/greenhouse_repository.dart` | emit initial empty maps from streams |
| `app/lib/connection/mqtt_connection.dart` | add testConnect() method |
| `app/lib/providers/connection_provider.dart` | expose mqttConnectionProvider publicly |
| `app/lib/screens/pairing/pairing_screen.dart` | simplified UX + connection verification |
