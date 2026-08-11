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

    testWidgets('summary bar shows node/online/offline counts', (tester) async {
      await _pumpMeshMap(tester, {
        'A0B1C2D3E4F5': _node('A0B1C2D3E4F5', rank: 0, zone: 'bridge-zone'),
        'node1': _node('node1', rank: 1, zone: 'zone1'),
        'node2': _node('node2', isOnline: false, rank: 1, zone: 'zone2'),
      });

      expect(find.text('3 nodes · 2 online · 1 offline'), findsOneWidget);
    });

    testWidgets('link-quality legend is shown alongside the canvas', (tester) async {
      await _pumpMeshMap(tester, {
        'A0B1C2D3E4F5': _node('A0B1C2D3E4F5', rank: 0, zone: 'bridge-zone'),
      });

      expect(find.textContaining('Good ('), findsOneWidget);
      expect(find.textContaining('Stale / offline'), findsOneWidget);
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
      expect(find.text('This is the bridge'), findsOneWidget); // Connection row
    });

    testWidgets('detail sheet labels a relayed node with its hop count and parent',
        (tester) async {
      // The canvas is a fixed 1200x1600 logical pixels regardless of node
      // count -- a rank-2 row sits below the default 600px-tall test
      // viewport, so grow the viewport to fit the whole canvas rather than
      // panning the InteractiveViewer mid-test.
      tester.view.physicalSize = const Size(1200, 1600);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await _pumpMeshMap(tester, {
        'A0B1C2D3E4F5': _node('A0B1C2D3E4F5', rank: 0, zone: 'bridge-zone'),
        'node1': _node('node1', rank: 1, zone: 'zone1', parentId: 'A0B1C2D3E4F5'),
        'node2': _node('node2', rank: 2, zone: 'zone2', parentId: 'node1'),
      });

      // Three cards on screen -> tap the one with rank 2 specifically by its
      // zone label, since InkWell alone would be ambiguous with three cards.
      await tester.tap(find.text('zone2'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 300));

      expect(find.text('Relayed — 2 hops (via node1)'), findsOneWidget);
    });

    testWidgets('dragging a card actually pins it at the dragged-to position, and Unpin clears it',
        (tester) async {
      // Regression test for a real bug: _buildCard used to switch between
      // Positioned and AnimatedPositioned (different widget types) based on
      // isDragging, with no Key on either. The moment a drag starts,
      // MeshLayout's insertion order shifts (pinned nodes are emitted where
      // first encountered rather than in their rank-sorted slot), and
      // without a stable key Flutter's unkeyed reconciliation, combined
      // with the type switch, tore down and rebuilt the Element -- killing
      // the live GestureRecognizer mid-drag. onPanStart fired once (with
      // the pre-drag position); onPanUpdate/onPanEnd for that same touch
      // never arrived, so the pin silently captured the ORIGINAL position
      // -- indistinguishable from auto-layout, making "Unpin" look like a
      // no-op since there was nothing actually moved to undo.
      // Default 800x600 puts the link-quality legend (bottom-right overlay)
      // right on top of the rank-1 card's position, stealing the pointer
      // down before it ever reaches the card -- grow the viewport to fit
      // the whole canvas instead, same as the relayed-node detail test.
      tester.view.physicalSize = const Size(1200, 1600);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      SharedPreferences.setMockInitialValues({});
      late ProviderContainer container;
      await tester.pumpWidget(ProviderScope(
        overrides: [
          nodesProvider.overrideWith((ref) => Stream.value({
                'A0B1C2D3E4F5': _node('A0B1C2D3E4F5', rank: 0, zone: 'bridge-zone'),
                'node1': _node('node1', rank: 1, zone: 'zone1'),
              })),
        ],
        child: Consumer(builder: (context, ref, _) {
          container = ProviderScope.containerOf(context);
          return const MaterialApp(home: MeshMapScreen());
        }),
      ));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 20));
      await tester.pump(const Duration(milliseconds: 50));

      const originalPosition = Offset(0.5, 0.3); // rank-1 row, sole occupant
      expect(container.read(pinnedPositionsProvider).valueOrNull, isEmpty);

      // Multiple incremental moves (not a single teleport) -- this is what
      // exposed the bug: the Element/recognizer died after the very first
      // setState, so only a single-jump gesture could ever look like it
      // "worked" (and even then, at the wrong position).
      final gesture = await tester.startGesture(tester.getCenter(find.text('zone1')));
      await tester.pump(const Duration(milliseconds: 50));
      for (var i = 0; i < 5; i++) {
        await gesture.moveBy(const Offset(30, 40));
        await tester.pump(const Duration(milliseconds: 20));
      }
      await gesture.up();
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 300));

      final pinnedAfterDrag = container.read(pinnedPositionsProvider).valueOrNull;
      expect(pinnedAfterDrag?['node1'], isNotNull);
      expect(pinnedAfterDrag!['node1'], isNot(originalPosition),
          reason: 'the pin must capture where the card was actually dragged to, '
              'not silently snap back to its pre-drag auto-layout position');

      // Long-press -> confirm "Unpin" in the dialog -> pin is gone.
      await tester.longPress(find.text('zone1'));
      await tester.pump();
      expect(find.text('Unpin node?'), findsOneWidget);
      await tester.tap(find.text('Unpin'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));

      expect(container.read(pinnedPositionsProvider).valueOrNull?.containsKey('node1'), isFalse);
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
