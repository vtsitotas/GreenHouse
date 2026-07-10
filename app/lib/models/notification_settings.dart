class NotificationSettings {
  final bool frostForecast;
  final bool dailySummary;

  const NotificationSettings({required this.frostForecast, required this.dailySummary});

  factory NotificationSettings.fromJson(Map<String, dynamic> json) => NotificationSettings(
        frostForecast: json['frost_forecast'] as bool? ?? true,
        dailySummary: json['daily_summary'] as bool? ?? true,
      );

  Map<String, dynamic> toJson() => {
        'frost_forecast': frostForecast,
        'daily_summary': dailySummary,
      };

  NotificationSettings copyWith({bool? frostForecast, bool? dailySummary}) => NotificationSettings(
        frostForecast: frostForecast ?? this.frostForecast,
        dailySummary: dailySummary ?? this.dailySummary,
      );
}
