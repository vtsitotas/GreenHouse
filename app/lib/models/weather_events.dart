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
