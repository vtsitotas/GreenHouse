import 'dart:convert';

class WeatherAlert {
  final String type;
  final String message;
  final String severity; // 'info' | 'warning' | 'error'
  final DateTime receivedAt;

  const WeatherAlert({
    required this.type,
    required this.message,
    required this.severity,
    required this.receivedAt,
  });

  factory WeatherAlert.fromJson(Map<String, dynamic> json) => WeatherAlert(
        type: json['type'] as String? ?? 'unknown',
        message: json['message'] as String? ?? '',
        severity: json['severity'] as String? ?? 'info',
        receivedAt: DateTime.now(),
      );

  factory WeatherAlert.fromMqtt(String payload) =>
      WeatherAlert.fromJson(jsonDecode(payload) as Map<String, dynamic>);

  String get title {
    switch (type) {
      case 'frost':          return '❄️ Frost Alert';
      case 'daily_summary':  return '🌤 Daily Forecast';
      case 'rain-close':     return '🌧 Rain Alert';
      case 'frost-heat':     return '❄️ Frost Protection';
      case 'heat-fan':       return '🌡 Heat Wave';
      default:               return '🌿 Greenhouse Alert';
    }
  }

  bool get isWarning => severity == 'warning' || severity == 'error';
}
