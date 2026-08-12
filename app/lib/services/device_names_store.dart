import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// User-chosen names for devices, kept on this phone only.
///
/// A greenhouse owner identifies a sensor by where it physically sits ("tomato
/// bed", "by the door"), not by the 12-hex MAC the mesh addresses it with, nor
/// by an actuator id like `pump1`. Neither of those is renameable at source:
/// MACs are burned into the hardware and the actuator ids come from the
/// automation rules, so the mapping has to live somewhere the user controls.
///
/// Deliberately local, exactly like [NodePositionsStore] which this mirrors
/// (same single-JSON-blob-in-SharedPreferences shape, same provider style):
/// naming is a personal labelling preference, not fleet configuration, and
/// syncing it would mean pushing it through MQTT and reconciling conflicts
/// between phones for no real gain.
///
/// Keyed by raw device id — a node MAC or an actuator id — so one store serves
/// both. Ids are opaque here; nothing in this file knows or cares which kind
/// it is holding.
class DeviceNamesStore {
  static const _prefsKey = 'device_names_v1';

  const DeviceNamesStore();

  Future<Map<String, String>> load() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_prefsKey);
    if (raw == null) return const <String, String>{};
    try {
      final decoded = jsonDecode(raw) as Map<String, dynamic>;
      final result = <String, String>{};
      for (final entry in decoded.entries) {
        final value = entry.value;
        // Skip anything that isn't a usable name rather than throwing: a
        // corrupt or hand-edited blob must not make every device nameless.
        if (value is String && value.trim().isNotEmpty) {
          result[entry.key] = value;
        }
      }
      return result;
    } on FormatException {
      // Unparseable blob — fall back to "no custom names" instead of breaking
      // every screen that reads this.
      return const <String, String>{};
    }
  }

  /// Sets a name, or clears it when [name] is blank — trimming means a user
  /// who wipes the field and saves gets the automatic label back rather than
  /// an invisible empty title.
  Future<void> setName(String deviceId, String name) async {
    final trimmed = name.trim();
    final current = Map<String, String>.from(await load());
    if (trimmed.isEmpty) {
      current.remove(deviceId);
    } else {
      current[deviceId] = trimmed;
    }
    await _save(current);
  }

  Future<void> clearName(String deviceId) => setName(deviceId, '');

  Future<void> _save(Map<String, String> names) async {
    final prefs = await SharedPreferences.getInstance();
    if (names.isEmpty) {
      await prefs.remove(_prefsKey);
      return;
    }
    await prefs.setString(_prefsKey, jsonEncode(names));
  }
}

final deviceNamesStoreProvider = Provider((_) => const DeviceNamesStore());

/// Reactive wrapper so screens can `ref.watch` the name map declaratively,
/// matching [NodePositionsNotifier]'s shape (riverpod 2.x classic API, no
/// codegen).
class DeviceNamesNotifier extends AsyncNotifier<Map<String, String>> {
  DeviceNamesStore get _store => ref.read(deviceNamesStoreProvider);

  @override
  Future<Map<String, String>> build() => _store.load();

  Future<void> setName(String deviceId, String name) async {
    await _store.setName(deviceId, name);
    final current =
        Map<String, String>.from(state.valueOrNull ?? const <String, String>{});
    final trimmed = name.trim();
    if (trimmed.isEmpty) {
      current.remove(deviceId);
    } else {
      current[deviceId] = trimmed;
    }
    state = AsyncData(current);
  }

  Future<void> clearName(String deviceId) => setName(deviceId, '');
}

final deviceNamesProvider =
    AsyncNotifierProvider<DeviceNamesNotifier, Map<String, String>>(
  DeviceNamesNotifier.new,
);
