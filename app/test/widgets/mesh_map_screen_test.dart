import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/providers/nodes_provider.dart';
import 'package:greenhouse_app/screens/devices/mesh_map/node_positions_store.dart';
import 'package:greenhouse_app/screens/devices/mesh_map_screen.dart';
import 'package:shared_preferences/shared_preferences.dart';

NodeStatus _node(
  String id, {
  bool isOnline = true,
  int? rank,
  String? zone,
  int? rssi,
  String? parentId,
}) =>
    NodeStatus(
      nodeId: id,
      isOnline: isOnline,
      lastSeen: DateTime(2026, 7, 26, 10, 0),
      meshRank: rank,
      zone: zone,
      parentRssi: rssi,
      parentId: parentId,
    );

/// Pumps `MeshMapScreen` with `nodesProvider` overridden to a fixed map and
/// `SharedPreferences` backed by an in-memory mock (no pins stored).
///
/// IMPORTANT: the screen owns a *repeating* `AnimationController` (the link
/// flow animation, 1.5s cycle) — `tester.pumpAndSettle()` would spin
/// forever waiting for it to stop animating and time out the test. Every
/// test here uses fixed `tester.pump(...)` steps instead.
Future<void> _pumpMeshMap(WidgetTester tester, Map<String, NodeStatus> nodes) async {
  SharedPreferences.setMockInitialValues({});
  await tester.pumpWidget(ProviderScope(
    overrides: [
      nodesProvider.overrideWith((ref) => Stream.value(nodes)),
    ],
    child: const MaterialApp(home: MeshMapScreen()),
  ));
  // Fixed pump steps (never pumpAndSettle — see note above) to let the
  // overridden nodesProvider stream and the pinned-positions AsyncNotifier's
  // initial SharedPreferences load both resolve and rebuild.
  await tester.pump();
  await tester.pump(const Duration(milliseconds: 20));
  await tester.pump(const Duration(milliseconds: 50));
}

void main() {
  group('MeshMapScreen', () {
    testWidgets('renders a card per node with zone title and bridge router icon',
        (tester) async {
      await _pumpMeshMap(tester, {
        'A0B1C2D3E4F5': _node('A0B1C2D3E4F5', rank: 0, zone: 'bridge-zone'),
        'node1': _node('node1',
            rank: 1, zone: 'zone1', rssi: -55, parentId: 'A0B1C2D3E4F5'),
      });

      expect(find.text('bridge-zone'), findsOneWidget);
      expect(find.text('zone1'), findsOneWidget);
      expect(find.byIcon(Icons.router), findsOneWidget);
    });

    testWidgets('offline node shows Offline pill', (tester) async {
      await _pumpMeshMap(tester, {
        'A0B1C2D3E4F5': _node('A0B1C2D3E4F5', rank: 0, zone: 'bridge-zone'),
        'node1': _node('node1', isOnline: false, rank: 1, zone: 'zone1'),
      });

      expect(find.text('Offline'), findsOneWidget);
    });

    testWidgets('degraded-data banner appears when nodes exist but none has meshRank',
        (tester) async {
      await _pumpMeshMap(tester, {
        'node1': _node('node1', zone: 'zone1'),
        'node2': _node('node2', zone: 'zone2'),
      });

      expect(
        find.textContaining('Mesh topology data not being published yet'),
        findsOneWidget,
      );
    });

    testWidgets('no degraded-data banner once at least one node has a meshRank',
        (tester) async {
      await _pumpMeshMap(tester, {
        'A0B1C2D3E4F5': _node('A0B1C2D3E4F5', rank: 0, zone: 'bridge-zone'),
        'node1': _node('node1', zone: 'zone1'), // no /mesh data yet
      });

      expect(
        find.textContaining('Mesh topology data not being published yet'),
        findsNothing,
      );
    });

    testWidgets('empty node map shows the empty state', (tester) async {
      await _pumpMeshMap(tester, {});

      expect(find.text('No nodes detected yet'), findsOneWidget);
    });

    testWidgets('tapping a card opens a detail sheet with MAC and rank', (tester) async {
      await _pumpMeshMap(tester, {
        'A0B1C2D3E4F5': _node('A0B1C2D3E4F5', rank: 0, zone: 'bridge-zone'),
      });

      await tester.tap(find.byType(InkWell));
      // Bottom-sheet enter transition is a one-shot animation (unlike the
      // screen's repeating link-flow controller) — safe to step through
      // with fixed pumps, still no pumpAndSettle given the repeating
      // controller keeps running underneath.
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 300));

      expect(find.text('A0B1C2D3E4F5'), findsWidgets); // card subtitle + MAC row
      expect(find.text('0'), findsOneWidget); // Rank row value, unique in this fixture
    });
  });

  group('NodePositionsStore', () {
    test('pin persists a position; load returns it; unpin clears it', () async {
      SharedPreferences.setMockInitialValues({});
      const store = NodePositionsStore();

      expect(await store.load(), isEmpty);

      await store.pin('node1', const Offset(0.31, 0.62));
      final loaded = await store.load();
      expect(loaded['node1'], const Offset(0.31, 0.62));

      await store.unpin('node1');
      expect(await store.load(), isEmpty);
    });
  });
}
