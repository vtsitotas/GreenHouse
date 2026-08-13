import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/plant_profile.dart';

void main() {
  group('kBuiltInPlantProfiles', () {
    test('every profile has a unique, non-empty id', () {
      final ids = kBuiltInPlantProfiles.map((p) => p.id).toList();
      expect(ids.every((id) => id.isNotEmpty), isTrue);
      expect(ids.toSet().length, ids.length);
    });

    test('none of the built-in ids collide with the reserved custom id', () {
      expect(kBuiltInPlantProfiles.any((p) => p.id == kCustomPlantProfileId), isFalse);
    });

    test('ranges use the rule-engine metric vocabulary, not the readings-map one', () {
      // These three are the only metrics WeatherRule/rule_form_dialog know
      // about for zone-scoped rules -- a profile with any other key would
      // silently never plug into a suggested rule.
      const knownMetrics = {'soil_moisture', 'air_temperature', 'air_humidity'};
      for (final profile in kBuiltInPlantProfiles) {
        for (final metric in profile.ranges.keys) {
          expect(knownMetrics.contains(metric), isTrue,
              reason: '${profile.id} has an unrecognised metric key "$metric"');
        }
      }
    });

    test('every range has at least one bound', () {
      for (final profile in kBuiltInPlantProfiles) {
        for (final entry in profile.ranges.entries) {
          expect(entry.value.min != null || entry.value.max != null, isTrue,
              reason: '${profile.id}.${entry.key} has neither a min nor a max');
        }
      }
    });
  });

  group('builtInPlantProfileById', () {
    test('finds a known profile by id', () {
      expect(builtInPlantProfileById('tomato')?.name, 'Tomato');
    });

    test('returns null for an unknown id', () {
      expect(builtInPlantProfileById('dragonfruit'), isNull);
      expect(builtInPlantProfileById(kCustomPlantProfileId), isNull);
    });
  });
}
