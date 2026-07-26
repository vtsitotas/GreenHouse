import 'package:flutter/material.dart';

/// Shared battery-icon threshold mapping, extracted from
/// `NodeListTile._batteryIcon` so `MeshNodeCard` can reuse the exact same
/// rules without duplicating them.
IconData batteryIconFor(double? percent) {
  if (percent == null) return Icons.battery_unknown;
  if (percent > 80) return Icons.battery_full;
  if (percent > 50) return Icons.battery_5_bar;
  if (percent > 20) return Icons.battery_3_bar;
  return Icons.battery_alert;
}
