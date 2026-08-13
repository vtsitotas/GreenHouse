import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/plant_profile.dart';
import 'package:greenhouse_app/services/zone_plant_store.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  group('ZonePlantStore', () {
    test('set persists a built-in assignment; load returns it; clear removes it', () async {
      SharedPreferences.setMockInitialValues({});
      const store = ZonePlantStore();

      expect(await store.load(), isEmpty);

      await store.setAssignment('zone1', const ZonePlantAssignment(profileId: 'tomato'));
      final loaded = await store.load();
      expect(loaded['zone1']?.profileId, 'tomato');
      expect(loaded['zone1']?.isCustom, isFalse);
      expect(loaded['zone1']?.displayName, 'Tomato');

      await store.clear('zone1');
      expect(await store.load(), isEmpty);
    });

    test('a custom assignment round-trips its ranges through JSON', () async {
      SharedPreferences.setMockInitialValues({});
      const store = ZonePlantStore();

      await store.setAssignment(
        'zone2',
        const ZonePlantAssignment(profileId: kCustomPlantProfileId, customRanges: {
          'soil_moisture': ClimateRange(min: 35, max: 55),
          'air_humidity': ClimateRange(max: 80), // no floor
        }),
      );

      final loaded = await store.load();
      final assignment = loaded['zone2']!;
      expect(assignment.isCustom, isTrue);
      expect(assignment.ranges['soil_moisture']?.min, 35);
      expect(assignment.ranges['soil_moisture']?.max, 55);
      expect(assignment.ranges['air_humidity']?.min, isNull);
      expect(assignment.ranges['air_humidity']?.max, 80);
    });

    test('multiple zones are stored side by side', () async {
      SharedPreferences.setMockInitialValues({});
      const store = ZonePlantStore();

      await store.setAssignment('zone1', const ZonePlantAssignment(profileId: 'tomato'));
      await store.setAssignment('zone2', const ZonePlantAssignment(profileId: 'lettuce'));

      final loaded = await store.load();
      expect(loaded.keys.toSet(), {'zone1', 'zone2'});
      expect(loaded['zone1']?.profileId, 'tomato');
      expect(loaded['zone2']?.profileId, 'lettuce');
    });

    test('a corrupt blob degrades to "no assignments" instead of throwing', () async {
      SharedPreferences.setMockInitialValues({'zone_plant_profiles_v1': '{not valid json'});
      const store = ZonePlantStore();

      expect(await store.load(), isEmpty);
    });

    test('an entry with no profileId is skipped, not fatal', () async {
      SharedPreferences.setMockInitialValues({
        'zone_plant_profiles_v1': jsonEncode({
          'zone1': {'profileId': 'tomato'},
          'zone2': {'somethingElse': 1},
        }),
      });
      const store = ZonePlantStore();

      final loaded = await store.load();
      expect(loaded.keys.toList(), ['zone1']);
    });

    test('clearing the last assignment removes the key rather than storing "{}"', () async {
      SharedPreferences.setMockInitialValues({});
      const store = ZonePlantStore();
      await store.setAssignment('zone1', const ZonePlantAssignment(profileId: 'tomato'));

      await store.clear('zone1');

      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString('zone_plant_profiles_v1'), isNull);
    });
  });

  group('zonePlantProvider', () {
    test('exposes a stored assignment and updates optimistically on set', () async {
      SharedPreferences.setMockInitialValues({});
      final container = ProviderContainer();
      addTearDown(container.dispose);

      expect(await container.read(zonePlantProvider.future), isEmpty);

      await container
          .read(zonePlantProvider.notifier)
          .setAssignment('zone1', const ZonePlantAssignment(profileId: 'tomato'));

      expect(container.read(zonePlantProvider).valueOrNull?['zone1']?.profileId, 'tomato');
    });

    test('an assignment set through the notifier survives a fresh read of the store', () async {
      SharedPreferences.setMockInitialValues({});
      final container = ProviderContainer();
      addTearDown(container.dispose);
      await container.read(zonePlantProvider.future);

      await container
          .read(zonePlantProvider.notifier)
          .setAssignment('zone1', const ZonePlantAssignment(profileId: 'basil'));

      // Proves the notifier actually wrote through, not just updated
      // in-memory state -- the bug that would look fine until app restart.
      expect((await const ZonePlantStore().load())['zone1']?.profileId, 'basil');
    });

    test('clearing through the notifier removes it from state', () async {
      SharedPreferences.setMockInitialValues({});
      final container = ProviderContainer();
      addTearDown(container.dispose);
      await container.read(zonePlantProvider.future);
      await container
          .read(zonePlantProvider.notifier)
          .setAssignment('zone1', const ZonePlantAssignment(profileId: 'basil'));

      await container.read(zonePlantProvider.notifier).clear('zone1');

      expect(container.read(zonePlantProvider).valueOrNull, isEmpty);
    });
  });
}
