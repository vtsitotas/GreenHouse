import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/services/device_names_store.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  group('DeviceNamesStore', () {
    test('set persists a name; load returns it; clear removes it', () async {
      SharedPreferences.setMockInitialValues({});
      const store = DeviceNamesStore();

      expect(await store.load(), isEmpty);

      await store.setName('206EF16CA1B0', 'Tomato bed');
      expect(await store.load(), {'206EF16CA1B0': 'Tomato bed'});

      await store.clearName('206EF16CA1B0');
      expect(await store.load(), isEmpty);
    });

    test('holds node MACs and actuator ids side by side', () async {
      SharedPreferences.setMockInitialValues({});
      const store = DeviceNamesStore();

      await store.setName('206EF16CA1B0', 'Tomato bed');
      await store.setName('pump1', 'Water pump');

      expect(await store.load(),
          {'206EF16CA1B0': 'Tomato bed', 'pump1': 'Water pump'});
    });

    test('names are trimmed on the way in', () async {
      SharedPreferences.setMockInitialValues({});
      const store = DeviceNamesStore();

      await store.setName('n1', '  Back corner  ');

      expect(await store.load(), {'n1': 'Back corner'});
    });

    test('saving a blank name clears it, restoring the automatic label',
        () async {
      SharedPreferences.setMockInitialValues({});
      const store = DeviceNamesStore();
      await store.setName('n1', 'Back corner');

      await store.setName('n1', '   ');

      expect(await store.load(), isEmpty);
    });

    test('a corrupt blob degrades to "no custom names" instead of throwing',
        () async {
      // A hand-edited or truncated value must not leave every device nameless
      // *and* crash the screen reading it.
      SharedPreferences.setMockInitialValues(
          {'device_names_v1': '{not valid json'});
      const store = DeviceNamesStore();

      expect(await store.load(), isEmpty);
    });

    test('non-string and blank entries in the blob are skipped, not fatal',
        () async {
      SharedPreferences.setMockInitialValues({
        'device_names_v1': jsonEncode({
          'good': 'Tomato bed',
          'numeric': 42,
          'blank': '   ',
          'nullish': null,
        }),
      });
      const store = DeviceNamesStore();

      expect(await store.load(), {'good': 'Tomato bed'});
    });

    test('clearing the last name removes the key rather than storing "{}"',
        () async {
      SharedPreferences.setMockInitialValues({});
      const store = DeviceNamesStore();
      await store.setName('n1', 'Back corner');

      await store.clearName('n1');

      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString('device_names_v1'), isNull);
    });
  });

  group('deviceNamesProvider', () {
    test('exposes stored names and updates optimistically on set', () async {
      SharedPreferences.setMockInitialValues({});
      final container = ProviderContainer();
      addTearDown(container.dispose);

      expect(await container.read(deviceNamesProvider.future), isEmpty);

      await container
          .read(deviceNamesProvider.notifier)
          .setName('206EF16CA1B0', 'Tomato bed');

      expect(container.read(deviceNamesProvider).valueOrNull,
          {'206EF16CA1B0': 'Tomato bed'});
    });

    test('a name set through the notifier survives a fresh read of the store',
        () async {
      SharedPreferences.setMockInitialValues({});
      final container = ProviderContainer();
      addTearDown(container.dispose);
      await container.read(deviceNamesProvider.future);

      await container.read(deviceNamesProvider.notifier).setName('n1', 'Shed');

      // Proves the notifier actually wrote through, rather than only updating
      // its in-memory state -- the bug that would look fine until app restart.
      expect(await const DeviceNamesStore().load(), {'n1': 'Shed'});
    });

    test('clearing through the notifier removes it from state', () async {
      SharedPreferences.setMockInitialValues({});
      final container = ProviderContainer();
      addTearDown(container.dispose);
      await container.read(deviceNamesProvider.future);
      await container.read(deviceNamesProvider.notifier).setName('n1', 'Shed');

      await container.read(deviceNamesProvider.notifier).clearName('n1');

      expect(container.read(deviceNamesProvider).valueOrNull, isEmpty);
    });
  });
}
