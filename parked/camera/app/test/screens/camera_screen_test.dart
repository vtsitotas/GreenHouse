import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/cam_event.dart';
import 'package:greenhouse_app/models/cam_status.dart';
import 'package:greenhouse_app/models/connection_status.dart';
import 'package:greenhouse_app/providers/camera_provider.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';
import 'package:greenhouse_app/screens/camera/camera_screen.dart';

void main() {
  _streamUrlTests();
  testWidgets('shows offline status when camera has never been seen', (tester) async {
    await tester.pumpWidget(ProviderScope(
      overrides: [
        camStatusProvider.overrideWith((ref) => Stream.value(const CamStatus(online: false))),
        connectionStatusProvider.overrideWith((ref) => Stream.value(ConnectionStatus.offline)),
      ],
      child: const MaterialApp(home: CameraScreen()),
    ));
    await tester.pump();
    expect(find.textContaining('Offline'), findsWidgets);
  });

  testWidgets('shows online status with last motion event', (tester) async {
    await tester.pumpWidget(ProviderScope(
      overrides: [
        camStatusProvider.overrideWith((ref) => Stream.value(const CamStatus(
              online: true,
              ip: '192.168.1.50',
              lastEvent: CamEvent(eventId: 'evt1', ts: 1700000000),
            ))),
        connectionStatusProvider.overrideWith((ref) => Stream.value(ConnectionStatus.local)),
      ],
      child: const MaterialApp(home: CameraScreen()),
    ));
    await tester.pump();
    expect(find.textContaining('Online'), findsWidgets);
    expect(find.textContaining('Last motion'), findsOneWidget);
  });
}

// ── /stream authentication (IMPROVEMENTS.md A5 remainder) ───────────────────
// The camera's MJPEG stream used to be the one endpoint any LAN device could
// open with no credentials at all. It now requires CAM_TOKEN, which the app
// learns via the PIN-gated pairing response.
void _streamUrlTests() {
  test('streamUrl attaches the cam token as a query param', () {
    expect(streamUrl('192.168.1.50', 'cam-tok'),
        'http://192.168.1.50/stream?token=cam-tok');
  });

  test('streamUrl percent-encodes a token with URL-unsafe characters', () {
    expect(streamUrl('192.168.1.50', 'a b&c=d'),
        'http://192.168.1.50/stream?token=a+b%26c%3Dd');
  });

  test('streamUrl still produces a token param when the token is empty', () {
    // An empty token must reach the camera and be rejected there (visible
    // 401), rather than silently producing the old unauthenticated URL.
    expect(streamUrl('192.168.1.50', ''), 'http://192.168.1.50/stream?token=');
  });
}
