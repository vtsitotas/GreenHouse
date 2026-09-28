class NotificationSettings {
  final bool frostForecast;
  final bool dailySummary;
  /// Push for fire/flood/frost/heat/drought hazards (the Pi's hazard
  /// service). The alerts still appear in the app when this is off.
  final bool hazardAlerts;

  const NotificationSettings({
    required this.frostForecast,
    required this.dailySummary,
    this.hazardAlerts = true,
  });

  factory NotificationSettings.fromJson(Map<String, dynamic> json) => NotificationSettings(
        frostForecast: json['frost_forecast'] as bool? ?? true,
        dailySummary: json['daily_summary'] as bool? ?? true,
        hazardAlerts: json['hazard_alerts'] as bool? ?? true,
      );

  Map<String, dynamic> toJson() => {
        'frost_forecast': frostForecast,
        'daily_summary': dailySummary,
        'hazard_alerts': hazardAlerts,
      };

  NotificationSettings copyWith({bool? frostForecast, bool? dailySummary, bool? hazardAlerts}) =>
      NotificationSettings(
        frostForecast: frostForecast ?? this.frostForecast,
        dailySummary: dailySummary ?? this.dailySummary,
        hazardAlerts: hazardAlerts ?? this.hazardAlerts,
      );
}
