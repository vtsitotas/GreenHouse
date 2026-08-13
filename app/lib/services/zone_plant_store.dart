import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/plant_profile.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Which plant profile (built-in or custom) a zone is assigned, kept on this
/// phone only -- mirrors [DeviceNamesStore]'s reasoning exactly: which plant
/// is growing where is this grower's own interpretation of their own zones,
/// not fleet configuration, so there is nothing to reconcile between phones
/// by syncing it over MQTT.
class ZonePlantAssignment {
  /// One of [kBuiltInPlantProfiles]' ids, or [kCustomPlantProfileId].
  final String profileId;

  /// Only meaningful when [profileId] is [kCustomPlantProfileId].
  final Map<String, ClimateRange>? customRanges;

  const ZonePlantAssignment({required this.profileId, this.customRanges});

  bool get isCustom => profileId == kCustomPlantProfileId;

  String get displayName =>
      isCustom ? 'Custom' : (builtInPlantProfileById(profileId)?.name ?? profileId);

  /// The ranges that actually apply -- resolved from the built-in profile,
  /// or from the user's own custom values. Empty (not an error) when a
  /// custom assignment has no ranges typed in yet.
  Map<String, ClimateRange> get ranges => isCustom
      ? (customRanges ?? const <String, ClimateRange>{})
      : (builtInPlantProfileById(profileId)?.ranges ?? const <String, ClimateRange>{});

  Map<String, dynamic> toJson() => {
        'profileId': profileId,
        if (customRanges != null)
          'customRanges': customRanges!.map(
            (metric, r) => MapEntry(metric, {'min': r.min, 'max': r.max}),
          ),
      };

  static ZonePlantAssignment? fromJson(dynamic json) {
    if (json is! Map) return null;
    final profileId = json['profileId'];
    if (profileId is! String || profileId.isEmpty) return null;

    Map<String, ClimateRange>? customRanges;
    final raw = json['customRanges'];
    if (raw is Map) {
      customRanges = {};
      for (final entry in raw.entries) {
        final key = entry.key;
        final value = entry.value;
        if (key is String && value is Map) {
          customRanges[key] = ClimateRange(
            min: (value['min'] as num?)?.toDouble(),
            max: (value['max'] as num?)?.toDouble(),
          );
        }
      }
    }
    return ZonePlantAssignment(profileId: profileId, customRanges: customRanges);
  }
}

/// Single-JSON-blob-in-SharedPreferences store, same shape as
/// [DeviceNamesStore]/`NodePositionsStore` -- keyed by zone string instead of
/// device id, holding a small structured value instead of a bare name.
class ZonePlantStore {
  static const _prefsKey = 'zone_plant_profiles_v1';

  const ZonePlantStore();

  Future<Map<String, ZonePlantAssignment>> load() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_prefsKey);
    if (raw == null) return const <String, ZonePlantAssignment>{};
    try {
      final decoded = jsonDecode(raw) as Map<String, dynamic>;
      final result = <String, ZonePlantAssignment>{};
      for (final entry in decoded.entries) {
        final assignment = ZonePlantAssignment.fromJson(entry.value);
        // Skip anything unparseable rather than throwing -- a corrupt or
        // hand-edited blob must not make every zone lose its plant.
        if (assignment != null) result[entry.key] = assignment;
      }
      return result;
    } on FormatException {
      return const <String, ZonePlantAssignment>{};
    }
  }

  Future<void> setAssignment(String zone, ZonePlantAssignment assignment) async {
    final current = Map<String, ZonePlantAssignment>.from(await load());
    current[zone] = assignment;
    await _save(current);
  }

  Future<void> clear(String zone) async {
    final current = Map<String, ZonePlantAssignment>.from(await load());
    current.remove(zone);
    await _save(current);
  }

  Future<void> _save(Map<String, ZonePlantAssignment> assignments) async {
    final prefs = await SharedPreferences.getInstance();
    if (assignments.isEmpty) {
      await prefs.remove(_prefsKey);
      return;
    }
    final encoded = assignments.map((zone, a) => MapEntry(zone, a.toJson()));
    await prefs.setString(_prefsKey, jsonEncode(encoded));
  }
}

final zonePlantStoreProvider = Provider((_) => const ZonePlantStore());

/// Reactive wrapper, same shape as [DeviceNamesNotifier].
class ZonePlantNotifier extends AsyncNotifier<Map<String, ZonePlantAssignment>> {
  ZonePlantStore get _store => ref.read(zonePlantStoreProvider);

  @override
  Future<Map<String, ZonePlantAssignment>> build() => _store.load();

  Future<void> setAssignment(String zone, ZonePlantAssignment assignment) async {
    await _store.setAssignment(zone, assignment);
    final current = Map<String, ZonePlantAssignment>.from(
        state.valueOrNull ?? const <String, ZonePlantAssignment>{});
    current[zone] = assignment;
    state = AsyncData(current);
  }

  Future<void> clear(String zone) async {
    await _store.clear(zone);
    final current = Map<String, ZonePlantAssignment>.from(
        state.valueOrNull ?? const <String, ZonePlantAssignment>{});
    current.remove(zone);
    state = AsyncData(current);
  }
}

final zonePlantProvider =
    AsyncNotifierProvider<ZonePlantNotifier, Map<String, ZonePlantAssignment>>(
  ZonePlantNotifier.new,
);
