import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/services/multicast_lock.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const channel = MethodChannel('greenhouse/multicast');

  tearDown(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null);
  });

  test('acquire invokes the native "acquire" method', () async {
    String? called;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
      called = call.method;
      return null;
    });

    await MulticastLock.acquire();

    expect(called, 'acquire');
  });

  test('release invokes the native "release" method', () async {
    String? called;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
      called = call.method;
      return null;
    });

    await MulticastLock.release();

    expect(called, 'release');
  });

  // Regression guard: iOS/desktop have no MainActivity.kt handler for this
  // channel, so invokeMethod throws MissingPluginException there. Discovery
  // must keep running on those platforms instead of crashing pairing.
  test('acquire swallows MissingPluginException on platforms with no handler', () async {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null);

    await expectLater(MulticastLock.acquire(), completes);
  });

  test('release swallows MissingPluginException on platforms with no handler', () async {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null);

    await expectLater(MulticastLock.release(), completes);
  });
}
