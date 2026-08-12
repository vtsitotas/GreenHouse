import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:http/io_client.dart';
import 'package:greenhouse_app/services/history_service.dart' show kPortalHttpsPort;
import 'package:greenhouse_app/utils/cert_pinning.dart';
import 'package:multicast_dns/multicast_dns.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:greenhouse_app/models/connection_config.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';
import 'package:greenhouse_app/services/multicast_lock.dart';
import 'package:greenhouse_app/services/pairing_service.dart';
import 'package:greenhouse_app/utils/friendly_error.dart';

class PairingScreen extends ConsumerStatefulWidget {
  const PairingScreen({super.key});
  @override ConsumerState<PairingScreen> createState() => _PairingScreenState();
}

class _PairingScreenState extends ConsumerState<PairingScreen> {
  final _formKey = GlobalKey<FormState>();
  final _host       = TextEditingController(text: 'greenhouse.local');
  final _pass       = TextEditingController();
  final _remoteHost = TextEditingController();
  final _remoteUser = TextEditingController();
  final _remotePass = TextEditingController();
  final _port       = TextEditingController(text: '8883');
  final _fp         = TextEditingController();
  final _user       = TextEditingController(text: 'app');
  // Machine secrets, same category as the TLS fingerprint above: normally
  // filled in automatically by QR/PIN pairing, but exposed under Advanced so
  // the fully-manual path (used whenever mDNS discovery can't find the unit)
  // can still reach the token-gated history API and camera stream.
  final _apiToken   = TextEditingController();
  final _camToken   = TextEditingController();
  bool _busy = false;
  bool _showAdvanced = false;
  String? _error;

  @override
  void dispose() {
    for (final c in [_host, _pass, _remoteHost, _remoteUser, _remotePass, _port,
                     _fp, _user, _apiToken, _camToken]) {
      c.dispose();
    }
    super.dispose();
  }

