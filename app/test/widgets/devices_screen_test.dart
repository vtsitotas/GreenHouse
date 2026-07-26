import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/providers/nodes_provider.dart';
import 'package:greenhouse_app/screens/devices/devices_screen.dart';
import 'package:greenhouse_app/screens/devices/mesh_map_screen.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// `MeshMapScreen` (reachable via the hub icon) reads pinned positions from
/// `SharedPreferences` on build (`pinnedPositionsProvider`) — mock it here so
/// navigating into it in a test never hits a real platform channel (same
/// setup as `mesh_map_screen_test.dart`).
Future<void> _pumpDevicesScreen(WidgetTester tester, Map<String, NodeStatus> nodes) async {
  SharedPreferences.setMockInitialValues({});
  await tester.pumpWidget(ProviderScope(
    overrides: [
      nodesProvider.overrideWith((ref) => Stream.value(nodes)),
    ],
    child: const MaterialApp(home: DevicesScreen()),
  ));
  await tester.pump();
}

void main() {
  group('DevicesScreen', () {
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
