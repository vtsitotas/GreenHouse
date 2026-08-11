import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/screens/devices/mesh_map/mesh_node_card.dart';

void main() {
  testWidgets('MeshNodeCard shows zone, router icon, battery, RSSI and sleepy badge', (tester) async {
    final node = NodeStatus(
      nodeId: 'A0B1C2D3E4F5',
      isOnline: true,
      batteryPercent: 72.0,
      lastSeen: DateTime(2026, 7, 26, 10, 0),
      meshRank: 0,
      parentRssi: -55,
      isSleepy: true,
      zone: 'greenhouse-a',
    );
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: MeshNodeCard(node: node))));

    expect(find.text('greenhouse-a'), findsOneWidget);
    expect(find.text('A0B1C2D3E4F5'), findsOneWidget);
    expect(find.byIcon(Icons.router), findsOneWidget);
    expect(find.textContaining('72'), findsOneWidget);
    expect(find.text('-55 dBm'), findsOneWidget);
    expect(find.byIcon(Icons.nightlight_round), findsOneWidget);
    expect(find.byType(Tooltip), findsOneWidget);
    // The bridge's own card doesn't need a "how do I reach the bridge" label.
    expect(find.text('Direct'), findsNothing);
  });

  testWidgets('MeshNodeCard labels a rank-1 node "Direct"', (tester) async {
    final node = NodeStatus(
      nodeId: 'node1',
      isOnline: true,
      lastSeen: DateTime(2026, 7, 26, 10, 0),
      meshRank: 1,
    );
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: MeshNodeCard(node: node))));
    expect(find.text('Direct'), findsOneWidget);
  });

  testWidgets('MeshNodeCard labels a relayed node with its hop count', (tester) async {
    final node = NodeStatus(
      nodeId: 'node2',
      isOnline: true,
      lastSeen: DateTime(2026, 7, 26, 10, 0),
      meshRank: 3,
    );
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: MeshNodeCard(node: node))));
    expect(find.text('3 hops'), findsOneWidget);
  });

  testWidgets('MeshNodeCard shows no connection label when mesh rank is unknown', (tester) async {
    final node = NodeStatus(
      nodeId: 'node4',
      isOnline: true,
      lastSeen: DateTime(2026, 7, 26, 10, 0),
    );
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: MeshNodeCard(node: node))));
    expect(find.text('Direct'), findsNothing);
    expect(find.textContaining('hops'), findsNothing);
  });

  testWidgets('MeshNodeCard falls back to last-4-of-MAC title when zone is unknown', (tester) async {
    final node = NodeStatus(
      nodeId: 'A0B1C2D3E4F5',
      isOnline: true,
      lastSeen: DateTime(2026, 7, 26, 10, 0),
      meshRank: 1,
    );
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: MeshNodeCard(node: node))));

    expect(find.text('E4F5'), findsOneWidget);
    expect(find.byIcon(Icons.sensors), findsOneWidget);
  });

  testWidgets('MeshNodeCard greyscales and shows an Offline pill for offline nodes', (tester) async {
    final node = NodeStatus(
      nodeId: 'node2',
      isOnline: false,
      lastSeen: DateTime(2026, 7, 26, 9, 0),
      meshRank: 2,
    );
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: MeshNodeCard(node: node))));

    expect(find.text('Offline'), findsOneWidget);
    expect(
      find.byWidgetPredicate((w) => w is Opacity && w.opacity == 0.45),
      findsOneWidget,
    );
  });

  testWidgets('MeshNodeCard invokes onTap when tapped', (tester) async {
    var tapped = false;
    final node = NodeStatus(
      nodeId: 'node3',
      isOnline: true,
      lastSeen: DateTime(2026, 7, 26, 9, 0),
      meshRank: 1,
    );
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(body: MeshNodeCard(node: node, onTap: () => tapped = true)),
    ));

    await tester.tap(find.byType(InkWell));
    expect(tapped, isTrue);
  });
}
