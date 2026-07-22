import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:multicast_dns/multicast_dns.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:greenhouse_app/models/connection_config.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';
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
  bool _busy = false;
  bool _showAdvanced = false;
  String? _error;

  @override
  void dispose() {
    for (final c in [_host, _pass, _remoteHost, _remoteUser, _remotePass, _port, _fp, _user]) {
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

  Future<bool> _confirmWithPin(String baseUrl) async {
    final pin = await _promptForPin();
    if (pin == null) {
      setState(() { _busy = false; });
      return true;
    }
    try {
      final res = await http
          .post(Uri.parse('$baseUrl/pair/confirm'),
              headers: {'Content-Type': 'application/json'},
              body: jsonEncode({'pin': pin}))
          .timeout(const Duration(seconds: 5));
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
      } else if (res.statusCode == 401) {
        _error = 'Incorrect PIN.';
      } else if (res.statusCode == 429) {
        _error = 'Too many incorrect PINs. Restart the Pi to try again.';
      } else {
        _error = 'Could not confirm pairing.';
      }
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

    // Fall back to mDNS service discovery (reliable on Android)
    try {
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
    } catch (_) {}

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
