class ActuatorState {
  final String actuatorId;
  final bool isOn;
  final bool isPending;
  final DateTime? lastConfirmed;

  const ActuatorState({
    required this.actuatorId,
    required this.isOn,
    this.isPending = false,
    this.lastConfirmed,
  });

  factory ActuatorState.fromMqttState(String actuatorId, String payload) => ActuatorState(
        actuatorId: actuatorId,
        isOn: payload.trim() == 'ON',
        lastConfirmed: DateTime.now(),
      );

  ActuatorState withPending(bool isOn) =>
      ActuatorState(actuatorId: actuatorId, isOn: isOn, isPending: true, lastConfirmed: lastConfirmed);

  ActuatorState withConfirmed(bool isOn) =>
      ActuatorState(actuatorId: actuatorId, isOn: isOn, isPending: false, lastConfirmed: DateTime.now());
}
