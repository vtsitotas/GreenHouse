import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/screens/devices/node_list_tile.dart';

void main() {
  testWidgets('NodeListTile shows Online badge and battery for online node', (tester) async {
    final node = NodeStatus(nodeId: 'node1', isOnline: true, batteryPercent: 72.0, lastSeen: DateTime(2026, 6, 25, 10, 0));
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: NodeListTile(node: node))));
    expect(find.text('node1'), findsOneWidget);
    expect(find.text('Online'), findsOneWidget);
    expect(find.textContaining('72'), findsOneWidget);
  });

  testWidgets('NodeListTile shows Offline badge for offline node', (tester) async {
    final node = NodeStatus(nodeId: 'node2', isOnline: false, lastSeen: DateTime(2026, 6, 25, 9, 0));
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: NodeListTile(node: node))));
    expect(find.text('Offline'), findsOneWidget);
  });
}
