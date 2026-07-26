import 'dart:convert';
import 'dart:ui' show Offset;

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Persists drag-to-pin node positions locally (spec §Pinned positions).
///
/// `SharedPreferences` key `mesh_map_positions_v1` holds a single JSON blob
/// `{"<nodeId>": {"x":0.31,"y":0.62}, ...}` — normalized (0-1) coordinates,
/// so a canvas-size change never invalidates a pin. No Flutter widget
/// imports here (just `dart:ui`'s `Offset`), keeping the persistence logic
/// cheaply unit-testable per the plan's Global Constraints.
class NodePositionsStore {
  static const _prefsKey = 'mesh_map_positions_v1';

  const NodePositionsStore();

  Future<Map<String, Offset>> load() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_prefsKey);
    if (raw == null) return const <String, Offset>{};
    final decoded = jsonDecode(raw) as Map<String, dynamic>;
    final result = <String, Offset>{};
    for (final entry in decoded.entries) {
      final value = entry.value as Map<String, dynamic>;
      final x = (value['x'] as num?)?.toDouble();
      final y = (value['y'] as num?)?.toDouble();
      if (x == null || y == null) continue;
      result[entry.key] = Offset(x, y);
    }
    return result;
  }

  Future<void> pin(String nodeId, Offset normalized) async {
    final current = await load();
    final updated = {...current, nodeId: normalized};
    await _save(updated);
  }

  Future<void> unpin(String nodeId) async {
    final current = await load();
    if (!current.containsKey(nodeId)) return;
    final updated = Map<String, Offset>.from(current)..remove(nodeId);
    await _save(updated);
  }

  Future<void> _save(Map<String, Offset> positions) async {
    final prefs = await SharedPreferences.getInstance();
    if (positions.isEmpty) {
      await prefs.remove(_prefsKey);
      return;
    }
    final encoded = {
      for (final entry in positions.entries)
        entry.key: {'x': entry.value.dx, 'y': entry.value.dy},
    };
    await prefs.setString(_prefsKey, jsonEncode(encoded));
  }
}

final nodePositionsStoreProvider = Provider((_) => const NodePositionsStore());

/// Reactive wrapper around [NodePositionsStore] so the screen can `ref.watch`
/// the pinned-position map declaratively (spec: "Provider-wrapped so the
/// screen stays declarative"). Plain `AsyncNotifier` — riverpod 2.x classic
/// API, no codegen — matching the app's existing provider style (services
/// exposed via a `Provider`, live data via `StreamProvider`/`FutureProvider`
/// wrappers; see `providers/connection_provider.dart`).
class NodePositionsNotifier extends AsyncNotifier<Map<String, Offset>> {
  NodePositionsStore get _store => ref.read(nodePositionsStoreProvider);

  @override
  Future<Map<String, Offset>> build() => _store.load();

  Future<void> pin(String nodeId, Offset normalized) async {
    await _store.pin(nodeId, normalized);
    final current =
        Map<String, Offset>.from(state.valueOrNull ?? const <String, Offset>{});
    current[nodeId] = normalized;
    state = AsyncData(current);
  }

  Future<void> unpin(String nodeId) async {
    await _store.unpin(nodeId);
    final current =
        Map<String, Offset>.from(state.valueOrNull ?? const <String, Offset>{});
    current.remove(nodeId);
    state = AsyncData(current);
  }
}

final pinnedPositionsProvider =
    AsyncNotifierProvider<NodePositionsNotifier, Map<String, Offset>>(
  NodePositionsNotifier.new,
);
