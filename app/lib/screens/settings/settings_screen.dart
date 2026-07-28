import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';
import 'package:greenhouse_app/services/pairing_service.dart';
import 'package:greenhouse_app/utils/cert_pinning.dart';

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
            data: (s) => Text(s.name),
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
              subtitle: Text(
                code.isEmpty
                    ? 'Not paired'
                    : '$code\nMust match the Pi: sudo bash scripts/selftest.sh',
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
          title: const Text('Re-pair with server'),
          onTap: () => context.go('/pair'),
        ),
        ListTile(
          leading: const Icon(Icons.logout),
          title: const Text('Disconnect'),
          onTap: () async {
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
