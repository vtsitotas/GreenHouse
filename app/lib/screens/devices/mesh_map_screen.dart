import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/providers/nodes_provider.dart';
import 'package:greenhouse_app/screens/devices/mesh_map/mesh_layout.dart';
import 'package:greenhouse_app/screens/devices/mesh_map/mesh_link_painter.dart';
import 'package:greenhouse_app/screens/devices/mesh_map/mesh_node_card.dart';
import 'package:greenhouse_app/screens/devices/mesh_map/node_positions_store.dart';
import 'package:greenhouse_app/theme/app_colors.dart';

/// The live Mesh Map screen (spec §Screen structure): every known node as a
/// card on a pannable/zoomable field, auto-laid-out by mesh rank via
/// [MeshLayout], linked to its parent by [MeshLinkPainter], with
/// drag-to-pin persistence via [NodePositionsStore].
class MeshMapScreen extends ConsumerStatefulWidget {
  const MeshMapScreen({super.key});

  @override
  ConsumerState<MeshMapScreen> createState() => _MeshMapScreenState();
}

class _MeshMapScreenState extends ConsumerState<MeshMapScreen>
    with SingleTickerProviderStateMixin {
  static const double _canvasWidth = 1200;
  static const double _canvasHeight = 1600;
  // Used only for the vertical-centering math below (`top = y*H - _cardHeight
  // / 2`) — deliberately NOT applied as a hard layout constraint on the card
  // itself (see `_buildCard`): `MeshNodeCard`'s real content (icon + title +
  // subtitle + chip row, plus the Offline pill) intrinsically renders taller
  // than ~90px, and forcing that exact height via a tight `SizedBox` would
  // trigger `RenderFlex` overflow. Leaving the child's height unconstrained
  // keeps the card's *top edge* anchored at the intended centering point
  // without clipping or overflow.
  static const double _cardHeight = 90;
  static const double _cardWidth = MeshNodeCard.width;

  late final AnimationController _phaseController;

  /// Normalized offsets for cards currently mid-drag (not yet persisted).
  /// Kept out of `pinnedPositionsProvider` so a drag-in-progress never
  /// round-trips through `SharedPreferences` on every pointer move.
  final Map<String, Offset> _dragging = {};

  @override
  void initState() {
    super.initState();
    _phaseController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1500),
    )..repeat();
  }

  @override
  void dispose() {
    _phaseController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final nodesAsync = ref.watch(nodesProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Mesh Map')),
      body: nodesAsync.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (error, _) => Center(child: Text('Error: $error')),
        data: (nodes) {
          if (nodes.isEmpty) {
            return const Center(child: Text('No nodes detected yet'));
          }
          final showDegradedBanner = nodes.values.every((n) => n.meshRank == null);
          return Column(
            children: [
              if (showDegradedBanner) const _DegradedDataBanner(),
              Expanded(child: _buildCanvas(nodes)),
            ],
          );
        },
      ),
    );
  }

  Widget _buildCanvas(Map<String, NodeStatus> nodes) {
    final pinnedAsync = ref.watch(pinnedPositionsProvider);
    final pinned = pinnedAsync.valueOrNull ?? const <String, Offset>{};
    final effectivePinned = {...pinned, ..._dragging};
    final positions = MeshLayout.compute(nodes, effectivePinned);

    return InteractiveViewer(
      minScale: 0.5,
      maxScale: 3.0,
      constrained: false,
      child: SizedBox(
        width: _canvasWidth,
        height: _canvasHeight,
        child: Stack(
          children: [
            AnimatedBuilder(
              animation: _phaseController,
              builder: (context, _) => CustomPaint(
                size: const Size(_canvasWidth, _canvasHeight),
                painter: MeshLinkPainter(
                  nodes: nodes,
                  positions: positions,
                  phase: _phaseController.value,
                ),
              ),
            ),
            // `MeshLayout.compute` only ever emits keys it read from `nodes`
            // (see mesh_layout.dart), so this lookup is always non-null.
            for (final entry in positions.entries)
              _buildCard(nodes[entry.key]!, entry.value),
          ],
        ),
      ),
    );
  }

  Widget _buildCard(NodeStatus node, Offset normalized) {
    final left = normalized.dx * _canvasWidth - _cardWidth / 2;
    final top = normalized.dy * _canvasHeight - _cardHeight / 2;
    final isDragging = _dragging.containsKey(node.nodeId);

    final content = GestureDetector(
      onPanStart: (_) => setState(() => _dragging[node.nodeId] = normalized),
      onPanUpdate: (details) => _onPanUpdate(node.nodeId, details),
      onPanEnd: (_) => _onPanEnd(node.nodeId),
      onLongPress: () => _confirmUnpin(node),
      child: MeshNodeCard(node: node, onTap: () => _showNodeDetail(node)),
    );

    return isDragging
        ? Positioned(left: left, top: top, width: _cardWidth, child: content)
        : AnimatedPositioned(
            duration: const Duration(milliseconds: 250),
            curve: Curves.easeInOut,
            left: left,
            top: top,
            width: _cardWidth,
            child: content,
          );
  }

  void _onPanUpdate(String nodeId, DragUpdateDetails details) {
    final current = _dragging[nodeId];
    if (current == null) return;
    setState(() {
      _dragging[nodeId] = Offset(
        (current.dx + details.delta.dx / _canvasWidth).clamp(0.0, 1.0),
        (current.dy + details.delta.dy / _canvasHeight).clamp(0.0, 1.0),
      );
    });
  }

  void _onPanEnd(String nodeId) {
    final finalOffset = _dragging[nodeId];
    setState(() => _dragging.remove(nodeId));
    if (finalOffset != null) {
      ref.read(pinnedPositionsProvider.notifier).pin(nodeId, finalOffset);
    }
  }

  Future<void> _confirmUnpin(NodeStatus node) async {
    final label = node.zone ?? node.nodeId;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Unpin node?'),
        content: Text('Unpin $label? It will return to automatic layout.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('Unpin'),
          ),
        ],
      ),
    );
    if (confirmed == true) {
      await ref.read(pinnedPositionsProvider.notifier).unpin(node.nodeId);
    }
  }

  void _showNodeDetail(NodeStatus node) {
    showModalBottomSheet<void>(
      context: context,
      builder: (_) => _NodeDetailSheet(node: node),
    );
  }
}

