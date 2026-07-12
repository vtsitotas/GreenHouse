import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/notification_settings.dart';

void main() {
  test('fromJson defaults motionAlert to true when absent', () {
    final settings = NotificationSettings.fromJson({'frost_forecast': true, 'daily_summary': true});
    expect(settings.motionAlert, isTrue);
  });

  test('toJson includes motion_alert', () {
    const settings = NotificationSettings(frostForecast: true, dailySummary: true, motionAlert: false);
    expect(settings.toJson(), {'frost_forecast': true, 'daily_summary': true, 'motion_alert': false});
  });

  test('copyWith updates motionAlert independently', () {
    const settings = NotificationSettings(frostForecast: true, dailySummary: true, motionAlert: true);
    final updated = settings.copyWith(motionAlert: false);
    expect(updated.motionAlert, isFalse);
    expect(updated.frostForecast, isTrue);
  });
}
