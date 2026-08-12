import 'package:flutter/material.dart';
import 'package:greenhouse_app/utils/plain_language.dart';

/// Shared battery-icon threshold mapping, extracted from
/// `NodeListTile._batteryIcon` so `MeshNodeCard` can reuse the exact same
/// rules without duplicating them.
///
/// The bands are imported from `plain_language.dart` so this icon and the
/// word `batteryLabel` shows beside it always agree.
IconData batteryIconFor(double? percent) {
  if (percent == null) return Icons.battery_unknown;
  if (percent > kBatteryFullPct) return Icons.battery_full;
  if (percent > kBatteryGoodPct) return Icons.battery_5_bar;
  if (percent > kBatteryLowPct) return Icons.battery_3_bar;
  return Icons.battery_alert;
}
