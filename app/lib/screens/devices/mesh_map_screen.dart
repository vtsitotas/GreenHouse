import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/providers/nodes_provider.dart';
import 'package:greenhouse_app/screens/devices/mesh_map/mesh_layout.dart';
import 'package:greenhouse_app/screens/devices/mesh_map/mesh_link_painter.dart';
import 'package:greenhouse_app/screens/devices/mesh_map/mesh_node_card.dart';
import 'package:greenhouse_app/screens/devices/mesh_map/node_positions_store.dart';
import 'package:greenhouse_app/theme/app_colors.dart';
import 'package:greenhouse_app/utils/last_seen_format.dart';

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
              _MeshSummaryBar(nodes: nodes),
              Expanded(
                child: Stack(
                  children: [
                    _buildCanvas(nodes),
                    // Fixed to the screen, not the pannable canvas -- a
                    // legend that scrolled off with the map it's explaining
                    // would defeat the point.
                    const Positioned(right: 12, bottom: 12, child: _LinkQualityLegend()),
                  ],
                ),
              ),
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

    // A stable key AND a single, unchanging widget type across every
    // rebuild are both required here -- not just style. Once a drag starts,
    // effectivePinned (in _buildCanvas) immediately includes this node, so
    // MeshLayout.compute's insertion order shifts it (pinned nodes are
    // inserted where they're first encountered, unpinned ones only later,
    // bucketed by rank) -- without a key, Flutter's unkeyed positional
    // reconciliation can attribute this Element to a different logical
    // node after such a reorder. Worse, switching the widget class itself
    // (Positioned vs AnimatedPositioned, as this used to do based on
    // isDragging) makes Element.canUpdate return false even when the slot
    // does line up, so Flutter tears down and recreates the whole subtree
    // -- including the live GestureRecognizer tracking the pointer that's
    // mid-drag. The recognizer dies right after onPanStart's first
    // setState, so onPanUpdate/onPanEnd for that same touch never arrive:
    // pinning silently captured only the pre-drag position, making the
    // pin indistinguishable from auto-layout and "Unpin" look like a no-op.
    // Animating via `duration` instead of switching types keeps one
    // consistent Element (and its recognizer) alive through the whole
    // gesture, whatever the map's iteration order does.
    return AnimatedPositioned(
      key: ValueKey(node.nodeId),
      duration: isDragging ? Duration.zero : const Duration(milliseconds: 250),
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

/// "How many nodes are there and how many can I actually reach right now" at
/// a glance, without counting cards on the canvas by eye.
class _MeshSummaryBar extends StatelessWidget {
  final Map<String, NodeStatus> nodes;
  const _MeshSummaryBar({required this.nodes});

  @override
  Widget build(BuildContext context) {
    final total = nodes.length;
    final online = nodes.values.where((n) => n.isOnline).length;
    final offline = total - online;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 6, 16, 6),
      child: Text(
        '$total node${total == 1 ? '' : 's'} · $online online · $offline offline',
        style: Theme.of(context).textTheme.labelSmall,
      ),
    );
  }
}

/// What the link colors/dash pattern mean (spec §Link quality mapping,
/// `mesh_link_painter.dart`'s `linkQualityOf`) — without this, the map shows
/// colored/dashed lines with nothing explaining them to anyone but whoever
/// wrote the painter.
class _LinkQualityLegend extends StatelessWidget {
  const _LinkQualityLegend();

  static const _entries = [
    (AppColors.online, 'Good (≥ −60 dBm)'),
    (Colors.amber, 'Fair (−61…−75 dBm)'),
    (Colors.deepOrange, 'Weak (< −75 dBm)'),
    (Colors.grey, 'Stale / offline (dashed)'),
  ];

  @override
  Widget build(BuildContext context) => Card(
        margin: EdgeInsets.zero,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              for (final (color, label) in _entries)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 2),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Container(width: 14, height: 3, color: color),
                      const SizedBox(width: 6),
                      Text(label, style: const TextStyle(fontSize: 10)),
                    ],
                  ),
                ),
            ],
          ),
        ),
      );
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

/// Human-readable answer to "how is this node reaching the bridge?" — the
/// raw `Rank`/`Parent` rows stay too (rank is the actual field the mesh and
/// the layout engine key off), but "rank 2" doesn't tell a reader "that's
/// two hops away through a relay" at a glance the way this does.
String _connectionLabel(NodeStatus node) {
  final rank = node.meshRank;
  if (rank == null) return 'Unknown — no mesh data yet';
  if (rank == 0) return 'This is the bridge';
  if (rank == 1) return 'Direct to bridge';
  return 'Relayed — $rank hops (via ${node.parentId ?? "unknown"})';
}

/// Detail bottom sheet shown on card tap (spec §Node card tap → detail
/// sheet): MAC, zone, connection (direct/relayed/unknown), rank, parent,
/// RSSI, battery (% and mV), sleepy, online state, and last-seen time (same
/// format as `NodeListTile`, qualified with a date once it isn't today).
class _NodeDetailSheet extends StatelessWidget {
  final NodeStatus node;
  const _NodeDetailSheet({required this.node});

  @override
  Widget build(BuildContext context) {
    final rows = <(String, String)>[
      ('MAC', node.nodeId),
      ('Zone', node.zone ?? '—'),
      ('Connection', _connectionLabel(node)),
      ('Rank', node.meshRank?.toString() ?? '—'),
      ('Parent', node.parentId ?? '—'),
      ('RSSI', node.parentRssi != null ? '${node.parentRssi} dBm' : '—'),
      ('Battery', node.batteryPercent != null
          ? '${node.batteryPercent!.toStringAsFixed(0)}%'
          : '—'),
      ('Battery (mV)', node.batteryMv != null ? '${node.batteryMv} mV' : '—'),
      ('Sleepy', node.isSleepy == true ? 'Yes' : 'No'),
      ('Status', node.isOnline ? 'Online' : 'Offline'),
      ('Last seen', formatLastSeen(node.lastSeen)),
    ];

    // Scrollable so the full row list stays reachable when the sheet's max
    // height is smaller than the content (short screens, large text scale).
    return SafeArea(
      child: SingleChildScrollView(
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
