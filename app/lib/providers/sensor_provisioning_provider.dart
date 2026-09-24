import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../providers/connection_provider.dart';
import '../services/sensor_provisioning_service.dart';

final sensorProvisioningServiceProvider = Provider<SensorProvisioningService?>((ref) {
  final config = ref.watch(savedConfigProvider).valueOrNull;
  if (config == null) return null;
  return SensorProvisioningService(config: config);
});

final unenrolledMacsProvider = FutureProvider<List<String>>((ref) async {
  final svc = ref.watch(sensorProvisioningServiceProvider);
  if (svc == null) return [];
  return svc.unenrolled();
});

/// Sensors the Pi trusts, including ones that have not reported yet -- the
/// Devices screen lists those as "Joining" / "Waiting for first reading".
final enrolledSensorsProvider = FutureProvider<List<EnrolledSensor>>((ref) async {
  final svc = ref.watch(sensorProvisioningServiceProvider);
  if (svc == null) return [];
  return svc.enrolled();
});
