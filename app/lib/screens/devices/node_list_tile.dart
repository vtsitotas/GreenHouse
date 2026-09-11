import 'package:flutter/material.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/screens/devices/battery_icon.dart';
import 'package:greenhouse_app/theme/app_colors.dart';
import 'package:greenhouse_app/utils/display_name.dart';
import 'package:greenhouse_app/utils/last_seen_format.dart';

class NodeListTile extends StatelessWidget {
  final NodeStatus node;

  /// User-chosen names, from `deviceNamesProvider`. Passed in rather than
  /// watched here so this stays a plain `StatelessWidget` (it is used inside
  /// lists that already have the map to hand).
  final Map<String, String> names;

  /// Long-press to rename. Null disables the affordance.
  final VoidCallback? onRename;

  /// De-enrol this sensor (revokes its key on the Pi). Null disables the
  /// affordance — the caller omits it for the bridge, which isn't a sensor
  /// and was never enrolled via /api/nodes.
  final VoidCallback? onRemove;

  const NodeListTile({
    required this.node,
    this.names = const {},
    this.onRename,
    this.onRemove,
    super.key,
  });

  @override
  Widget build(BuildContext context) {
    final title = displayNameFor(node.nodeId, names, zone: node.zone);
    // The MAC still has a job: it is what you match against the sticker on the
    // physical box. It just stops being the headline.
    final showsId = hasFriendlyName(node.nodeId, names, zone: node.zone);

    return ListTile(
      leading: Icon(Icons.sensors,
          color: node.isOnline ? AppColors.online : AppColors.offline),
      title: Text(title),
      subtitle: Text(
        showsId
            ? '${node.nodeId} · seen ${formatLastSeen(node.lastSeen)}'
            : 'Last seen: ${formatLastSeen(node.lastSeen)}',
      ),
      onLongPress: onRename,
      trailing: Row(mainAxisSize: MainAxisSize.min, children: [
        if (node.batteryPercent != null) ...[
          Icon(batteryIconFor(node.batteryPercent), size: 18),
          const SizedBox(width: 4),
          Text('${node.batteryPercent!.toStringAsFixed(0)} %'),
          const SizedBox(width: 12),
        ],
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
          decoration: BoxDecoration(
            color: node.isOnline ? AppColors.online : AppColors.offline,
            borderRadius: BorderRadius.circular(12),
          ),
          child: Text(node.isOnline ? 'Online' : 'Offline',
              style: const TextStyle(color: Colors.white, fontSize: 12)),
        ),
        if (onRemove != null)
          IconButton(
            icon: const Icon(Icons.delete_outline),
            tooltip: 'Remove sensor',
            onPressed: onRemove,
          ),
      ]),
    );
  }
}
