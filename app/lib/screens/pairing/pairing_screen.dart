import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
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
  final _host   = TextEditingController(text: 'greenhouse.local');
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

  Future<void> _discover() async {
    setState(() { _busy = true; _error = null; });
    try {
      final uri = Uri.parse('http://greenhouse.local:8080/pair');
      final response = await http.get(uri).timeout(const Duration(seconds: 10));
      if (response.statusCode == 200) {
        final j = jsonDecode(response.body) as Map<String, dynamic>;
        _host.text   = j['host_lan']        ?? '';
        _tsHost.text = j['host_tailscale']  ?? '';
        _port.text   = (j['port'] ?? 8883).toString();
        _fp.text     = j['tls_fingerprint'] ?? '';
        _user.text   = j['username']        ?? 'app';
        _pass.text   = j['password']        ?? '';
        setState(() { _busy = false; });
      } else if (response.statusCode == 403) {
        setState(() {
          _error = 'Pairing window expired. Restart the Pi and try again within 5 minutes.';
          _busy = false;
        });
      } else {
        setState(() {
          _error = 'Greenhouse not found. Make sure you are on the same WiFi.';
          _busy = false;
        });
      }
    } catch (_) {
      setState(() {
        _error = 'Greenhouse not found. Make sure you are on the same WiFi.';
        _busy = false;
      });
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
