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
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Invalid QR code')));
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
        _error = 'Pairing window expired. Restart the Pi and try again within 10 minutes.';
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
        title: const Text('Cannot verify this greenhouse'),
        content: const Text(
          'This greenhouse has not been identified before, so the app cannot '
          'confirm it is really yours rather than another device answering on '
          'the network.\n\n'
          'The most secure option is to cancel and pair by scanning the QR '
          'code instead.\n\n'
          'If you continue, check afterwards that Settings → Safety code '
          'matches the code shown by "selftest.sh" on the Pi.',
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
      _error = 'Security warning: this device presented an unexpected '
               'certificate. Someone may be impersonating your greenhouse. '
               'Pairing was cancelled and no PIN was sent.';
    } catch (e) {
      _error = 'Could not reach the greenhouse: $e';
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
            labelText: 'PIN',
            hintText: '6-digit PIN from the device label',
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
      _error = 'Greenhouse not found. Make sure you are on the same WiFi.';
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
                onPressed: _busy ? null : _discover,
                icon: const Icon(Icons.search),
                label: const Text('Find my greenhouse'),
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
                label: const Text('Scan QR code'),
              ),
              // The QR carries the unit's certificate fingerprint out of band,
              // which is what lets pairing verify the identity of whatever
              // answers on the network. Worth saying plainly rather than
              // leaving the two buttons looking equivalent.
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Text(
                  _fp.text.trim().isEmpty
                      ? 'Scanning the QR is the most secure option — it lets the '
                        'app verify the greenhouse\'s identity.'
                      : 'Greenhouse identity known — pairing will be verified.',
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
                _field(_remoteHost, 'Remote host (HiveMQ)', validator: (_) => null, hint: 'xxxxx.s1.eu.hivemq.cloud'),
                _field(_remoteUser, 'Remote username', validator: (_) => null),
                _field(_remotePass, 'Remote password', obscure: true, validator: (_) => null),
                _field(_port, 'Port', type: TextInputType.number,
                    validator: (v) => int.tryParse(v ?? '') == null ? 'Must be a number' : null),
                _field(_user, 'Username'),
                _field(_fp, 'TLS fingerprint', validator: (_) => null),
                _field(_apiToken, 'History API token', obscure: true, validator: (_) => null),
                _field(_camToken, 'Camera token', obscure: true, validator: (_) => null),
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