class _DegradedDataBanner extends StatelessWidget {
  const _DegradedDataBanner();

  @override
  Widget build(BuildContext context) => Container(
        width: double.infinity,
        color: AppColors.warning,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        child: const Text(
          'Mesh topology data not being published yet — update bridge/node firmware',
          style: TextStyle(color: Colors.white, fontSize: 12),
        ),
      );
}

/// Detail bottom sheet shown on card tap (spec §Node card tap → detail
/// sheet): MAC, zone, rank, parent, RSSI, battery (% and mV), sleepy,
/// online state, and last-seen time (same `HH:mm` format as `NodeListTile`).
class _NodeDetailSheet extends StatelessWidget {
  final NodeStatus node;
  const _NodeDetailSheet({required this.node});

  @override
  Widget build(BuildContext context) {
    final lastSeen = '${node.lastSeen.hour.toString().padLeft(2, '0')}:'
        '${node.lastSeen.minute.toString().padLeft(2, '0')}';
    final rows = <(String, String)>[
      ('MAC', node.nodeId),
      ('Zone', node.zone ?? '—'),
      ('Rank', node.meshRank?.toString() ?? '—'),
      ('Parent', node.parentId ?? '—'),
      ('RSSI', node.parentRssi != null ? '${node.parentRssi} dBm' : '—'),
      ('Battery', node.batteryPercent != null
          ? '${node.batteryPercent!.toStringAsFixed(0)}%'
          : '—'),
      ('Battery (mV)', node.batteryMv != null ? '${node.batteryMv} mV' : '—'),
      ('Sleepy', node.isSleepy == true ? 'Yes' : 'No'),
      ('Status', node.isOnline ? 'Online' : 'Offline'),
      ('Last seen', lastSeen),
    ];

    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            for (final (label, value) in rows)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 4),
                child: Row(
                  children: [
                    SizedBox(
                      width: 110,
                      child: Text(label, style: const TextStyle(fontWeight: FontWeight.bold)),
                    ),
                    Expanded(child: Text(value)),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }
}
