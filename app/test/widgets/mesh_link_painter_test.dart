import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/screens/devices/mesh_map/mesh_link_painter.dart';
import 'package:greenhouse_app/theme/app_colors.dart';

void main() {
  group('linkQualityOf', () {
    test('-55 dBm online is good (AppColors.online), not dashed', () {
      final q = linkQualityOf(-55, true);
      expect(q.color, AppColors.online);
      expect(q.dashed, isFalse);
    });

    test('-60 dBm online is the good boundary (AppColors.online), not dashed', () {
      final q = linkQualityOf(-60, true);
      expect(q.color, AppColors.online);
      expect(q.dashed, isFalse);
    });

    test('-61 dBm online is fair (amber), not dashed', () {
      final q = linkQualityOf(-61, true);
      expect(q.color, Colors.amber);
      expect(q.dashed, isFalse);
    });

    test('-75 dBm online is the fair boundary (amber), not dashed', () {
      final q = linkQualityOf(-75, true);
      expect(q.color, Colors.amber);
      expect(q.dashed, isFalse);
    });

    test('-76 dBm online is weak (red/orange), not dashed', () {
      final q = linkQualityOf(-76, true);
      expect(q.color, Colors.deepOrange);
      expect(q.dashed, isFalse);
    });

    test('null rssi is grey and dashed regardless of online state', () {
      final q = linkQualityOf(null, true);
      expect(q.color, Colors.grey);
      expect(q.dashed, isTrue);
    });

    test('offline node is grey and dashed even with a strong rssi', () {
      final q = linkQualityOf(-55, false);
      expect(q.color, Colors.grey);
      expect(q.dashed, isTrue);
    });

    test('offline node with null rssi is grey and dashed', () {
      final q = linkQualityOf(null, false);
      expect(q.color, Colors.grey);
      expect(q.dashed, isTrue);
    });
  });

  group('MeshLinkPainter', () {
    test('paint() runs without throwing for a 2-node topology', () {
      final nodes = <String, NodeStatus>{
        'bridge': NodeStatus(
          nodeId: 'bridge',
          isOnline: true,
          lastSeen: DateTime(2026, 7, 26),
          meshRank: 0,
        ),
        'node1': NodeStatus(
          nodeId: 'node1',
          isOnline: true,
          lastSeen: DateTime(2026, 7, 26),
          parentId: 'bridge',
          meshRank: 1,
          parentRssi: -61,
        ),
      };
      final positions = <String, Offset>{
        'bridge': const Offset(0.5, 0.12),
        'node1': const Offset(0.5, 0.30),
      };

      final painter = MeshLinkPainter(
        nodes: nodes,
        positions: positions,
        phase: 0.3,
        showLabels: true,
      );

      final recorder = ui.PictureRecorder();
      final canvas = Canvas(recorder);
      expect(
        () => painter.paint(canvas, const Size(400, 600)),
        returnsNormally,
      );
      recorder.endRecording();
    });

    test('paint() tolerates an offline/dangling parent link without throwing', () {
      final nodes = <String, NodeStatus>{
        'node1': NodeStatus(
          nodeId: 'node1',
          isOnline: false,
          lastSeen: DateTime(2026, 7, 26),
          parentId: 'ghost',
          meshRank: 1,
        ),
      };
      final positions = <String, Offset>{
        'node1': const Offset(0.5, 0.30),
        // 'ghost' intentionally has no position.
      };

      final painter = MeshLinkPainter(
        nodes: nodes,
        positions: positions,
        phase: 0.0,
        showLabels: false,
      );

      final recorder = ui.PictureRecorder();
      final canvas = Canvas(recorder);
      expect(
        () => painter.paint(canvas, const Size(400, 600)),
        returnsNormally,
      );
      recorder.endRecording();
    });

    test('shouldRepaint is true when positions map identity changes', () {
      final nodes = <String, NodeStatus>{};
      final positionsA = <String, Offset>{};
      final positionsB = <String, Offset>{};
      final a = MeshLinkPainter(nodes: nodes, positions: positionsA, phase: 0.0);
      final b = MeshLinkPainter(nodes: nodes, positions: positionsB, phase: 0.0);
      expect(a.shouldRepaint(b), isTrue);
    });

    test('shouldRepaint is false when nothing changed', () {
      final nodes = <String, NodeStatus>{};
      final positions = <String, Offset>{};
      final a = MeshLinkPainter(nodes: nodes, positions: positions, phase: 0.5);
      final b = MeshLinkPainter(nodes: nodes, positions: positions, phase: 0.5);
      expect(a.shouldRepaint(b), isFalse);
    });
  });
}
