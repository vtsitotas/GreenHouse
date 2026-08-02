import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/cam_status.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';

/// Emits the current camera status (online/offline + last motion event).
final camStatusProvider = StreamProvider<CamStatus>((ref) {
  ref.watch(connectOnStartProvider);
  return ref.watch(repositoryProvider).camStatus;
});
