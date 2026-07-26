import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/screens/devices/mesh_map/mesh_layout.dart';

NodeStatus _node(
  String id, {
  int? rank,
  String? parentId,
  String? zone,
}) =>
    NodeStatus(
      nodeId: id,
      isOnline: true,
      lastSeen: DateTime(2026, 7, 26),
      parentId: parentId,
      meshRank: rank,
      zone: zone,
    );

void main() {
  group('MeshLayout.compute', () {
    test('bridge alone is centered at row 0', () {
      final nodes = {'bridge': _node('bridge', rank: 0)};
      final result = MeshLayout.compute(nodes, {});
      expect(result['bridge'], const Offset(0.5, 0.12));
    });

    test('3-node chain lands in rows by rank with expected y values', () {
      final nodes = {
        'bridge': _node('bridge', rank: 0),
        'node1': _node('node1', rank: 1, parentId: 'bridge'),
        'node2': _node('node2', rank: 2, parentId: 'node1'),
      };
      final result = MeshLayout.compute(nodes, {});
      expect(result['bridge']!.dy, closeTo(0.12, 1e-9));
      expect(result['node1']!.dy, closeTo(0.30, 1e-9));
      expect(result['node2']!.dy, closeTo(0.48, 1e-9));
      // Single-member rows are centered.
      expect(result['bridge']!.dx, closeTo(0.5, 1e-9));
      expect(result['node1']!.dx, closeTo(0.5, 1e-9));
      expect(result['node2']!.dx, closeTo(0.5, 1e-9));
    });

    test('two rank-1 siblings keep stable order by zone key across repeated calls', () {
      final nodesA = {
        'nodeA': _node('nodeA', rank: 1, zone: 'zoneB'),
        'nodeB': _node('nodeB', rank: 1, zone: 'zoneA'),
      };
      // Same nodes, different insertion order in the source map.
      final nodesB = {
        'nodeB': _node('nodeB', rank: 1, zone: 'zoneA'),
        'nodeA': _node('nodeA', rank: 1, zone: 'zoneB'),
      };

      final resultA = MeshLayout.compute(nodesA, {});
      final resultB = MeshLayout.compute(nodesB, {});

      // zoneA sorts before zoneB, so nodeB (zoneA) gets the left slot.
      expect(resultA['nodeB']!.dx, closeTo(1 / 3, 1e-9));
      expect(resultA['nodeA']!.dx, closeTo(2 / 3, 1e-9));
      // Repeating compute with different map insertion order is identical.
      expect(resultA['nodeA'], resultB['nodeA']);
      expect(resultA['nodeB'], resultB['nodeB']);

      // Calling compute again with the exact same input is also stable.
      final resultA2 = MeshLayout.compute(nodesA, {});
      expect(resultA2['nodeA'], resultA['nodeA']);
      expect(resultA2['nodeB'], resultA['nodeB']);
    });

    test('pinned node keeps its exact offset; remaining sibling re-spreads alone', () {
      final nodes = {
        'nodeA': _node('nodeA', rank: 1),
        'nodeB': _node('nodeB', rank: 1),
      };
      const pinnedOffset = Offset(0.8, 0.33);
      final result = MeshLayout.compute(nodes, {'nodeA': pinnedOffset});

      expect(result['nodeA'], pinnedOffset);
      // nodeB is now the only row member (nodeA excluded from spreading),
      // so it is centered as if it were alone in the row.
      expect(result['nodeB']!.dx, closeTo(0.5, 1e-9));
      expect(result['nodeB']!.dy, closeTo(0.30, 1e-9));
    });

    test('unknown parentId does not throw', () {
      final nodes = {
        'node1': _node('node1', rank: 1, parentId: 'ghost-parent-not-in-map'),
      };
      expect(() => MeshLayout.compute(nodes, {}), returnsNormally);
      final result = MeshLayout.compute(nodes, {});
      expect(result['node1'], isNotNull);
    });

    test('null meshRank lands in the bottom unplaced row', () {
      final nodes = {
        'bridge': _node('bridge', rank: 0),
        'node1': _node('node1', rank: 1),
        'mystery': _node('mystery'), // meshRank: null
      };
      final result = MeshLayout.compute(nodes, {});
      expect(result['mystery']!.dy, closeTo(0.92, 1e-9));
      // The unplaced row stays below every known-rank row.
      expect(result['mystery']!.dy, greaterThan(result['bridge']!.dy));
      expect(result['mystery']!.dy, greaterThan(result['node1']!.dy));
    });

    test('empty nodes map returns empty layout', () {
      expect(MeshLayout.compute({}, {}), isEmpty);
    });
  });
}
