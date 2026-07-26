import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/node_status.dart';

void main() {
  group('NodeStatus.fromMqttMesh', () {
    test('parses a full mesh payload', () {
      final status = NodeStatus.fromMqttMesh(
        'node2',
        '{"parent":"206EF16C6B50","rank":2,"rssi":-61,"sleepy":true,'
        '"battery_mv":3312,"zone":"zone1","ts":1753500000}',
      );
      expect(status.nodeId, 'node2');
      expect(status.isOnline, isTrue);
      expect(status.parentId, '206EF16C6B50');
      expect(status.meshRank, 2);
      expect(status.parentRssi, -61);
      expect(status.isSleepy, isTrue);
      expect(status.zone, 'zone1');
      expect(status.batteryMv, 3312);
      expect(status.lastSeen.difference(DateTime.now()).inSeconds.abs(), lessThan(5));
    });

    test('explicit nulls map to null fields', () {
      final status = NodeStatus.fromMqttMesh(
        'node3',
        '{"parent":null,"rank":1,"rssi":null,"sleepy":false,'
        '"battery_mv":null,"zone":null}',
      );
      expect(status.parentId, isNull);
      expect(status.parentRssi, isNull);
      expect(status.batteryMv, isNull);
      expect(status.zone, isNull);
      expect(status.isSleepy, isFalse);
      expect(status.meshRank, 1);
    });

    test('unknown keys are ignored, absent keys become null', () {
      final status = NodeStatus.fromMqttMesh(
        'node1',
        '{"rank":1,"unexpected_field":"whatever"}',
      );
      expect(status.meshRank, 1);
      expect(status.parentId, isNull);
      expect(status.parentRssi, isNull);
      expect(status.isSleepy, isNull);
      expect(status.zone, isNull);
      expect(status.batteryMv, isNull);
    });

    test('bridge record: parent null, rank 0', () {
      final status = NodeStatus.fromMqttMesh(
        'A0B1C2D3E4F5',
        '{"parent":null,"rank":0,"sleepy":false}',
      );
      expect(status.parentId, isNull);
      expect(status.meshRank, 0);
      expect(status.isSleepy, isFalse);
      expect(status.isOnline, isTrue);
    });

    test('malformed JSON throws FormatException', () {
      expect(() => NodeStatus.fromMqttMesh('node1', '{not json'), throwsFormatException);
    });
  });

  group('NodeStatus.copyWith', () {
    test('preserves existing mesh fields when no overrides given', () {
      final original = NodeStatus(
        nodeId: 'node1',
        isOnline: true,
        lastSeen: DateTime(2026, 1, 1),
        parentId: 'bridge',
        meshRank: 1,
        parentRssi: -55,
        isSleepy: false,
        zone: 'zone1',
        batteryMv: 4100,
      );
      final copy = original.copyWith(isOnline: false);
      expect(copy.parentId, 'bridge');
      expect(copy.meshRank, 1);
      expect(copy.parentRssi, -55);
      expect(copy.isSleepy, isFalse);
      expect(copy.zone, 'zone1');
      expect(copy.batteryMv, 4100);
      expect(copy.isOnline, isFalse);
    });

    test('overrides mesh fields when explicitly provided', () {
      final original = NodeStatus(
        nodeId: 'node1',
        isOnline: true,
        lastSeen: DateTime(2026, 1, 1),
      );
      final copy = original.copyWith(
        parentId: 'node1',
        meshRank: 2,
        parentRssi: -70,
        isSleepy: true,
        zone: 'zone2',
        batteryMv: 3300,
      );
      expect(copy.parentId, 'node1');
      expect(copy.meshRank, 2);
      expect(copy.parentRssi, -70);
      expect(copy.isSleepy, isTrue);
      expect(copy.zone, 'zone2');
      expect(copy.batteryMv, 3300);
    });
  });
}
