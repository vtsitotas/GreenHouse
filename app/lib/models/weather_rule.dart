import 'dart:convert';

class WeatherRule {
  final String id;
  final String name;
  final bool enabled;
  final String triggerMetric;
  final String op;
  final double value;
  final String actuatorId;
  final String command;

  const WeatherRule({
    required this.id,
    required this.name,
    required this.enabled,
    required this.triggerMetric,
    required this.op,
    required this.value,
    required this.actuatorId,
    required this.command,
  });

  factory WeatherRule.fromJson(Map<String, dynamic> json) => WeatherRule(
        id:            json['id']   as String? ?? '',
        name:          json['name'] as String? ?? '',
        enabled:       json['enabled'] as bool? ?? true,
        triggerMetric: (json['trigger'] as Map)['metric'] as String? ?? '',
        op:            (json['trigger'] as Map)['op']     as String? ?? '>',
        value:         ((json['trigger'] as Map)['value'] as num).toDouble(),
        actuatorId:    (json['action'] as Map)['actuator'] as String? ?? '',
        command:       (json['action'] as Map)['command']  as String? ?? 'OFF',
      );

  Map<String, dynamic> toJson() => {
        'id':      id,
        'name':    name,
        'enabled': enabled,
        'trigger': {'metric': triggerMetric, 'op': op, 'value': value},
        'action':  {'actuator': actuatorId, 'command': command},
      };

  WeatherRule copyWith({bool? enabled, double? value}) => WeatherRule(
        id:            id,
        name:          name,
        enabled:       enabled ?? this.enabled,
        triggerMetric: triggerMetric,
        op:            op,
        value:         value ?? this.value,
        actuatorId:    actuatorId,
        command:       command,
      );

  static List<WeatherRule> listFromJson(String payload) {
    final list = jsonDecode(payload) as List;
    return list.map((e) => WeatherRule.fromJson(e as Map<String, dynamic>)).toList();
  }

  static String listToJson(List<WeatherRule> rules) =>
      jsonEncode(rules.map((r) => r.toJson()).toList());

  String get conditionLabel => '$triggerMetric $op $value';

  String get metricLabel {
    switch (triggerMetric) {
      case 'temperature': return 'Temperature (°C)';
      case 'rain_mm_1h':  return 'Rain next hour (mm)';
      case 'humidity':    return 'Humidity (%)';
      case 'wind_kmh':    return 'Wind (km/h)';
      case 'uv_index':    return 'UV Index';
      default:            return triggerMetric;
    }
  }
}
