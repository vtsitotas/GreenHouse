import 'package:flutter/foundation.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';

/// Wraps flutter_local_notifications for greenhouse weather alerts.
class NotificationService {
  NotificationService._();
  static final NotificationService instance = NotificationService._();

  final _plugin = FlutterLocalNotificationsPlugin();
  bool _initialized = false;

  static const _channelId   = 'greenhouse_weather';
  static const _channelName = 'Greenhouse Weather';
  static const _channelDesc = 'Weather alerts and daily forecasts for your greenhouse';

  Future<void> init() async {
    if (_initialized) return;
    const android = AndroidInitializationSettings('@mipmap/ic_launcher');
    const ios = DarwinInitializationSettings(
      requestAlertPermission: true,
      requestBadgePermission: true,
      requestSoundPermission: true,
    );
    const settings = InitializationSettings(android: android, iOS: ios);
    await _plugin.initialize(settings);
    _initialized = true;
    debugPrint('[Notifications] initialized');
  }

  Future<void> requestPermission() async {
    final android = _plugin.resolvePlatformSpecificImplementation<
        AndroidFlutterLocalNotificationsPlugin>();
    await android?.requestNotificationsPermission();
  }

  /// Show a notification (weather alerts, daily summaries, and — since FCM
  /// messages are the sole remaining notification path — anything the Pi
  /// pushes). High importance so the Android channel (created lazily on
  /// first use, and fixed at that importance thereafter) shows a heads-up
  /// banner for frost/rule alerts, matching the old showAlert() behavior.
  Future<void> showInfo(String title, String body) async {
    if (!_initialized) return;
    const androidDetails = AndroidNotificationDetails(
      _channelId,
      _channelName,
      channelDescription: _channelDesc,
      importance: Importance.high,
      priority: Priority.high,
      icon: '@mipmap/ic_launcher',
    );
    const details = NotificationDetails(
      android: androidDetails,
      iOS: DarwinNotificationDetails(),
    );
    await _plugin.show(DateTime.now().millisecondsSinceEpoch % 10000, title, body, details);
  }
}
