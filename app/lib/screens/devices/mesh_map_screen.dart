import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/providers/nodes_provider.dart';
import 'package:greenhouse_app/screens/common/friendly_error_view.dart';
import 'package:greenhouse_app/screens/common/rename_device_dialog.dart';
import 'package:greenhouse_app/screens/devices/mesh_map/mesh_layout.dart';
import 'package:greenhouse_app/screens/devices/mesh_map/mesh_link_painter.dart';
import 'package:greenhouse_app/screens/devices/mesh_map/mesh_node_card.dart';
import 'package:greenhouse_app/screens/devices/mesh_map/node_positions_store.dart';
import 'package:greenhouse_app/services/device_names_store.dart';
import 'package:greenhouse_app/theme/app_colors.dart';
import 'package:greenhouse_app/utils/display_name.dart';
import 'package:greenhouse_app/utils/last_seen_format.dart';
import 'package:greenhouse_app/utils/plain_language.dart';

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
      appBar: AppBar(title: const Text('Sensor network')),
      body: nodesAsync.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (error, _) => FriendlyErrorView(
          error: error,
          onRetry: () => ref.invalidate(nodesProvider),
        ),
        data: (nodes) {
          if (nodes.isEmpty) {
            return const Center(child: Text('No sensors found yet'));
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
    final names = ref.watch(deviceNamesProvider).valueOrNull ?? const {};
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
              _buildCard(nodes[entry.key]!, entry.value, names, nodes),
          ],
        ),
      ),
    );
  }

  Widget _buildCard(NodeStatus node, Offset normalized,
      Map<String, String> names, Map<String, NodeStatus> allNodes) {
    final left = normalized.dx * _canvasWidth - _cardWidth / 2;
    final top = normalized.dy * _canvasHeight - _cardHeight / 2;
    final isDragging = _dragging.containsKey(node.nodeId);

    final content = GestureDetector(
      onPanStart: (_) => setState(() => _dragging[node.nodeId] = normalized),
      onPanUpdate: (details) => _onPanUpdate(node.nodeId, details),
      onPanEnd: (_) => _onPanEnd(node.nodeId),
      // Long-press is already spoken for here (unpin); renaming lives in the
      // detail sheet instead, reachable by tapping the card.
      onLongPress: () => _confirmUnpin(node),
      child: MeshNodeCard(
        node: node,
        names: names,
        onTap: () => _showNodeDetail(node, allNodes),
      ),
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
    final names = ref.read(deviceNamesProvider).valueOrNull ?? const {};
    final label = displayNameFor(node.nodeId, names, zone: node.zone);
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Move back automatically?'),
        content: Text(
            'Let the app position $label again instead of keeping it where '
            'you placed it.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('Move back'),
          ),
        ],
      ),
    );
    if (confirmed == true) {
      await ref.read(pinnedPositionsProvider.notifier).unpin(node.nodeId);
    }
  }

  void _showNodeDetail(NodeStatus node, Map<String, NodeStatus> allNodes) {
    final names = ref.read(deviceNamesProvider).valueOrNull ?? const {};
    // Resolved here, where the whole node map is in scope: naming the relaying
    // sensor ("passes through Tomato bed") only helps if it uses that
    // sensor's own zone as a fallback, which needs more than the tapped node.
    final parentId = node.parentId;
    final parentName = parentId == null
        ? null
        : displayNameFor(parentId, names, zone: allNodes[parentId]?.zone);
    showModalBottomSheet<void>(
      context: context,
      builder: (_) => _NodeDetailSheet(
        node: node,
        names: names,
        parentName: parentName,
        // Long-press on the card is taken by unpin, so renaming is offered
        // here instead — the sheet is also where a user goes to work out
        // *which* device this is, which is exactly when they want to name it.
        onRename: () {
          Navigator.of(context).pop();
          showRenameDeviceDialog(
            context,
            ref,
            deviceId: node.nodeId,
            currentAutoLabel:
                displayNameFor(node.nodeId, const {}, zone: node.zone),
          );
        },
      ),
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
        '$total sensor${total == 1 ? '' : 's'} · $online working · '
        '$offline not responding',
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

  // Words, not dBm -- the legend has to be readable by whoever is standing in
  // the greenhouse. The exact numbers are one tap away in any node's
  // "Technical details".
  static const _entries = [
    (AppColors.online, 'Strong signal'),
    (Colors.amber, 'Fair signal'),
    (Colors.deepOrange, 'Weak signal'),
    (Colors.grey, 'Not responding'),
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
        // Names the symptom the user can see, then the one action that fixes
        // it -- the old copy ("update bridge/node firmware") named neither a
        // symptom nor a step anyone could follow.
        child: const Text(
          "Your sensors haven't reported how they're connected yet, so the "
          'map can only show them as a list. They need updating — see the '
          'setup guide.',
          style: TextStyle(color: Colors.white, fontSize: 12),
        ),
      );
}

/// Detail bottom sheet shown on card tap.
///
/// Two tiers on purpose: what a grower needs in order to *act* (is it working,
/// when was it last heard from, how is it connected, what do I do if it's
/// offline), and — collapsed underneath — the raw mesh fields that only matter
/// when debugging on the bench. Nothing is removed; it is just no longer the
/// first thing you have to read past.
class _NodeDetailSheet extends StatelessWidget {
  final NodeStatus node;
  final Map<String, String> names;

  /// Already-resolved friendly name of the relaying sensor, if any — the
  /// caller has the full node map and can fall back to the parent's own zone,
  /// which this widget could not do from `node` alone.
  final String? parentName;

  final VoidCallback? onRename;

  const _NodeDetailSheet({
    required this.node,
    this.names = const {},
    this.parentName,
    this.onRename,
  });

  @override
  Widget build(BuildContext context) {
    final plainRows = <(String, String)>[
      ('Status', node.isOnline ? 'Working' : 'Not responding'),
      ('Last heard from', formatLastSeen(node.lastSeen)),
      ('Connection', connectionLabel(node, parentName: parentName)),
      if (node.parentRssi != null) ('Signal', signalLabel(node.parentRssi)),
      if (node.batteryPercent != null)
        ('Battery', '${batteryLabel(node.batteryPercent)} '
            '(${node.batteryPercent!.toStringAsFixed(0)}%)'),
      if (node.isSleepy != null) ('Power mode', sleepLabel(node.isSleepy)),
    ];

    // Everything an engineer would want and a grower would not. Kept verbatim
    // -- these are the values you compare against `mosquitto_sub` output when
    // something looks wrong.
    final technicalRows = <(String, String)>[
      ('Device ID', node.nodeId),
      ('Zone', node.zone ?? '—'),
      ('Mesh rank', node.meshRank?.toString() ?? '—'),
      ('Parent ID', node.parentId ?? '—'),
      ('RSSI', node.parentRssi != null ? '${node.parentRssi} dBm' : '—'),
      ('Battery (mV)', node.batteryMv != null ? '${node.batteryMv} mV' : '—'),
      ('Sleepy flag', node.isSleepy == null
          ? '—'
          : (node.isSleepy! ? 'true' : 'false')),
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
            Row(
              children: [
                Expanded(
                  child: Text(
                    displayNameFor(node.nodeId, names, zone: node.zone),
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
                if (onRename != null)
                  TextButton.icon(
                    onPressed: onRename,
                    icon: const Icon(Icons.edit_outlined, size: 18),
                    label: const Text('Rename'),
                  ),
              ],
            ),
            const Divider(),
            for (final (label, value) in plainRows) _DetailRow(label, value),
            if (!node.isOnline) ...[
              const SizedBox(height: 12),
              Text('What to try',
                  style: Theme.of(context).textTheme.titleSmall),
              const SizedBox(height: 4),
              for (final step in kOfflineNodeSteps)
                Padding(
                  padding: const EdgeInsets.only(bottom: 4),
                  child: Text('•  $step',
                      style: Theme.of(context).textTheme.bodySmall),
                ),
            ],
            const SizedBox(height: 8),
            ExpansionTile(
              title: const Text('Technical details'),
              tilePadding: EdgeInsets.zero,
              childrenPadding: const EdgeInsets.only(bottom: 8),
              expandedCrossAxisAlignment: CrossAxisAlignment.start,
              children: [
                for (final (label, value) in technicalRows)
                  _DetailRow(label, value),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _DetailRow extends StatelessWidget {
  final String label;
  final String value;
  const _DetailRow(this.label, this.value);

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(
              width: 120,
              child: Text(label,
                  style: const TextStyle(fontWeight: FontWeight.bold)),
            ),
            Expanded(child: Text(value)),
          ],
        ),
      );
}
