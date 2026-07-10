// Internal event wrappers emitted by MqttConnection into the event stream.
// These carry raw JSON strings for payloads that need structured parsing
// in the repository layer.

/// Carries the raw JSON payload from greenhouse/weather/forecast.
class WeatherForecastRaw {
  final String payload;
  const WeatherForecastRaw(this.payload);
}

/// Carries the raw JSON payload from greenhouse/rules/current.
class RulesPayloadRaw {
  final String payload;
  const RulesPayloadRaw(this.payload);
}

/// Carries the raw JSON payload from greenhouse/settings/notifications/current.
class NotificationSettingsRaw {
  final String payload;
  const NotificationSettingsRaw(this.payload);
}

/// Carries the raw JSON payload from greenhouse/history/response/<id> —
/// the MQTT-based history fetch used when the app is on the HiveMQ Cloud
/// path (remote) rather than the LAN, where the HTTP /api/history endpoint
/// isn't reachable.
class HistoryResponseRaw {
  final String id;
  final String payload;
  const HistoryResponseRaw(this.id, this.payload);
}
