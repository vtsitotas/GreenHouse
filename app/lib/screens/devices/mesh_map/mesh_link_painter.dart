import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/theme/app_colors.dart';

/// Link quality mapping (spec §Link quality mapping), a pure function of
/// the child-side RSSI reading and the node's online state.
///
/// | RSSI (dBm, at child)   | Color              | Meaning |
/// |------------------------|--------------------|---------|
/// | >= -60                 | `AppColors.online`  | good    |
/// | -61 ... -75             | amber               | fair    |
/// | < -75                  | red/orange          | weak    |
/// | offline or rssi null   | grey, dashed        | stale   |
({Color color, bool dashed}) linkQualityOf(int? rssi, bool online) {
  if (!online || rssi == null) {
    return (color: Colors.grey, dashed: true);
  }
  if (rssi >= -60) {
    return (color: AppColors.online, dashed: false);
  }
  if (rssi >= -75) {
    return (color: Colors.amber, dashed: false);
  }
  return (color: Colors.deepOrange, dashed: false);
}

/// Draws one child->parent link per node that has a resolvable parent
/// position, colored by [linkQualityOf], with a small animated "flow" of
/// dots toward the parent and (optionally) a midpoint RSSI label.
///
/// Positions are normalized (0-1) coordinates; this painter scales them by
/// the canvas [Size] it's given at paint time.
class MeshLinkPainter extends CustomPainter {
  final Map<String, NodeStatus> nodes;
  final Map<String, Offset> positions;
  final double phase;
  final bool showLabels;

  const MeshLinkPainter({
    required this.nodes,
    required this.positions,
    required this.phase,
    this.showLabels = false,
  });

  static const int _flowDotCount = 3;
  static const double _curveFactor = 0.08;

  @override
  void paint(Canvas canvas, Size size) {
    for (final node in nodes.values) {
      final parentId = node.parentId;
      if (parentId == null) continue;

      final childOffset = positions[node.nodeId];
      final parentOffset = positions[parentId];
      if (childOffset == null || parentOffset == null) continue;

      final from = Offset(childOffset.dx * size.width, childOffset.dy * size.height);
      final to = Offset(parentOffset.dx * size.width, parentOffset.dy * size.height);
      final control = _controlPoint(from, to);

      final quality = linkQualityOf(node.parentRssi, node.isOnline);
      final paint = Paint()
        ..color = quality.color
        ..strokeWidth = 2.0
        ..style = PaintingStyle.stroke
        ..strokeCap = StrokeCap.round;

      final path = Path()
        ..moveTo(from.dx, from.dy)
        ..quadraticBezierTo(control.dx, control.dy, to.dx, to.dy);

      if (quality.dashed) {
        _drawDashedPath(canvas, path, paint);
      } else {
        canvas.drawPath(path, paint);
      }

      final dotPaint = Paint()
        ..color = quality.color
        ..style = PaintingStyle.fill;
      for (var i = 0; i < _flowDotCount; i++) {
        final t = (phase + i / _flowDotCount) % 1.0;
        canvas.drawCircle(_quadraticPoint(from, control, to, t), 2.5, dotPaint);
      }

      if (showLabels && node.parentRssi != null) {
        _paintLabel(canvas, '${node.parentRssi} dBm', control);
      }
    }
  }

  Offset _controlPoint(Offset from, Offset to) {
    final mid = Offset((from.dx + to.dx) / 2, (from.dy + to.dy) / 2);
    final dx = to.dx - from.dx;
    final dy = to.dy - from.dy;
    final length = math.sqrt(dx * dx + dy * dy);
    if (length == 0) return mid;
    // Perpendicular unit vector, scaled by a fraction of the link length,
    // gives a slight curve so overlapping links stay visually distinct.
    final normal = Offset(-dy / length, dx / length);
    return mid + normal * (length * _curveFactor);
  }

  Offset _quadraticPoint(Offset p0, Offset p1, Offset p2, double t) {
    final u = 1 - t;
    final x = u * u * p0.dx + 2 * u * t * p1.dx + t * t * p2.dx;
    final y = u * u * p0.dy + 2 * u * t * p1.dy + t * t * p2.dy;
    return Offset(x, y);
  }

  void _drawDashedPath(Canvas canvas, Path path, Paint paint) {
    const dashWidth = 5.0;
    const dashGap = 4.0;
    for (final metric in path.computeMetrics()) {
      var distance = 0.0;
      while (distance < metric.length) {
        final end = math.min(distance + dashWidth, metric.length);
        canvas.drawPath(metric.extractPath(distance, end), paint);
        distance += dashWidth + dashGap;
      }
    }
  }

  void _paintLabel(Canvas canvas, String text, Offset at) {
    final painter = TextPainter(
      text: TextSpan(
        text: text,
        style: const TextStyle(fontSize: 10, color: Colors.black87),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    painter.paint(canvas, Offset(at.dx - painter.width / 2, at.dy - painter.height / 2));
  }

  @override
  bool shouldRepaint(covariant MeshLinkPainter oldDelegate) =>
      !identical(oldDelegate.nodes, nodes) ||
      !identical(oldDelegate.positions, positions) ||
      oldDelegate.phase != phase ||
      oldDelegate.showLabels != showLabels;
}
