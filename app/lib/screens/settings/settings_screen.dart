import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';
import 'package:greenhouse_app/services/pairing_service.dart';
import 'package:greenhouse_app/utils/cert_pinning.dart';
import 'package:greenhouse_app/utils/plain_language.dart';

class SettingsScreen extends ConsumerWidget {
  const SettingsScreen({super.key});
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final statusAsync = ref.watch(connectionStatusProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(children: [
        ListTile(
          leading: const Icon(Icons.wifi),
          title: const Text('Connection'),
          subtitle: statusAsync.when(
            // `s.name` is the raw enum identifier ("local", "reconnecting").
            // It reads like a debug print, and "local" in particular tells a
            // user nothing about whether things are working.
            data: (s) => Text(connectionStatusLabel(s)),
            loading: () => const Text('Connecting…'),
            error: (_, __) => const Text('Unknown'),
          ),
        ),
        const Divider(),
        // Out-of-band identity check. Pairing pins the certificate whenever the
        // fingerprint is known in advance (QR), but a first-time pair over mDNS
        // has nothing to verify against — a man-in-the-middle could have
        // substituted its own certificate. Comparing this code once against the
        // one `selftest.sh` prints on the Pi detects exactly that, the same way
        // an SSH host-key fingerprint does.
        ref.watch(savedConfigProvider).when(
          data: (config) {
            final code = safetyCode(config?.tlsFingerprint ?? '');
            return ListTile(
              leading: const Icon(Icons.verified_user_outlined),
              title: const Text('Safety code'),
              // The shell command that used to live here (`sudo bash
              // scripts/selftest.sh`) is an operator instruction, not
              // something to put in front of whoever is holding the phone.
              // It lives in SECURITY.md, where someone with a terminal open
              // will actually look.
              subtitle: Text(
                code.isEmpty
                    ? 'Not paired'
                    : '$code\nThis code identifies your greenhouse. It should '
                        'match the one shown on the hub.',
              ),
              isThreeLine: code.isNotEmpty,
            );
          },
          loading: () => const ListTile(
            leading: Icon(Icons.verified_user_outlined),
            title: Text('Safety code'),
            subtitle: Text('Loading…'),
          ),
          error: (_, __) => const ListTile(
            leading: Icon(Icons.verified_user_outlined),
            title: Text('Safety code'),
            subtitle: Text('Unavailable'),
          ),
        ),
        const Divider(),
        ListTile(
          leading: const Icon(Icons.qr_code),
          title: const Text('Connect to a greenhouse again'),
          subtitle: const Text('Scan the QR code on the hub'),
          onTap: () => context.go('/pair'),
        ),
        ListTile(
          leading: const Icon(Icons.logout),
          title: const Text('Forget this greenhouse'),
          subtitle: const Text('Removes the connection from this phone'),
          // This wipes the stored pairing -- MQTT credentials, the API token
          // and the pinned certificate. It used to fire on a single tap, one
          // row below a navigation item, with nothing to undo it: the only
          // way back is the QR code (or typing every field by hand).
          onTap: () async {
            final confirmed = await showDialog<bool>(
              context: context,
              builder: (dialogContext) => AlertDialog(
                icon: const Icon(Icons.warning_amber_rounded),
                title: const Text('Forget this greenhouse?'),
                content: const Text(
                  'This phone will stop showing your greenhouse. To connect '
                  'again you will need to scan the QR code on the hub.',
                ),
                actions: [
                  TextButton(
                    onPressed: () => Navigator.of(dialogContext).pop(false),
                    child: const Text('Cancel'),
                  ),
                  FilledButton(
                    onPressed: () => Navigator.of(dialogContext).pop(true),
                    child: const Text('Forget'),
                  ),
                ],
              ),
            );
            if (confirmed != true) return;
            await ref.read(pairingServiceProvider).clearConfig();
            if (context.mounted) context.go('/pair');
          },
        ),
        const Divider(),
        const ListTile(
          leading: Icon(Icons.info_outline),
          title: Text('App version'),
          subtitle: Text('1.0.0'),
        ),
      ]),
    );
  }
}
