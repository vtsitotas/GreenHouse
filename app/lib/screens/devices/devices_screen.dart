import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/providers/nodes_provider.dart';
import 'package:greenhouse_app/screens/common/friendly_error_view.dart';
import 'package:greenhouse_app/screens/common/rename_device_dialog.dart';
import 'package:greenhouse_app/screens/devices/mesh_map_screen.dart';
import 'package:greenhouse_app/screens/devices/node_list_tile.dart';
import 'package:greenhouse_app/services/device_names_store.dart';
import 'package:greenhouse_app/utils/display_name.dart';

import 'package:greenhouse_app/providers/sensor_provisioning_provider.dart';
import 'package:greenhouse_app/screens/pairing/qr_scan_screen.dart';
import 'package:greenhouse_app/screens/devices/add_sensor_screen.dart';
import 'package:greenhouse_app/models/sensor_enrolment.dart';

class DevicesScreen extends ConsumerWidget {
  const DevicesScreen({super.key});
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final nodes = ref.watch(nodesProvider);
    final names = ref.watch(deviceNamesProvider).valueOrNull ?? const {};
    final unenrolled = ref.watch(unenrolledMacsProvider).valueOrNull ?? const [];

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
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () async {
          final qr = await Navigator.push<String>(
            context,
            MaterialPageRoute(builder: (_) => const QrScanScreen()),
          );
          if (qr == null || !context.mounted) return;
          
          SensorEnrolment sensor;
          try {
            sensor = SensorEnrolment.fromQr(qr);
          } on FormatException catch (e) {
            ScaffoldMessenger.of(context).showSnackBar(
              SnackBar(content: Text(e.message)),
            );
            return;
          }
          
          final svc = ref.read(sensorProvisioningServiceProvider);
          if (svc == null) return;
          
          final added = await Navigator.push<bool>(
            context,
            MaterialPageRoute(
              builder: (_) => AddSensorScreen(
                scanned: sensor,
                onSubmit: (zone, name, sleepy) =>
                    svc.enrol(sensor: sensor, zone: zone, name: name, sleepy: sleepy),
              ),
            ),
          );
          if (added == true && context.mounted) {
             ref.invalidate(unenrolledMacsProvider);
             ref.invalidate(nodesProvider);
          }
        },
        icon: const Icon(Icons.add),
        label: const Text('Add sensor'),
      ),
      body: nodes.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => FriendlyErrorView(
          error: e,
          onRetry: () => ref.invalidate(nodesProvider),
        ),
        data: (n) => ListView(
          children: [
            if (unenrolled.isNotEmpty)
              MaterialBanner(
                content: const Text(
                  "A sensor nearby hasn't been added yet — scan the code on its box.",
                ),
                leading: const Icon(Icons.sensors_off),
                actions: [
                  TextButton(
                    onPressed: () => ref.invalidate(unenrolledMacsProvider),
                    child: const Text('DISMISS'),
                  ),
                ],
              ),
            if (n.isEmpty)
              const Padding(
                padding: EdgeInsets.all(32.0),
                child: Center(child: Text('No devices found yet')),
              ),
            if (n.isNotEmpty)
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
          ],
        ),
      ),
    );
  }
}
