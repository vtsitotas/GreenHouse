import 'package:flutter/material.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/screens/devices/battery_icon.dart';
import 'package:greenhouse_app/theme/app_colors.dart';

class NodeListTile extends StatelessWidget {
  final NodeStatus node;
  const NodeListTile({required this.node, super.key});

  @override
  Widget build(BuildContext context) => ListTile(
        leading: Icon(Icons.sensors, color: node.isOnline ? AppColors.online : AppColors.offline),
        title: Text(node.nodeId),
        subtitle: Text('Last seen: ${node.lastSeen.hour.toString().padLeft(2,'0')}:${node.lastSeen.minute.toString().padLeft(2,'0')}'),
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
        ]),
      );
}
