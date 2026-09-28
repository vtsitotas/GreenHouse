import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/notification_settings.dart';

void main() {
  test('fromJson defaults both switches to true when absent', () {
    final settings = NotificationSettings.fromJson({});
    expect(settings.frostForecast, isTrue);
    expect(settings.dailySummary, isTrue);
    expect(settings.hazardAlerts, isTrue);
  });

  test('toJson round-trips both switches', () {
    const settings = NotificationSettings(frostForecast: true, dailySummary: false);
    expect(settings.toJson(),
        {'frost_forecast': true, 'daily_summary': false, 'hazard_alerts': true});
  });

  test('copyWith updates one switch independently', () {
    const settings = NotificationSettings(frostForecast: true, dailySummary: true);
    final updated = settings.copyWith(dailySummary: false);
    expect(updated.dailySummary, isFalse);
    expect(updated.frostForecast, isTrue);
  });

  test('hazard_alerts round-trips and copyWith changes only it', () {
    final settings = NotificationSettings.fromJson(
        {'frost_forecast': true, 'daily_summary': true, 'hazard_alerts': false});
    expect(settings.hazardAlerts, isFalse);
    expect(settings.toJson()['hazard_alerts'], isFalse);
    final on = settings.copyWith(hazardAlerts: true);
    expect(on.hazardAlerts, isTrue);
    expect(on.frostForecast, isTrue);
  });
}
