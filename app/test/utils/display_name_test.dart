import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/utils/display_name.dart';

void main() {
  group('zoneLabel', () {
    test('zoneN becomes "Zone N"', () {
      expect(zoneLabel('zone1'), 'Zone 1');
      expect(zoneLabel('zone12'), 'Zone 12');
    });

    test('a hand-named zone is capitalised, not mangled', () {
      expect(zoneLabel('greenhouse-a'), 'Greenhouse-a');
    });

    test('the bare word "zone" has no number to show, so it is left alone', () {
      // Guards the substring(4) call: 'zone' alone would otherwise produce
      // the nonsensical 'Zone '.
      expect(zoneLabel('zone'), 'Zone');
    });

    test('empty string does not throw', () {
      expect(zoneLabel(''), '');
    });
  });

  group('shortId', () {
    test('keeps the last 4 characters of a MAC -- the part on the sticker', () {
      expect(shortId('206EF16CA1B0'), 'A1B0');
    });

    test('an already-short id is returned whole', () {
      expect(shortId('n1'), 'n1');
      expect(shortId('abcd'), 'abcd');
    });
  });

  group('displayNameFor', () {
    const mac = '206EF16CA1B0';

    test('a user-set name wins over everything', () {
      expect(
        displayNameFor(mac, {mac: 'Tomato bed'}, zone: 'zone1'),
        'Tomato bed',
      );
    });

    test('falls back to the zone when there is no user-set name', () {
      expect(displayNameFor(mac, const {}, zone: 'zone1'), 'Zone 1');
    });

    test('falls back to the short id when there is neither', () {
      expect(displayNameFor(mac, const {}), 'A1B0');
    });

    test('a whitespace-only stored name is treated as no name at all', () {
      // Otherwise a user who typed spaces would get an invisible title.
      expect(displayNameFor(mac, {mac: '   '}, zone: 'zone1'), 'Zone 1');
    });

    test('a stored name is trimmed for display', () {
      expect(displayNameFor(mac, {mac: '  Tomato bed  '}), 'Tomato bed');
    });

    test('a whitespace-only zone falls through to the short id', () {
      expect(displayNameFor(mac, const {}, zone: '  '), 'A1B0');
    });

    test('works for an actuator id, not just a MAC', () {
      expect(displayNameFor('pump1', {'pump1': 'Water pump'}), 'Water pump');
      expect(displayNameFor('pump1', const {}), 'ump1');
    });
  });

  group('hasFriendlyName', () {
    const mac = '206EF16CA1B0';

    test('true when named or zoned, false when only the raw id is known', () {
      expect(hasFriendlyName(mac, {mac: 'Tomato bed'}), isTrue);
      expect(hasFriendlyName(mac, const {}, zone: 'zone1'), isTrue);
      expect(hasFriendlyName(mac, const {}), isFalse);
    });

    test('agrees with displayNameFor about blank names and zones', () {
      expect(hasFriendlyName(mac, {mac: '  '}), isFalse);
      expect(hasFriendlyName(mac, const {}, zone: '  '), isFalse);
    });
  });
}
