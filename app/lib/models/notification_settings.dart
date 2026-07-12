class NotificationSettings {
  final bool frostForecast;
  final bool dailySummary;
  final bool motionAlert;

  const NotificationSettings({
    required this.frostForecast,
    required this.dailySummary,
    this.motionAlert = true,
  });

  factory NotificationSettings.fromJson(Map<String, dynamic> json) => NotificationSettings(
        frostForecast: json['frost_forecast'] as bool? ?? true,
        dailySummary: json['daily_summary'] as bool? ?? true,
        motionAlert: json['motion_alert'] as bool? ?? true,
      );

  Map<String, dynamic> toJson() => {
        'frost_forecast': frostForecast,
        'daily_summary': dailySummary,
        'motion_alert': motionAlert,
      };

  NotificationSettings copyWith({bool? frostForecast, bool? dailySummary, bool? motionAlert}) =>
      NotificationSettings(
        frostForecast: frostForecast ?? this.frostForecast,
        dailySummary: dailySummary ?? this.dailySummary,
        motionAlert: motionAlert ?? this.motionAlert,
      );
}
