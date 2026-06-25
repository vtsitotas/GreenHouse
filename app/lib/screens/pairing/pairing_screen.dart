import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:greenhouse_app/models/connection_config.dart';
import 'package:greenhouse_app/services/pairing_service.dart';

class PairingScreen extends ConsumerStatefulWidget {
  const PairingScreen({super.key});
  @override ConsumerState<PairingScreen> createState() => _PairingScreenState();
}

class _PairingScreenState extends ConsumerState<PairingScreen> {
  final _formKey = GlobalKey<FormState>();
  final _lan      = TextEditingController(text: 'greenhouse.local');
  final _ts       = TextEditingController();
  final _port     = TextEditingController(text: '9001');
  final _fp       = TextEditingController();
  final _user     = TextEditingController(text: 'app');
  final _pass     = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    for (final c in [_lan, _ts, _port, _fp, _user, _pass]) c.dispose();
    super.dispose();
  }

  void _applyQr(String raw) {
    try {
      final j = jsonDecode(raw) as Map<String, dynamic>;
      _lan.text  = j['host_lan']        ?? '';
      _ts.text   = j['host_tailscale']  ?? '';
      _port.text = (j['port'] ?? 9001).toString();
      _fp.text   = j['tls_fingerprint'] ?? '';
      _user.text = j['username']        ?? 'app';
      _pass.text = j['password']        ?? '';
    } catch (_) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Invalid QR code')));
    }
  }

  Future<void> _save() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() { _busy = true; _error = null; });
    try {
      await ref.read(pairingServiceProvider).saveConfig(ConnectionConfig(
        lanHost: _lan.text.trim(),
        tailscaleHost: _ts.text.trim(),
        port: int.parse(_port.text.trim()),
        tlsFingerprint: _fp.text.trim(),
        username: _user.text.trim(),
        password: _pass.text,
      ));
      if (mounted) context.go('/dashboard');
    } catch (e) {
      setState(() { _error = e.toString(); _busy = false; });
    }
  }

  Widget _field(TextEditingController c, String label,
      {bool obscure = false, TextInputType? type, String? Function(String?)? validator}) =>
      Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: TextFormField(
          controller: c,
          decoration: InputDecoration(labelText: label),
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
              _field(_lan,  'LAN host (mDNS)'),
              _field(_ts,   'Tailscale IP'),
              _field(_port, 'Port', type: TextInputType.number,
                  validator: (v) => int.tryParse(v ?? '') == null ? 'Must be a number' : null),
              _field(_fp,   'TLS fingerprint', validator: (_) => null),
              _field(_user, 'Username'),
              _field(_pass, 'Password', obscure: true),
              if (_error != null) ...[
                Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
                const SizedBox(height: 12),
              ],
              FilledButton(
                onPressed: _busy ? null : _save,
                child: _busy ? const CircularProgressIndicator() : const Text('Connect'),
              ),
            ]),
          ),
        ),
      );
}
