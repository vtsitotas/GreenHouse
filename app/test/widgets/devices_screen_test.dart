import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/providers/nodes_provider.dart';
import 'package:greenhouse_app/providers/sensor_provisioning_provider.dart';
import 'package:greenhouse_app/screens/devices/devices_screen.dart';
import 'package:greenhouse_app/screens/devices/mesh_map_screen.dart';
import 'package:greenhouse_app/services/sensor_provisioning_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// `MeshMapScreen` (reachable via the hub icon) reads pinned positions from
/// `SharedPreferences` on build (`pinnedPositionsProvider`) — mock it here so
/// navigating into it in a test never hits a real platform channel (same
/// setup as `mesh_map_screen_test.dart`).
Future<void> _pumpDevicesScreen(WidgetTester tester, Map<String, NodeStatus> nodes,
    {List<EnrolledSensor> enrolled = const [], List<String> unenrolled = const []}) async {
  SharedPreferences.setMockInitialValues({});
  await tester.pumpWidget(ProviderScope(
    overrides: [
      nodesProvider.overrideWith((ref) => Stream.value(nodes)),
      enrolledSensorsProvider.overrideWith((ref) async => enrolled),
      unenrolledMacsProvider.overrideWith((ref) async => unenrolled),
    ],
    child: const MaterialApp(home: DevicesScreen()),
  ));
  await tester.pump();
  await tester.pump();   // let the FutureProviders resolve
}

void main() {
  group('DevicesScreen', () {
    // A sensor added through the app but not yet reporting used to be
    // invisible -- a wrong QR, a sensor that was off, or one whose
    // provisioning failed all looked exactly like "nothing happened".
    testWidgets('an added sensor with no reading yet shows as waiting', (tester) async {
      await _pumpDevicesScreen(tester, const {},
          enrolled: const [EnrolledSensor(mac: '206EF16C6B50', zone: 'zone3', name: 'zone3')]);
      expect(find.textContaining('Waiting for first reading'), findsOneWidget);
      expect(find.textContaining('206EF16C6B50'), findsOneWidget);
    });

    testWidgets('an added sensor still sending join beacons shows as joining', (tester) async {
      await _pumpDevicesScreen(tester, const {},
          enrolled: const [EnrolledSensor(mac: '206EF16C6B50', zone: 'zone3', name: 'zone3')],
          unenrolled: const ['206EF16C6B50']);
      expect(find.textContaining('Joining'), findsOneWidget);
      // Already added, so the "not added yet -- scan its code" banner is wrong here.
      expect(find.textContaining("hasn't been added yet"), findsNothing);
    });

    testWidgets('a sensor that already reports is not also listed as waiting', (tester) async {
      await _pumpDevicesScreen(tester, {
        '206EF16C6B50': NodeStatus(nodeId: '206EF16C6B50', isOnline: true,
            lastSeen: DateTime(2026, 9, 24, 19, 40), zone: 'zone3'),
      }, enrolled: const [EnrolledSensor(mac: '206EF16C6B50', zone: 'zone3', name: 'zone3')]);
      expect(find.textContaining('Waiting for first reading'), findsNothing);
      expect(find.textContaining('Joining'), findsNothing);
    });

    testWidgets('a nearby sensor nobody added still shows the add banner', (tester) async {
      await _pumpDevicesScreen(tester, const {}, unenrolled: const ['AABBCCDDEEFF']);
      expect(find.textContaining("hasn't been added yet"), findsOneWidget);
    });

    testWidgets('AppBar has a hub icon action', (tester) async {
      await _pumpDevicesScreen(tester, {
        'node1': NodeStatus(nodeId: 'node1', isOnline: true, lastSeen: DateTime(2026, 7, 26, 10, 0)),
      });

      expect(find.byIcon(Icons.hub), findsOneWidget);
    });

    testWidgets('tapping the hub icon navigates to MeshMapScreen', (tester) async {
      await _pumpDevicesScreen(tester, {
        'node1': NodeStatus(nodeId: 'node1', isOnline: true, lastSeen: DateTime(2026, 7, 26, 10, 0)),
      });

      await tester.tap(find.byIcon(Icons.hub));
      // MeshMapScreen owns a repeating AnimationController (link-flow phase,
      // 1.5s cycle) — never pumpAndSettle here, use fixed pump steps instead
      // (same convention as mesh_map_screen_test.dart).
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 20));
      await tester.pump(const Duration(milliseconds: 50));

      expect(find.byType(MeshMapScreen), findsOneWidget);
    });
  });
}
