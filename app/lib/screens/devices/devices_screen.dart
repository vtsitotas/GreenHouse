import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/providers/nodes_provider.dart';
import 'package:greenhouse_app/screens/devices/node_list_tile.dart';

class DevicesScreen extends ConsumerWidget {
  const DevicesScreen({super.key});
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final nodes = ref.watch(nodesProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Devices')),
      body: nodes.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(child: Text('Error: $e')),
        data: (n) => n.isEmpty
            ? const Center(child: Text('No nodes detected yet'))
            : ListView(children: n.values.map((node) => NodeListTile(node: node)).toList()),
      ),
    );
  }
}
