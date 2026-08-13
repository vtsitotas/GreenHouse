/// Comparing live/historical readings against a [PlantProfile]'s ideal
/// ranges, and turning the result into words and a badge colour.
///
/// Deliberately a separate file from `utils/plain_language.dart`: that file
/// is entirely about mesh/device health (signal, battery, connectivity) --
/// nothing in it concerns growing conditions. Keeping this file separate
/// avoids mixing two unrelated domains into one "misc labels" file.
library;

import 'package:flutter/material.dart';
import 'package:greenhouse_app/models/plant_profile.dart';
import 'package:greenhouse_app/theme/app_colors.dart';

enum PlantConditionStatus { unknown, good, low, high }

/// Maps a rule-engine metric key (`soil_moisture`) to the slash-separated key
/// the live readings map uses (`soil/moisture`) -- the two subsystems named
/// the same quantity differently (see docs/technical/13-mobile-app.md), so
/// anything that needs both a profile range *and* a live reading for the
/// same metric has to bridge them explicitly rather than assume one key.
const Map<String, String> kMetricReadingsKey = {
  'soil_moisture': 'soil/moisture',
  'air_temperature': 'air/temperature',
  'air_humidity': 'air/humidity',
};

String metricDisplayName(String metric) => switch (metric) {
      'soil_moisture' => 'Soil moisture',
      'air_temperature' => 'Air temperature',
      'air_humidity' => 'Air humidity',
      _ => metric,
    };

/// Where a single value sits relative to a range. Null bounds mean "no
/// limit on that side" -- e.g. a humidity ceiling with no floor.
PlantConditionStatus metricStatus(double? value, ClimateRange range) {
  if (value == null) return PlantConditionStatus.unknown;
  if (range.min == null && range.max == null) return PlantConditionStatus.unknown;
  if (range.min != null && value < range.min!) return PlantConditionStatus.low;
  if (range.max != null && value > range.max!) return PlantConditionStatus.high;
  return PlantConditionStatus.good;
}

/// Combines several metrics' statuses into one, for the card badge.
///
/// "Worst wins": a metric out of range is more actionable than "we don't
/// know yet", which is itself more actionable than "all good" -- so that
/// ordering is the severity ordering here, checked worst-first. Low and high
/// are checked as two separate branches (not a single "out of range" check)
/// so the result stays one of the four enum values a caller already knows
/// how to render.
PlantConditionStatus overallZoneStatus(Map<String, PlantConditionStatus> perMetric) {
  if (perMetric.isEmpty) return PlantConditionStatus.unknown;
  final statuses = perMetric.values.toSet();
  if (statuses.contains(PlantConditionStatus.low)) return PlantConditionStatus.low;
  if (statuses.contains(PlantConditionStatus.high)) return PlantConditionStatus.high;
  if (statuses.contains(PlantConditionStatus.unknown)) return PlantConditionStatus.unknown;
  return PlantConditionStatus.good;
}

/// Colour + icon for a status badge. Low and high share the warning colour
/// (both mean "do something"); only the arrow direction differs, matching
/// the direction printed in [metricAdviceText].
(Color, IconData) zoneStatusVisual(PlantConditionStatus status) => switch (status) {
      PlantConditionStatus.good => (AppColors.online, Icons.eco),
      PlantConditionStatus.low => (AppColors.warning, Icons.arrow_downward),
      PlantConditionStatus.high => (AppColors.warning, Icons.arrow_upward),
      PlantConditionStatus.unknown => (AppColors.pending, Icons.help_outline),
    };

/// A short, plant-specific sentence for one metric's status -- same
/// "cheapest-first, says what to do" spirit as `plain_language.dart`'s
/// offline-node steps.
String metricAdviceText(String metric, PlantConditionStatus status) {
  if (status == PlantConditionStatus.unknown) return 'No reading yet.';
  if (status == PlantConditionStatus.good) return 'Within the ideal range for this plant.';
  switch (metric) {
    case 'soil_moisture':
      return status == PlantConditionStatus.low
          ? 'Soil is drier than this plant likes — consider watering.'
          : 'Soil is wetter than this plant likes — ease off watering.';
    case 'air_temperature':
      return status == PlantConditionStatus.low
          ? 'Air is colder than this plant likes.'
          : 'Air is hotter than this plant likes — consider ventilation.';
    case 'air_humidity':
      return status == PlantConditionStatus.low
          ? 'Air is drier than this plant likes.'
          : 'Air is more humid than this plant likes — consider ventilation.';
    default:
      return status == PlantConditionStatus.low ? 'Below the ideal range.' : 'Above the ideal range.';
  }
}

/// Average and % of samples inside [range], over whatever window of history
/// the caller already fetched (e.g. the last 7 days of a metric's minute/
/// hour readings). Pure function so it is testable without a real history
/// fetch -- the zone care sheet supplies the values via the existing
/// `historyPointsProvider`.
({double average, double compliancePct}) historicalCompliance(
  List<double> values,
  ClimateRange range,
) {
  if (values.isEmpty) return (average: 0, compliancePct: 0);
  final average = values.reduce((a, b) => a + b) / values.length;
  final inRange = values.where((v) =>
      (range.min == null || v >= range.min!) && (range.max == null || v <= range.max!)).length;
  return (average: average, compliancePct: inRange / values.length * 100);
}
