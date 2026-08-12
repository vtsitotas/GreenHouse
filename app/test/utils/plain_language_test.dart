import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/screens/devices/battery_icon.dart';
import 'package:greenhouse_app/screens/devices/mesh_map/mesh_link_painter.dart';
import 'package:greenhouse_app/theme/app_colors.dart';
import 'package:greenhouse_app/utils/plain_language.dart';

NodeStatus _node({int? rank, String? parentId}) => NodeStatus(
      nodeId: 'n1',
      isOnline: true,
      lastSeen: DateTime(2026, 8, 12),
      meshRank: rank,
      parentId: parentId,
    );

void main() {
  group('signalLabel', () {
    test('bands, checked on both sides of every boundary', () {
      expect(signalLabel(-59), 'Strong');
      expect(signalLabel(-60), 'Strong'); // inclusive boundary
      expect(signalLabel(-61), 'Fair');
      expect(signalLabel(-75), 'Fair'); // inclusive boundary
      expect(signalLabel(-76), 'Weak');
    });

    test('null is Unknown, not Weak -- no reading is not a bad reading', () {
      expect(signalLabel(null), 'Unknown');
    });
  });

  group('batteryLabel', () {
    test('bands, checked on both sides of every boundary', () {
      expect(batteryLabel(81), 'Full');
      expect(batteryLabel(80), 'Good'); // exclusive boundary, matches the icon
      expect(batteryLabel(51), 'Good');
      expect(batteryLabel(50), 'Low');
      expect(batteryLabel(21), 'Low');
      expect(batteryLabel(20), 'Replace soon');
      expect(batteryLabel(0), 'Replace soon');
    });

    test('null is Unknown', () {
      expect(batteryLabel(null), 'Unknown');
    });
  });

  group('sleepLabel', () {
    test('sleepy is a feature, not a fault', () {
      expect(sleepLabel(true), 'Battery saver');
      expect(sleepLabel(false), 'Always on');
      expect(sleepLabel(null), 'Unknown');
    });
  });

  group('connectionLabel', () {
    test('rank 0 is the hub itself', () {
      expect(connectionLabel(_node(rank: 0)), 'This is the hub');
    });

    test('rank 1 talks straight to the hub', () {
      expect(connectionLabel(_node(rank: 1)), 'Talks straight to the hub');
    });

    test('a relayed node names the sensor it goes through', () {
      expect(
        connectionLabel(_node(rank: 2, parentId: 'AABB'), parentName: 'Tomato bed'),
        'Passes through Tomato bed (2 steps to the hub)',
      );
    });

    test('falls back to the raw parent id when no friendly name is given', () {
      expect(
        connectionLabel(_node(rank: 2, parentId: 'AABB')),
        'Passes through AABB (2 steps to the hub)',
      );
    });

    test('a relayed node with no known parent still says something useful', () {
      expect(
        connectionLabel(_node(rank: 3)),
        'Passes through 3 other sensors',
      );
    });

    test('no mesh data yet is stated, not guessed', () {
      expect(connectionLabel(_node()), 'Not connected yet');
    });
  });

  group('shortConnectionLabel', () {
    test('hidden for the hub and for a node with no mesh data', () {
      expect(shortConnectionLabel(_node(rank: 0)), isNull);
      expect(shortConnectionLabel(_node()), isNull);
    });

    test('Direct for rank 1, step count beyond that', () {
      expect(shortConnectionLabel(_node(rank: 1)), 'Direct');
      expect(shortConnectionLabel(_node(rank: 3)), '3 steps');
    });
  });

  group('threshold sharing', () {
    // These are the tests that matter most: they fail if someone edits a
    // number in one place and not the other, which is exactly how a legend
    // ends up disagreeing with the colour beside it.
    test('signalLabel and linkQualityOf agree across every boundary', () {
      const cases = {
        -50: 'Strong',
        -60: 'Strong',
        -61: 'Fair',
        -75: 'Fair',
        -76: 'Weak',
        -90: 'Weak',
      };
      const expectedColor = {
        'Strong': AppColors.online,
        'Fair': Colors.amber,
        'Weak': Colors.deepOrange,
      };

      for (final entry in cases.entries) {
        final label = signalLabel(entry.key);
        expect(label, entry.value, reason: 'label for ${entry.key} dBm');
        expect(
          linkQualityOf(entry.key, true).color,
          expectedColor[label],
          reason: 'colour must match the "$label" band at ${entry.key} dBm',
        );
      }
    });

    test('batteryLabel and batteryIconFor change bands at the same values', () {
      const expectedIcon = {
        'Full': Icons.battery_full,
        'Good': Icons.battery_5_bar,
        'Low': Icons.battery_3_bar,
        'Replace soon': Icons.battery_alert,
      };

      for (final pct in [100.0, 81.0, 80.0, 51.0, 50.0, 21.0, 20.0, 0.0]) {
        expect(
          batteryIconFor(pct),
          expectedIcon[batteryLabel(pct)],
          reason: 'icon must match the "${batteryLabel(pct)}" band at $pct%',
        );
      }
    });

    test('an unknown battery is unknown in both the label and the icon', () {
      expect(batteryLabel(null), 'Unknown');
      expect(batteryIconFor(null), Icons.battery_unknown);
    });
  });
}
