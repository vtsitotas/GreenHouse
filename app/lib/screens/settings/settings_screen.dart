import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';
import 'package:greenhouse_app/services/pairing_service.dart';

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
