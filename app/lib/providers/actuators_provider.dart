import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/actuator_state.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';

final actuatorsProvider = StreamProvider<Map<String, ActuatorState>>((ref) {
  ref.watch(connectOnStartProvider);
  return ref.watch(repositoryProvider).actuators;
});
