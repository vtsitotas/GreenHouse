import 'dart:math';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:greenhouse_app/repository/greenhouse_repository.dart';

const _deviceIdKey = 'greenhouse_device_id';

/// Registers this install's FCM token with the Pi (retained, per-device
/// topic) so weather.py — and later, camera motion alerts — can push to it
/// even when the app is fully closed.
class FcmTokenService {
  final GreenhouseRepository _repository;
  final Future<String?> Function(String key) _readSecure;
  final Future<void> Function(String key, String value) _writeSecure;
  final Future<String?> Function() _getToken;
  final Stream<String> Function() _onTokenRefresh;
  final Stream<RemoteMessage> Function() _onMessage;

  FcmTokenService(
    this._repository, {
    Future<String?> Function(String key)? readSecure,
    Future<void> Function(String key, String value)? writeSecure,
    Future<String?> Function()? getToken,
    Stream<String> Function()? onTokenRefresh,
    Stream<RemoteMessage> Function()? onMessage,
  })  : _readSecure = readSecure ??
            ((key) => const FlutterSecureStorage().read(key: key)),
        _writeSecure = writeSecure ??
            ((key, value) =>
                const FlutterSecureStorage().write(key: key, value: value)),
        _getToken = getToken ?? FirebaseMessaging.instance.getToken,
        _onTokenRefresh =
            onTokenRefresh ?? (() => FirebaseMessaging.instance.onTokenRefresh),
        _onMessage = onMessage ?? (() => FirebaseMessaging.onMessage);

  Future<String> _deviceId() async {
    final existing = await _readSecure(_deviceIdKey);
    if (existing != null) return existing;
    final id = _generateId();
    await _writeSecure(_deviceIdKey, id);
    return id;
  }

  String _generateId() {
    final rand = Random.secure();
    return List.generate(32, (_) => rand.nextInt(16).toRadixString(16)).join();
  }

  /// Fetches the current FCM token and (re)registers it with the Pi.
  /// Safe to call repeatedly (e.g. on every successful connect) — the
  /// underlying MQTT publish is retained, so re-sending the same value is
  /// harmless.
  Future<void> registerToken() async {
    final token = await _getToken();
    if (token == null) return;
    final deviceId = await _deviceId();
    await _repository.registerFcmToken(deviceId, token);
  }

  /// Starts listening for FCM token rotation and republishes on change.
  void listenForRefresh() {
    _onTokenRefresh().listen((newToken) async {
      final deviceId = await _deviceId();
      await _repository.registerFcmToken(deviceId, newToken);
    });
  }

  /// Starts listening for FCM messages that arrive while the app is in the
  /// foreground (background/terminated delivery is handled automatically by
  /// Android and never reaches this callback). Missing title/body fields
  /// fall back to a generic label rather than showing a blank notification.
  void listenForForegroundMessages(void Function(String title, String body) onNotification) {
    _onMessage().listen((message) {
      final title = message.notification?.title ?? 'Greenhouse';
      final body = message.notification?.body ?? '';
      onNotification(title, body);
    });
  }
}
