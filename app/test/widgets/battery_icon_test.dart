import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/screens/devices/battery_icon.dart';

void main() {
  group('batteryIconFor', () {
    test('100% -> battery_full', () {
      expect(batteryIconFor(100), Icons.battery_full);
    });

    test('60% -> battery_5_bar', () {
      expect(batteryIconFor(60), Icons.battery_5_bar);
    });

    test('30% -> battery_3_bar', () {
      expect(batteryIconFor(30), Icons.battery_3_bar);
    });

    test('10% -> battery_alert', () {
      expect(batteryIconFor(10), Icons.battery_alert);
    });

    test('null -> battery_unknown', () {
      expect(batteryIconFor(null), Icons.battery_unknown);
    });

    test('boundary: 80% is not > 80, falls to 5_bar', () {
      expect(batteryIconFor(80), Icons.battery_5_bar);
    });

    test('boundary: 50% is not > 50, falls to 3_bar', () {
      expect(batteryIconFor(50), Icons.battery_3_bar);
    });

    test('boundary: 20% is not > 20, falls to alert', () {
      expect(batteryIconFor(20), Icons.battery_alert);
    });
  });
}
