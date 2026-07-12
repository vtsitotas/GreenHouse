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
        camStatusProvider.overrideWith((ref) => Stream.value(CamStatus(
              online: true,
              ip: '192.168.1.50',
              lastEvent: const CamEvent(eventId: 'evt1', ts: 1700000000),
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
