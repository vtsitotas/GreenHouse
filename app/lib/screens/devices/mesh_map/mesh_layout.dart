import 'dart:ui' show Offset;

import 'package:greenhouse_app/models/node_status.dart';

/// Pure-Dart auto-layout for the mesh map. No Flutter widget imports —
/// keeps this file cheaply unit-testable (see `mesh_layout_test.dart`).
///
/// Rules (spec §Layout engine):
/// - Nodes partition into rank rows: row N == `meshRank` N. Nodes with a
///   null `meshRank` (no `/mesh` data yet) go into a bottom "unplaced" row,
///   always below every known-rank row.
/// - Row y = `0.12 + row * 0.18`, clamped to <= 0.92; the unplaced row is
///   pinned at y = 0.92 regardless of how many known ranks exist.
/// - Within a row, nodes spread evenly on x ordered by a stable key
///   (`zone` if set, else `nodeId`, with `nodeId` as the tiebreaker so
///   ordering never flickers when unrelated fields change).
/// - Pinned nodes (present in the `pinned` map) keep their exact stored
///   offset and are excluded from row spreading entirely — including from
///   the row's member count, so the remaining unpinned siblings spread as
///   if the pinned node were never there.
/// - An unknown/dangling `parentId` never affects layout placement (rows
///   are keyed purely by `meshRank`), so it can never throw here.
class MeshLayout {
  static const double _rowStartY = 0.12;
  static const double _rowStepY = 0.18;
  static const double _maxRowY = 0.92;
  static const double _unplacedY = 0.92;

  static Map<String, Offset> compute(
    Map<String, NodeStatus> nodes,
    Map<String, Offset> pinned,
  ) {
    final result = <String, Offset>{};

    final rowsByRank = <int, List<String>>{};
    final unplaced = <String>[];

    for (final entry in nodes.entries) {
      final id = entry.key;
      final pinnedOffset = pinned[id];
      if (pinnedOffset != null) {
        result[id] = pinnedOffset;
        continue;
      }
      final rank = entry.value.meshRank;
      if (rank == null) {
        unplaced.add(id);
      } else {
        rowsByRank.putIfAbsent(rank, () => <String>[]).add(id);
      }
    }

    final ranks = rowsByRank.keys.toList()..sort();
    for (final rank in ranks) {
      final row = rowsByRank[rank]!..sort((a, b) => _compareIds(a, b, nodes));
      final rawY = _rowStartY + rank * _rowStepY;
      final y = rawY > _maxRowY ? _maxRowY : rawY;
      _spreadRow(row, y, result);
    }

    if (unplaced.isNotEmpty) {
      unplaced.sort((a, b) => _compareIds(a, b, nodes));
      _spreadRow(unplaced, _unplacedY, result);
    }

    return result;
  }

  static int _compareIds(String a, String b, Map<String, NodeStatus> nodes) {
    final keyA = nodes[a]?.zone ?? a;
    final keyB = nodes[b]?.zone ?? b;
    final cmp = keyA.compareTo(keyB);
    if (cmp != 0) return cmp;
    return a.compareTo(b);
  }

  static void _spreadRow(List<String> row, double y, Map<String, Offset> result) {
    final n = row.length;
    for (var i = 0; i < n; i++) {
      final x = (i + 1) / (n + 1);
      result[row[i]] = Offset(x, y);
    }
  }
}
