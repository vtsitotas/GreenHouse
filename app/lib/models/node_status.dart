import 'dart:convert';

/// Which MQTT topic a `NodeStatus` event was derived from — lets the
/// repository merge own `isOnline` exclusively to `/status` events while
/// still folding `/battery` and `/mesh` facets into the same node entry.
enum NodeStatusSource { status, battery, mesh }

class NodeStatus {
  final String nodeId;
  final bool isOnline;
  final double? batteryPercent;
  final DateTime lastSeen;
  // Mesh topology fields (from `/mesh`, all nullable — absent until a
  // message arrives; `parentId == null` + `meshRank == 0` means the bridge).
  final String? parentId;
  final int? meshRank;
  final int? parentRssi;
  final bool? isSleepy;
  final String? zone;
  final int? batteryMv;
  final NodeStatusSource source;

  const NodeStatus({
    required this.nodeId,
    required this.isOnline,
    this.batteryPercent,
    required this.lastSeen,
    this.parentId,
    this.meshRank,
    this.parentRssi,
    this.isSleepy,
    this.zone,
    this.batteryMv,
    this.source = NodeStatusSource.status,
  });

  factory NodeStatus.fromMqttStatus(String nodeId, String payload) => NodeStatus(
        nodeId: nodeId,
        isOnline: payload.trim() == 'online',
        lastSeen: DateTime.now(),
        source: NodeStatusSource.status,
      );

  factory NodeStatus.fromMqttBattery(String nodeId, String payload) => NodeStatus(
        nodeId: nodeId,
        isOnline: true,
        batteryPercent: double.tryParse(payload.trim()),
        lastSeen: DateTime.now(),
        source: NodeStatusSource.battery,
      );

  // Liveness-hint semantics match fromMqttBattery: a `/mesh` message implies
  // the node is alive, but `isOnline` ownership stays with `/status` in the
  // repository merge (Task 3) — this factory's `true` is just the default
  // for a first-ever event.
  factory NodeStatus.fromMqttMesh(String nodeId, String jsonPayload) {
    final json = jsonDecode(jsonPayload) as Map<String, dynamic>;
    return NodeStatus(
      nodeId: nodeId,
      isOnline: true,
      lastSeen: DateTime.now(),
      parentId: json['parent'] as String?,
      meshRank: (json['rank'] as num?)?.toInt(),
      parentRssi: (json['rssi'] as num?)?.toInt(),
      isSleepy: json['sleepy'] as bool?,
      zone: json['zone'] as String?,
      batteryMv: (json['battery_mv'] as num?)?.toInt(),
      source: NodeStatusSource.mesh,
    );
  }

  NodeStatus copyWith({
    bool? isOnline,
    double? batteryPercent,
    DateTime? lastSeen,
    String? parentId,
    int? meshRank,
    int? parentRssi,
    bool? isSleepy,
    String? zone,
    int? batteryMv,
  }) =>
      NodeStatus(
        nodeId: nodeId,
        isOnline: isOnline ?? this.isOnline,
        batteryPercent: batteryPercent ?? this.batteryPercent,
        lastSeen: lastSeen ?? this.lastSeen,
        parentId: parentId ?? this.parentId,
        meshRank: meshRank ?? this.meshRank,
        parentRssi: parentRssi ?? this.parentRssi,
        isSleepy: isSleepy ?? this.isSleepy,
        zone: zone ?? this.zone,
        batteryMv: batteryMv ?? this.batteryMv,
        source: source,
      );
}
