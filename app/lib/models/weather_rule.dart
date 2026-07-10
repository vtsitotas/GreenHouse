import 'dart:convert';

class WeatherRule {
  final String id;
  final String name;
  final bool enabled;
  final bool notify;
  final String? zone; // null = weather-level (ambient/forecast) metric
  final String metric; // bare metric name, e.g. "soil_moisture" or "temperature"
  final String op;
  final double value;
  final int? durationMinutes; // required by the backend whenever zone != null
  final String? actuatorId; // null together with command = alert-only rule
  final String? command;

  const WeatherRule({
    required this.id,
    required this.name,
    required this.enabled,
    required this.notify,
    required this.zone,
    required this.metric,
    required this.op,
    required this.value,
    required this.durationMinutes,
    required this.actuatorId,
    required this.command,
  });

  factory WeatherRule.fromJson(Map<String, dynamic> json) {
    final trigger = json['trigger'] as Map;
    final rawMetric = trigger['metric'] as String? ?? '';
    final parts = rawMetric.split('/');
    final zone = parts.length == 2 ? parts[0] : null;
    final metric = parts.length == 2 ? parts[1] : rawMetric;
    final action = json['action'] as Map?;
    return WeatherRule(
      id: json['id'] as String? ?? '',
      name: json['name'] as String? ?? '',
      enabled: json['enabled'] as bool? ?? true,
      notify: json['notify'] as bool? ?? true,
      zone: zone,
      metric: metric,
      op: trigger['op'] as String? ?? '>',
      value: (trigger['value'] as num).toDouble(),
      durationMinutes: (trigger['duration_minutes'] as num?)?.toInt(),
      actuatorId: action?['actuator'] as String?,
      command: action?['command'] as String?,
    );
  }

  Map<String, dynamic> toJson() {
    final trigger = <String, dynamic>{
      'metric': zone != null ? '$zone/$metric' : metric,
      'op': op,
      'value': value,
    };
    if (durationMinutes != null) trigger['duration_minutes'] = durationMinutes;
    final json = <String, dynamic>{
      'id': id,
      'name': name,
      'enabled': enabled,
      'notify': notify,
      'trigger': trigger,
    };
    if (actuatorId != null && command != null) {
      json['action'] = {'actuator': actuatorId, 'command': command};
    }
    return json;
  }

  WeatherRule copyWith({
    String? name,
    bool? enabled,
    bool? notify,
    double? value,
  }) =>
      WeatherRule(
        id: id,
        name: name ?? this.name,
        enabled: enabled ?? this.enabled,
        notify: notify ?? this.notify,
        zone: zone,
        metric: metric,
        op: op,
        value: value ?? this.value,
        durationMinutes: durationMinutes,
        actuatorId: actuatorId,
        command: command,
      );

  static List<WeatherRule> listFromJson(String payload) {
    final list = jsonDecode(payload) as List;
    return list.map((e) => WeatherRule.fromJson(e as Map<String, dynamic>)).toList();
  }

  static String listToJson(List<WeatherRule> rules) =>
      jsonEncode(rules.map((r) => r.toJson()).toList());

  String get conditionLabel => '$metricLabel $op $value';

  String get zoneLabel => zone == null ? 'Weather' : 'Zone ${zone!.replaceFirst('zone', '')}';

  String get metricLabel {
    if (zone != null) {
      switch (metric) {
        case 'soil_moisture':   return 'Soil moisture (%)';
        case 'air_humidity':    return 'Air humidity (%)';
        case 'air_temperature': return 'Air temperature (°C)';
        default:                return metric;
      }
    }
    switch (metric) {
      case 'temperature': return 'Temperature (°C)';
      case 'rain_mm_1h':  return 'Rain next hour (mm)';
      case 'humidity':    return 'Humidity (%)';
      case 'wind_kmh':    return 'Wind (km/h)';
      case 'uv_index':    return 'UV Index';
      default:            return metric;
    }
  }
}
