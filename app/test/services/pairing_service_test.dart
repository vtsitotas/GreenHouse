import 'dart:convert';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:greenhouse_app/models/connection_config.dart';
import 'package:greenhouse_app/services/pairing_service.dart';

class MockSecureStorage extends Mock implements FlutterSecureStorage {}

const _configKey = 'greenhouse_connection_config';

const _config = ConnectionConfig(
  lanHost: 'greenhouse.local', remoteHost: 'cloud.example', port: 8883,
  tlsFingerprint: 'AA:BB', username: 'app', password: 'pw',
  remoteUsername: 'ru', remotePassword: 'rp',
);

void main() {
  late MockSecureStorage storage;
  late PairingService service;

  setUp(() {
    storage = MockSecureStorage();
    service = PairingService(storage);
    when(() => storage.delete(key: any(named: 'key'))).thenAnswer((_) async {});
  });

  test('loadConfig returns the stored config', () async {
    when(() => storage.read(key: any(named: 'key')))
        .thenAnswer((_) async => jsonEncode(_config.toJson()));

    final loaded = await service.loadConfig();

    expect(loaded?.lanHost, 'greenhouse.local');
    expect(await service.isPaired, isTrue);
  });

  test('loadConfig returns null when nothing is stored', () async {
    when(() => storage.read(key: any(named: 'key'))).thenAnswer((_) async => null);

    expect(await service.loadConfig(), isNull);
    expect(await service.isPaired, isFalse);
  });

  // Regression: an undecryptable blob (Android Keystore/SharedPreferences
  // mismatch after a reinstall or backup restore) used to escape as a
  // PlatformException through the router redirect, leaving a black screen.
  test('loadConfig recovers from an undecryptable blob and clears it', () async {
    when(() => storage.read(key: any(named: 'key'))).thenThrow(
      PlatformException(code: 'Exception encountered', message: 'BAD_DECRYPT'),
    );

    expect(await service.loadConfig(), isNull);
    expect(await service.isPaired, isFalse);
    verify(() => storage.delete(key: _configKey)).called(2);
  });

  test('loadConfig recovers from corrupt JSON and clears it', () async {
    when(() => storage.read(key: any(named: 'key')))
        .thenAnswer((_) async => 'not-json{');

    expect(await service.loadConfig(), isNull);
    verify(() => storage.delete(key: _configKey)).called(1);
  });

  test('loadConfig still returns null when clearing the corrupt entry fails', () async {
    when(() => storage.read(key: any(named: 'key')))
        .thenThrow(PlatformException(code: 'BAD_DECRYPT'));
    when(() => storage.delete(key: any(named: 'key')))
        .thenThrow(PlatformException(code: 'still broken'));

    expect(await service.loadConfig(), isNull);
  });
}
