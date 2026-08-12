import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/providers/nodes_provider.dart';
import 'package:greenhouse_app/screens/common/friendly_error_view.dart';
import 'package:greenhouse_app/screens/common/rename_device_dialog.dart';
import 'package:greenhouse_app/screens/devices/mesh_map_screen.dart';
import 'package:greenhouse_app/screens/devices/node_list_tile.dart';
import 'package:greenhouse_app/services/device_names_store.dart';
import 'package:greenhouse_app/utils/display_name.dart';

class DevicesScreen extends ConsumerWidget {
  const DevicesScreen({super.key});
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final nodes = ref.watch(nodesProvider);
    final names = ref.watch(deviceNamesProvider).valueOrNull ?? const {};
    return Scaffold(
      appBar: AppBar(
        title: const Text('Devices'),
        actions: [
          IconButton(
            icon: const Icon(Icons.hub),
            tooltip: 'Sensor network',
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => const MeshMapScreen()),
            ),
          ),
        ],
      ),
      body: nodes.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => FriendlyErrorView(
          error: e,
          onRetry: () => ref.invalidate(nodesProvider),
        ),
        data: (n) => n.isEmpty
            ? const Center(child: Text('No devices found yet'))
            : ListView(children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
                  child: Text(
                    'Hold a device to give it a name.',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ),
                ...n.values.map((node) => NodeListTile(
                      node: node,
                      names: names,
                      onRename: () => showRenameDeviceDialog(
                        context,
                        ref,
                        deviceId: node.nodeId,
                        currentAutoLabel:
                            displayNameFor(node.nodeId, const {}, zone: node.zone),
                      ),
                    )),
              ]),
      ),
    );
  }
}
