import 'dart:async';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:greenhouse_app/connection/greenhouse_connection.dart';
import 'package:greenhouse_app/models/connection_config.dart';
import 'package:greenhouse_app/repository/greenhouse_repository.dart';
import 'package:greenhouse_app/services/fcm_token_service.dart';

class MockConnection extends Mock implements GreenhouseConnection {}

void main() {
  setUpAll(() {
    registerFallbackValue(const ConnectionConfig(
      lanHost: '', remoteHost: '', port: 9001,
      tlsFingerprint: '', username: '', password: '',
      remoteUsername: '', remotePassword: '',
    ));
  });

  late MockConnection conn;
  late GreenhouseRepository repo;

  setUp(() {
    conn = MockConnection();
    when(() => conn.events).thenAnswer((_) => const Stream.empty());
    when(() => conn.status).thenAnswer((_) => const Stream.empty());
    when(() => conn.disconnect()).thenAnswer((_) async {});
    when(() => conn.publishRaw(any(), any(), retain: any(named: 'retain')))
        .thenAnswer((_) async {});
    repo = GreenhouseRepository(connection: conn);
  });

  tearDown(() => repo.disconnect());

  test('registerToken generates and persists a device id, then publishes retained', () async {
    String? storedId;
    final service = FcmTokenService(
      repo,
      readSecure: (key) async => storedId,
      writeSecure: (key, value) async => storedId = value,
      getToken: () async => 'token-123',
      onTokenRefresh: () => const Stream.empty(),
    );

    await service.registerToken();

    expect(storedId, isNotNull);
    verify(() => conn.publishRaw(
          'greenhouse/app/fcm_token/$storedId',
          'token-123',
          retain: true,
        )).called(1);
  });

  test('registerToken reuses an existing stored device id', () async {
    final service = FcmTokenService(
      repo,
      readSecure: (key) async => 'existing-device-id',
      writeSecure: (key, value) async => fail('should not write a new id'),
      getToken: () async => 'token-456',
      onTokenRefresh: () => const Stream.empty(),
    );

    await service.registerToken();

    verify(() => conn.publishRaw(
          'greenhouse/app/fcm_token/existing-device-id',
          'token-456',
          retain: true,
        )).called(1);
  });

  test('registerToken does nothing when no token is available yet', () async {
    final service = FcmTokenService(
      repo,
      readSecure: (key) async => 'existing-device-id',
      writeSecure: (key, value) async {},
      getToken: () async => null,
      onTokenRefresh: () => const Stream.empty(),
    );

    await service.registerToken();

    verifyNever(() => conn.publishRaw(any(), any(), retain: any(named: 'retain')));
  });

  test('listenForRefresh republishes when the token changes', () async {
    final refreshCtrl = StreamController<String>();
    final service = FcmTokenService(
      repo,
      readSecure: (key) async => 'device-xyz',
      writeSecure: (key, value) async {},
      getToken: () async => 'unused',
      onTokenRefresh: () => refreshCtrl.stream,
    );

    service.listenForRefresh();
    refreshCtrl.add('refreshed-token');
    await Future(() {});
    await Future(() {});

    verify(() => conn.publishRaw(
          'greenhouse/app/fcm_token/device-xyz',
          'refreshed-token',
          retain: true,
        )).called(1);
    await refreshCtrl.close();
  });

  test('listenForForegroundMessages invokes the callback with title/body', () async {
    final messageCtrl = StreamController<RemoteMessage>();
    final service = FcmTokenService(
      repo,
      readSecure: (key) async => 'device-xyz',
      writeSecure: (key, value) async {},
      getToken: () async => 'unused',
      onTokenRefresh: () => const Stream.empty(),
      onMessage: () => messageCtrl.stream,
    );

    String? gotTitle;
    String? gotBody;
    service.listenForForegroundMessages((title, body) {
      gotTitle = title;
      gotBody = body;
    });

    messageCtrl.add(const RemoteMessage(
      notification: RemoteNotification(title: 'Frost warning', body: 'Frost expected tonight'),
    ));
    await Future(() {});

    expect(gotTitle, 'Frost warning');
    expect(gotBody, 'Frost expected tonight');
    await messageCtrl.close();
  });

  test('listenForForegroundMessages falls back to defaults when notification fields are missing', () async {
    final messageCtrl = StreamController<RemoteMessage>();
    final service = FcmTokenService(
      repo,
      readSecure: (key) async => 'device-xyz',
      writeSecure: (key, value) async {},
      getToken: () async => 'unused',
      onTokenRefresh: () => const Stream.empty(),
      onMessage: () => messageCtrl.stream,
    );

    String? gotTitle;
    String? gotBody;
    service.listenForForegroundMessages((title, body) {
      gotTitle = title;
      gotBody = body;
    });

    messageCtrl.add(const RemoteMessage());
    await Future(() {});

    expect(gotTitle, 'Greenhouse');
    expect(gotBody, '');
    await messageCtrl.close();
  });
}
