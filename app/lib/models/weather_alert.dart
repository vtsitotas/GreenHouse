import 'dart:convert';

class WeatherAlert {
  final String type;
  final String message;
  final String severity; // 'info' | 'warning' | 'error' | 'critical'
  final DateTime receivedAt;
  /// Set for alerts forwarded over LoRa from a remote site
  /// (greenhouse/sites/<site>/weather/alert); null for this greenhouse.
  final String? site;

  const WeatherAlert({
    required this.type,
    required this.message,
    required this.severity,
    required this.receivedAt,
    this.site,
  });

  factory WeatherAlert.fromJson(Map<String, dynamic> json) {
    final ruleId = json['rule_id'] as String?;
    return WeatherAlert(
      // A plain LoRa rule alert carries only rule_id and severity.
      type: json['type'] as String? ?? ruleId ?? 'unknown',
      message: json['message'] as String? ?? (ruleId != null ? 'Rule $ruleId' : ''),
      severity: json['severity'] as String? ?? 'info',
      receivedAt: DateTime.now(),
      site: json['site'] as String?,
    );
  }

  factory WeatherAlert.fromMqtt(String payload) =>
      WeatherAlert.fromJson(jsonDecode(payload) as Map<String, dynamic>);

  String get title {
    switch (type) {
      case 'hazard-fire':    return '🔥 Possible Fire';
      case 'hazard-flood':   return '🌊 Soil Flooded';
      case 'hazard-frost':   return '❄️ Frost in Greenhouse';
      case 'hazard-heat':    return '🌡 Extreme Heat';
      case 'hazard-drought': return '🏜 Soil Too Dry';
      case 'frost':          return '❄️ Frost Alert';
      case 'daily_summary':  return '🌤 Daily Forecast';
      case 'rain-close':     return '🌧 Rain Alert';
      case 'frost-heat':     return '❄️ Frost Protection';
      case 'heat-fan':       return '🌡 Heat Wave';
      default:               return '🌿 Greenhouse Alert';
    }
  }

  /// Raised by the Pi's hazard service (fire, flood, frost, heat, drought).
  bool get isHazard => type.startsWith('hazard-');

  bool get isCritical => severity == 'critical';

  bool get isWarning => severity == 'warning' || severity == 'error' || isCritical;
}
