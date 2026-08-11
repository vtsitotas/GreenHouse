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
      // `ts` is the sighting time stamped by serial_bridge.py, NOT arrival
      // time -- that's what makes a retained replay still datable. This
      // payload's ts long predates "now", so a parser that ignored it (as
      // this one used to) would be caught here.
      expect(status.lastSeen,
          DateTime.fromMillisecondsSinceEpoch(1753500000 * 1000));
    });

    test('a payload with no ts falls back to arrival time when live', () {
      final status = NodeStatus.fromMqttMesh('node2', '{"rank":1}');
      expect(status.lastSeen!.difference(DateTime.now()).inSeconds.abs(), lessThan(5));
    });

    test('a payload with no ts has no lastSeen at all when retained', () {
      // Pre-2026-08-11 Pi code published no ts. A retained replay of one
      // carries no datable evidence whatsoever, so "unknown" is the only
      // honest answer -- the alternative is every node reading as "seen
      // just now" every time the app reconnects.
      final status = NodeStatus.fromMqttMesh('node2', '{"rank":1}', retain: true);
      expect(status.lastSeen, isNull);
    });

    test('ts is trusted even on a retained replay -- that is its whole purpose', () {
      final status =
          NodeStatus.fromMqttMesh('node2', '{"rank":1,"ts":1753500000}', retain: true);
      expect(status.lastSeen,
          DateTime.fromMillisecondsSinceEpoch(1753500000 * 1000));
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

  group('NodeStatus.withLastSeen', () {
    test('can explicitly clear lastSeen to null, unlike copyWith', () {
      final original = NodeStatus(
        nodeId: 'node1',
        isOnline: true,
        lastSeen: DateTime(2026, 1, 1),
        zone: 'zone1',
      );
      final cleared = original.withLastSeen(null);
      expect(cleared.lastSeen, isNull);
      // Everything else survives untouched.
      expect(cleared.nodeId, 'node1');
      expect(cleared.isOnline, isTrue);
      expect(cleared.zone, 'zone1');
    });

    test('can also set a real value, same as copyWith would', () {
      const original = NodeStatus(nodeId: 'node1', isOnline: true, lastSeen: null);
      final seen = DateTime(2026, 6, 1, 12, 0);
      expect(original.withLastSeen(seen).lastSeen, seen);
    });
  });

  group('retain flag on the MQTT factories', () {
    test('defaults to false when not specified', () {
      expect(NodeStatus.fromMqttStatus('n1', 'online').retain, isFalse);
      expect(NodeStatus.fromMqttBattery('n1', '50.0').retain, isFalse);
      expect(NodeStatus.fromMqttMesh('n1', '{"rank":1}').retain, isFalse);
    });

    test('is carried through from each factory when passed', () {
      expect(NodeStatus.fromMqttStatus('n1', 'online', retain: true).retain, isTrue);
      expect(NodeStatus.fromMqttBattery('n1', '50.0', retain: true).retain, isTrue);
      expect(NodeStatus.fromMqttMesh('n1', '{"rank":1}', retain: true).retain, isTrue);
    });
  });
}
