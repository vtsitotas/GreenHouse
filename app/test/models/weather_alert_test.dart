import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/weather_alert.dart';

void main() {
  test('title for a motion alert', () {
    final alert = WeatherAlert.fromJson({'type': 'motion', 'message': 'Motion detected', 'severity': 'info'});
    expect(alert.title, contains('Motion'));
  });
}
