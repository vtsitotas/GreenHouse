import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/screens/devices/node_list_tile.dart';

void main() {
  testWidgets('NodeListTile shows Online badge and battery for online node', (tester) async {
    final node = NodeStatus(nodeId: 'node1', isOnline: true, batteryPercent: 72.0, lastSeen: DateTime(2026, 6, 25, 10, 0));
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: NodeListTile(node: node))));
    // With no name and no zone, the id's tail is the honest fallback.
    expect(find.text('ode1'), findsOneWidget);
    expect(find.text('Online'), findsOneWidget);
    expect(find.textContaining('72'), findsOneWidget);
  });

  testWidgets('a user-set name replaces the id as the title, id kept as subtitle',
      (tester) async {
    final node = NodeStatus(
        nodeId: '206EF16CA1B0', isOnline: true, lastSeen: DateTime(2026, 6, 25, 10, 0));

    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: NodeListTile(node: node, names: const {'206EF16CA1B0': 'Tomato bed'}),
      ),
    ));

    expect(find.text('Tomato bed'), findsOneWidget);
    // The MAC still has a job: matching the sticker on the physical box.
    expect(find.textContaining('206EF16CA1B0'), findsOneWidget);
  });

  testWidgets('falls back to the zone name when the user has not named it',
      (tester) async {
    final node = NodeStatus(
        nodeId: '206EF16CA1B0',
        isOnline: true,
        zone: 'zone1',
        lastSeen: DateTime(2026, 6, 25, 10, 0));

    await tester.pumpWidget(
        MaterialApp(home: Scaffold(body: NodeListTile(node: node))));

    expect(find.text('Zone 1'), findsOneWidget);
  });

  testWidgets('long-press triggers rename when a handler is given', (tester) async {
    var renamed = false;
    final node = NodeStatus(nodeId: 'node1', isOnline: true, lastSeen: DateTime(2026, 6, 25));

    await tester.pumpWidget(MaterialApp(
      home: Scaffold(body: NodeListTile(node: node, onRename: () => renamed = true)),
    ));

    await tester.longPress(find.byType(ListTile));
    expect(renamed, isTrue);
  });

  testWidgets('NodeListTile shows Offline badge for offline node', (tester) async {
    final node = NodeStatus(nodeId: 'node2', isOnline: false, lastSeen: DateTime(2026, 6, 25, 9, 0));
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: NodeListTile(node: node))));
    expect(find.text('Offline'), findsOneWidget);
  });

  testWidgets('NodeListTile shows a bare time for a last-seen from today', (tester) async {
    final now = DateTime.now();
    final node = NodeStatus(nodeId: 'node3', isOnline: true, lastSeen: now);
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: NodeListTile(node: node))));
    final hm = '${now.hour.toString().padLeft(2, '0')}:${now.minute.toString().padLeft(2, '0')}';
    expect(find.text('Last seen: $hm'), findsOneWidget);
  });

  testWidgets('NodeListTile qualifies an old last-seen with a date instead of a bare time',
      (tester) async {
    final node = NodeStatus(nodeId: 'node4', isOnline: false, lastSeen: DateTime(2026, 6, 25, 9, 0));
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: NodeListTile(node: node))));
    expect(find.textContaining('Jun 25'), findsOneWidget);
  });
}