  void _applyQr(String raw) {
    try {
      final j = jsonDecode(raw) as Map<String, dynamic>;
      _host.text       = j['host_lan']        ?? '';
      _remoteHost.text = j['host_remote']     ?? j['host_tailscale'] ?? '';
      _remoteUser.text = j['remote_username'] ?? '';
      _remotePass.text = j['remote_password'] ?? '';
      _port.text       = (j['port'] ?? 8883).toString();
      _fp.text         = j['tls_fingerprint'] ?? '';
      _user.text       = j['username']        ?? 'app';
      _pass.text       = j['password']        ?? '';
      _apiToken.text   = j['api_token']       ?? '';
      _camToken.text   = j['cam_token']       ?? '';
    } catch (_) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("That QR code isn't one of ours — scan the one on the hub")));
    }
  }

  // GET /pair only confirms a greenhouse is there — no secrets in the
  // response. Real credentials require the PIN via /pair/confirm below
  // (closes the mDNS-spoofing gap: anyone can answer "found", only someone
  // reading the device's physical PIN label can get credentials).
  Future<bool> _applyPair(http.Response res, String baseUrl) async {
    if (res.statusCode == 200) {
      final j = jsonDecode(res.body) as Map<String, dynamic>;
      if (j['found'] != true) return false;
      return await _confirmWithPin(baseUrl);
    } else if (res.statusCode == 403) {
      setState(() {
        _error = 'The greenhouse only accepts new phones for 10 minutes after it '
            'starts up. Switch the hub off and on again, then try straight away.';
        _busy = false;
      });
      return true;
    }
    return false;
  }

  /// POST the PIN over TLS if the unit offers it, falling back to plaintext.
  ///
  /// This is the one request that carries real secrets in both directions (the
  /// PIN up, MQTT credentials + API/cam tokens back), so how its certificate
  /// is treated decides whether an active man-in-the-middle can steal them.
  ///
  /// Two cases, and the difference matters:
  ///
  /// * **Fingerprint already known out of band** — scanned from the QR code, or
  ///   typed into Advanced. The connection is then *pinned* exactly like every
  ///   post-pairing request: a MITM presenting its own certificate is rejected
  ///   before the PIN is ever sent. This closes first-contact MITM completely,
  ///   and is why QR is the recommended pairing path.
  /// * **No fingerprint yet** (mDNS discovery, first ever pair) — there is
  ///   nothing to verify against, so the certificate is accepted on trust for
  ///   this one exchange. That still defeats a PASSIVE eavesdropper, which is
  ///   the realistic threat on shared WiFi, but not an ACTIVE MITM. A 6-digit
  ///   PIN cannot fix this: any proof-of-knowledge it could carry is
  ///   brute-forceable offline from a single captured exchange, so the honest
  ///   answer is out-of-band fingerprint delivery, not more PIN cryptography.
  ///   The UI says so when this path is taken.
  Future<http.Response> _postPin(String baseUrl, String pin) async {
    final body = jsonEncode({'pin': pin});
    const headers = {'Content-Type': 'application/json'};
    final host = Uri.parse(baseUrl).host;
    final knownFingerprint = _fp.text.trim();

    final httpClient = knownFingerprint.isNotEmpty
        ? pinnedHttpClient(knownFingerprint, timeout: const Duration(seconds: 5))
        : (HttpClient()..connectionTimeout = const Duration(seconds: 5));
    if (knownFingerprint.isEmpty) {
      httpClient.badCertificateCallback = (_, __, ___) => true;
    }
    final secure = IOClient(httpClient);
    try {
      return await secure
          .post(Uri(scheme: 'https', host: host, port: kPortalHttpsPort,
                    path: '/pair/confirm'),
              headers: headers, body: body)
          .timeout(const Duration(seconds: 6));
    } on HandshakeException {
      // With a known fingerprint this is a PIN MISMATCH, not an old Pi:
      // something presented a certificate we don't trust. Never silently fall
      // back to plaintext here — that would hand the PIN to whatever it was.
      if (knownFingerprint.isNotEmpty) rethrow;
    } catch (_) {
      // Pi not redeployed yet (no HTTPS listener) — use the plaintext port.
      if (knownFingerprint.isNotEmpty) rethrow;
    } finally {
      secure.close();
    }
    return http
        .post(Uri.parse('$baseUrl/pair/confirm'), headers: headers, body: body)
        .timeout(const Duration(seconds: 5));
  }

  /// Make pairing without certificate verification an explicit choice.
  ///
  /// When no fingerprint is known in advance, the app cannot tell the real
  /// greenhouse from something impersonating it on the network. Silently
  /// trusting it is the wrong default: SSH asks before accepting an unknown
  /// host key, and browsers refuse unverified certificates outright. This is
  /// the same idea — proceed only if the user knowingly accepts it, and tell
  /// them exactly how to check afterwards.
  ///
  /// Returns true when the user chose to continue.
  Future<bool> _confirmUnverifiedPairing() async {
    final proceed = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (ctx) => AlertDialog(
        icon: const Icon(Icons.gpp_maybe_outlined),
        title: const Text("Can't confirm this is your greenhouse"),
        content: const Text(
          "This is the first time connecting here, so the app can't tell your "
          'greenhouse apart from another device answering on the same '
          'network.\n\n'
          'Safest: cancel and scan the QR code on the hub instead — that '
          'proves which greenhouse you are talking to.\n\n'
          'If you continue, afterwards check that the safety code in Settings '
          'matches the one shown on the hub.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Continue anyway'),
          ),
        ],
      ),
    );
    return proceed ?? false;
  }

  Future<bool> _confirmWithPin(String baseUrl) async {
    // Secure by default: an unverifiable greenhouse needs an informed decision
    // before any secret is exchanged, not after.
    if (_fp.text.trim().isEmpty && !await _confirmUnverifiedPairing()) {
      setState(() {
        _error = 'Pairing cancelled. Scan the QR code on the unit to pair '
                 'securely, or enter its TLS fingerprint under Advanced.';
        _busy = false;
      });
      return true;
    }
    if (!mounted) return true;
    final pin = await _promptForPin();
    if (pin == null) {
      setState(() { _busy = false; });
      return true;
    }
    try {
      final res = await _postPin(baseUrl, pin);
      if (res.statusCode == 200) {
        final j = jsonDecode(res.body) as Map<String, dynamic>;
        _host.text       = j['host_lan']        ?? '';
        _remoteHost.text = j['host_remote']     ?? j['host_tailscale'] ?? '';
        _remoteUser.text = j['remote_username'] ?? '';
        _remotePass.text = j['remote_password'] ?? '';
        _port.text       = (j['port'] ?? 8883).toString();
        _fp.text         = j['tls_fingerprint'] ?? '';
        _user.text       = j['username']        ?? 'app';
        _pass.text       = j['password']        ?? '';
        _apiToken.text   = j['api_token']       ?? '';
        _camToken.text   = j['cam_token']       ?? '';
      } else if (res.statusCode == 401) {
        _error = 'Incorrect PIN.';
      } else if (res.statusCode == 429) {
        _error = 'Too many incorrect PINs from this device. '
                 'Wait a few minutes and try again.';
      } else {
        _error = 'Could not confirm pairing.';
      }
    } on HandshakeException {
      // Only reachable when a fingerprint was known up front, so this is a
      // certificate that does not match the greenhouse we expected.
      // Keep the warning -- this is a genuine security control -- but lead
      // with the far likelier innocent cause. Told only that someone may be
      // impersonating their greenhouse, a user who simply reinstalled the hub
      // has no idea that they are the explanation.
      _error = "This doesn't match the greenhouse you paired with before. "
               'If you recently reset or reinstalled the hub, that is '
               'expected — scan its QR code again. If you did not, stop: '
               'another device may be pretending to be it. Nothing was sent.';
    } catch (e) {
      final friendly = describeError(e);
      _error = '${friendly.title}. ${friendly.message}';
    }
    setState(() { _busy = false; });
    return true;
  }

  Future<String?> _promptForPin() async {
    final controller = TextEditingController();
    final pin = await showDialog<String>(
      context: context,
      barrierDismissible: false,
      builder: (ctx) => AlertDialog(
        title: const Text('Found your greenhouse'),
        content: TextField(
          controller: controller,
          autofocus: true,
          keyboardType: TextInputType.number,
          maxLength: 6,
          decoration: const InputDecoration(
            labelText: 'PIN from the sticker on the hub',
            hintText: '6 digits',
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, controller.text.trim()),
            child: const Text('Confirm'),
          ),
        ],
      ),
    );
    controller.dispose();
    if (!mounted || pin == null || pin.isEmpty) return null;
    return pin;
  }

  Future<void> _discover() async {
    setState(() { _busy = true; _error = null; });

    // Try hostname first (works on iOS; sometimes on Android)
    try {
      const base = 'http://greenhouse.local';
      final res = await http.get(Uri.parse('$base/pair'))
          .timeout(const Duration(seconds: 5));
      if (await _applyPair(res, base)) return;
    } catch (_) {}

    // Fall back to mDNS service discovery (reliable on Android — as long as
    // the multicast lock below is held; Android may otherwise silently
    // filter out incoming mDNS packets to save power, see MainActivity.kt).
    try {
      await MulticastLock.acquire();
      String? ip;
      final client = MDnsClient();
      await client.start();
      outer:
      await for (final PtrResourceRecord ptr in client.lookup<PtrResourceRecord>(
          ResourceRecordQuery.serverPointer('_greenhouse._tcp.local'))) {
        await for (final SrvResourceRecord srv in client.lookup<SrvResourceRecord>(
            ResourceRecordQuery.service(ptr.domainName))) {
          await for (final IPAddressResourceRecord a in client.lookup<IPAddressResourceRecord>(
              ResourceRecordQuery.addressIPv4(srv.target))) {
            ip = a.address.address;
            break outer;
          }
        }
      }
      client.stop();

      if (ip != null) {
        final base = 'http://$ip';
        final res = await http.get(Uri.parse('$base/pair'))
            .timeout(const Duration(seconds: 5));
        if (await _applyPair(res, base)) return;
      }
    } catch (_) {
    } finally {
      await MulticastLock.release();
    }

    setState(() {
      // Deliberately does NOT say "make sure you're on the same WiFi": the
      // user very often IS. Automatic discovery relies on multicast, which
      // Android cannot receive at all when the phone is itself the hotspot
      // the hub is connected to — the exact bench setup here. Blaming the
      // network sends people to re-check something that was never wrong.
      _error = "Couldn't find the greenhouse automatically. "
          'Scan the QR code on the hub instead — it also sets up the secure '
          'connection. If you know the hub\'s address, you can type it in '
          'below.';
      _busy = false;
    });
  }

  Future<void> _save() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() { _busy = true; _error = null; });
    final config = ConnectionConfig(
      lanHost:        _host.text.trim(),
      remoteHost:     _remoteHost.text.trim(),
      port:           int.parse(_port.text.trim()),
      tlsFingerprint: _fp.text.trim(),
      username:       _user.text.trim(),
      password:       _pass.text,
      remoteUsername: _remoteUser.text.trim(),
      remotePassword: _remotePass.text,
      apiToken:       _apiToken.text.trim(),
      camToken:       _camToken.text.trim(),
    );
    try {
      final ok = await ref.read(mqttConnectionProvider).testConnect(config);
      if (!ok) {
        setState(() {
          _error = "Couldn't connect with those details. Check the address and "
              'password, or scan the QR code on the hub to fill them in '
              'automatically.';
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
      {bool obscure = false, TextInputType? type, String? Function(String?)? validator,
       String? hint, String? helper}) =>
      Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: TextFormField(
          controller: c,
          decoration: InputDecoration(
              labelText: label, hintText: hint, helperText: helper,
              helperMaxLines: 3),
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
                onPressed: _busy ? null : _discover,
                icon: const Icon(Icons.search),
                label: const Text('Find my greenhouse automatically'),
                style: FilledButton.styleFrom(
                  minimumSize: const Size.fromHeight(52),
                ),
              ),
              const SizedBox(height: 12),
              OutlinedButton.icon(
                onPressed: () async {
                  final result = await context.push<String>('/pair/qr');
                  if (result != null) _applyQr(result);
                },
                icon: const Icon(Icons.qr_code_scanner),
                label: const Text('Scan the QR code on the hub'),
              ),
              // The QR carries the unit's certificate fingerprint out of band,
              // which is what lets pairing verify the identity of whatever
              // answers on the network. Worth saying plainly rather than
              // leaving the two buttons looking equivalent.
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Text(
                  _fp.text.trim().isEmpty
                      ? 'Scanning the QR code is the safest way — it proves '
                        'which greenhouse you are connecting to, and fills in '
                        'every setting for you.'
                      : "This greenhouse is recognised — we'll check it really "
                        'is yours before connecting.',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ),
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 16),
                child: Row(children: [
                  Expanded(child: Divider()),
                  Padding(padding: EdgeInsets.symmetric(horizontal: 8), child: Text('or enter manually')),
                  Expanded(child: Divider()),
                ]),
              ),
              _field(_host, 'Greenhouse address',
                  hint: '192.168.1.x or greenhouse.local',
                  helper: "Shown on the hub's screen or label"),
              _field(_pass, 'Password', obscure: true,
                  helper: 'From the QR code or the label on the hub'),
              // Every field below stays: when automatic discovery fails —
              // which it always does when the phone is itself the hotspot —
              // typing them in is the only way through. What they lacked was
              // any hint of what they are or where to find them.
              InkWell(
                onTap: () => setState(() => _showAdvanced = !_showAdvanced),
                child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 8),
                  child: Row(children: [
                    Icon(_showAdvanced ? Icons.expand_less : Icons.expand_more, size: 18),
                    const SizedBox(width: 4),
                    Text("Manual setup — only if the QR code isn't available",
                        style: Theme.of(context).textTheme.bodySmall),
                  ]),
                ),
              ),
              if (_showAdvanced) ...[
                Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: Text(
                    'Scanning the QR code fills all of this in for you. Leave '
                    'anything you were not given blank.',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ),
                _field(_remoteHost, 'Address for access from away',
                    validator: (_) => null, hint: 'xxxxx.s1.eu.hivemq.cloud',
                    helper: 'Lets you check the greenhouse when not at home. '
                        'Leave blank to use it only at home.'),
                _field(_remoteUser, 'Username for access from away',
                    validator: (_) => null),
                _field(_remotePass, 'Password for access from away',
                    obscure: true, validator: (_) => null),
                _field(_port, 'Port', type: TextInputType.number,
                    helper: 'Leave as 8883 unless you were told otherwise',
                    validator: (v) => int.tryParse(v ?? '') == null ? 'Must be a number' : null),
                _field(_user, 'Username',
                    helper: 'Leave as "app" unless you were told otherwise'),
                _field(_fp, 'Security fingerprint', validator: (_) => null,
                    helper: "Proves the greenhouse is really yours. Filled in "
                        'automatically by the QR code.'),
                _field(_apiToken, 'History access key', obscure: true,
                    validator: (_) => null,
                    helper: 'Needed to view past readings. From the QR code.'),
                _field(_camToken, 'Camera key', obscure: true,
                    validator: (_) => null,
                    helper: 'Only needed if a camera is fitted.'),
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
