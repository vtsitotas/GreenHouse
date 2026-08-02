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
}
