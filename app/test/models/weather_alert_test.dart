import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/weather_alert.dart';

void main() {
  test('title for a frost alert', () {
    final alert = WeatherAlert.fromJson({'type': 'frost', 'message': '-1C tonight', 'severity': 'warning'});
    expect(alert.title, contains('Frost'));
    expect(alert.isWarning, isTrue);
  });

  test('title falls back to a generic label for an unknown type', () {
    final alert = WeatherAlert.fromJson({'type': 'something-new', 'message': 'hi', 'severity': 'info'});
    expect(alert.title, contains('Greenhouse'));
    expect(alert.isWarning, isFalse);
  });

  test('hazard alerts get their own titles', () {
    const expected = {
      'hazard-fire': 'fire',
      'hazard-flood': 'flood',
      'hazard-frost': 'frost',
      'hazard-heat': 'heat',
      'hazard-drought': 'dry',
    };
    expected.forEach((type, word) {
      final alert = WeatherAlert.fromJson({'type': type, 'message': 'm', 'severity': 'warning'});
      expect(alert.title.toLowerCase(), contains(word), reason: type);
      expect(alert.isHazard, isTrue, reason: type);
    });
    expect(WeatherAlert.fromJson({'type': 'rain-close'}).isHazard, isFalse);
  });

  test('critical alerts are warnings and critical', () {
    final alert = WeatherAlert.fromJson({'type': 'hazard-fire', 'message': 'm', 'severity': 'critical'});
    expect(alert.isWarning, isTrue);
    expect(alert.isCritical, isTrue);
    final warn = WeatherAlert.fromJson({'type': 'frost', 'severity': 'warning'});
    expect(warn.isCritical, isFalse);
  });

  test('a remote-site alert keeps its site and falls back to the rule id as message', () {
    final alert = WeatherAlert.fromJson(
        {'severity': 'warning', 'rule_id': 'dry', 'site': 'north', 'ts': 5});
    expect(alert.site, 'north');
    expect(alert.message, contains('dry'));
    expect(WeatherAlert.fromJson({'type': 'frost'}).site, isNull);
  });
}
