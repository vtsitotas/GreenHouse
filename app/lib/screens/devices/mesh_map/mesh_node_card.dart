import 'package:flutter/material.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/screens/devices/battery_icon.dart';
import 'package:greenhouse_app/theme/app_colors.dart';

/// One node's card on the mesh map (spec §Node card). Self-contained: no
/// provider imports — the caller supplies the `NodeStatus` and an optional
/// tap callback (a detail bottom sheet is the screen's job, Task 5).
class MeshNodeCard extends StatelessWidget {
  static const double width = 120;

  final NodeStatus node;
  final VoidCallback? onTap;

  const MeshNodeCard({required this.node, this.onTap, super.key});

  bool get _isBridge => node.meshRank == 0;

  /// "How is this node reaching the bridge" at a glance — null (hidden) for
  /// the bridge's own card and for a node with no `/mesh` data yet. `rank`
  /// already *is* hop-count-from-bridge in this mesh (one bridge, rank
  /// assigned by hop distance — see docs/technical/03-mesh-routing.md), so
  /// rank 1 means directly reachable without cross-referencing `parentId`
  /// against the bridge's own node id.
  String? get _hopLabel {
    final rank = node.meshRank;
    if (rank == null || rank == 0) return null;
    return rank == 1 ? 'Direct' : '$rank hops';
  }

  String get _title {
    final zone = node.zone;
    if (zone != null && zone.isNotEmpty) return zone;
    final id = node.nodeId;
    return id.length > 4 ? id.substring(id.length - 4) : id;
  }

  @override
  Widget build(BuildContext context) {
    final roleColor = node.isOnline ? AppColors.online : AppColors.offline;

    final card = SizedBox(
      width: width,
      child: Card(
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(12),
          child: Padding(
            padding: const EdgeInsets.all(8),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(_isBridge ? Icons.router : Icons.sensors, color: roleColor),
                const SizedBox(height: 4),
                Text(
                  _title,
                  style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 12),
                  overflow: TextOverflow.ellipsis,
                  maxLines: 1,
                ),
                Text(
                  node.nodeId,
                  style: const TextStyle(fontSize: 10, color: Colors.grey),
                  overflow: TextOverflow.ellipsis,
                  maxLines: 1,
                ),
                const SizedBox(height: 4),
                Wrap(
                  alignment: WrapAlignment.center,
                  spacing: 6,
                  runSpacing: 2,
                  children: [
                    if (node.batteryPercent != null)
                      Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(batteryIconFor(node.batteryPercent), size: 14),
                          const SizedBox(width: 2),
                          Text(
                            '${node.batteryPercent!.toStringAsFixed(0)}%',
                            style: const TextStyle(fontSize: 10),
                          ),
                        ],
                      ),
                    if (node.parentRssi != null)
                      Text('${node.parentRssi} dBm', style: const TextStyle(fontSize: 10)),
                    if (_hopLabel != null)
                      Text(_hopLabel!,
                          style: const TextStyle(fontSize: 10, fontStyle: FontStyle.italic)),
                    if (node.isSleepy == true)
                      const Tooltip(
                        message: 'Deep-sleep node',
                        child: Icon(Icons.nightlight_round, size: 14),
                      ),
                  ],
                ),
                if (!node.isOnline) ...[
                  const SizedBox(height: 4),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                    decoration: BoxDecoration(
                      color: AppColors.offline,
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: const Text(
                      'Offline',
                      style: TextStyle(color: Colors.white, fontSize: 10),
                    ),
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );

    return node.isOnline ? card : Opacity(opacity: 0.45, child: card);
  }
}
