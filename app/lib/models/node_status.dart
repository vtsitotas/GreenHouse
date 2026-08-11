import 'dart:convert';

/// Which MQTT topic a `NodeStatus` event was derived from — lets the
/// repository merge own `isOnline` exclusively to `/status` events while
/// still folding `/battery` and `/mesh` facets into the same node entry.
enum NodeStatusSource { status, battery, mesh }

class NodeStatus {
  final String nodeId;
  final bool isOnline;
  final double? batteryPercent;
  // Null means "never confirmed live" -- e.g. everything the app knows about
  // this node so far came from a *retained* MQTT redelivery (every topic is
  // replayed in full on every (re)connect), which carries no timestamp of
  // its own. Treating that arrival time as "seen now" is exactly the bug
  // this nullability exists to prevent: it would make every node look
  // freshly seen on every app launch/reconnect, whatever actually happened.
  final DateTime? lastSeen;
  // Mesh topology fields (from `/mesh`, all nullable — absent until a
  // message arrives; `parentId == null` + `meshRank == 0` means the bridge).
  final String? parentId;
  final int? meshRank;
  final int? parentRssi;
  final bool? isSleepy;
  final String? zone;
  final int? batteryMv;
  final NodeStatusSource source;
  // True when this specific event was a retained MQTT redelivery rather
  // than a live publish -- e.g. every topic replayed on (re)connect. Only
  // the repository merge (greenhouse_repository.dart) reads this, to decide
  // whether `lastSeen` below is trustworthy evidence of a live sighting.
  final bool retain;

  const NodeStatus({
    required this.nodeId,
    required this.isOnline,
    this.batteryPercent,
    this.lastSeen,
    this.parentId,
    this.meshRank,
    this.parentRssi,
    this.isSleepy,
    this.zone,
    this.batteryMv,
    this.source = NodeStatusSource.status,
    this.retain = false,
  });

  /// The arrival time of a message, but only when arrival is real evidence
  /// the node is alive right now. A retained message is a replay of whatever
  /// the broker already held — it says nothing about *when* — so it yields
  /// null ("unknown") rather than a fabricated sighting.
  static DateTime? _seenOnArrival({required bool retain}) =>
      retain ? null : DateTime.now();

  factory NodeStatus.fromMqttStatus(String nodeId, String payload, {bool retain = false}) {
    final isOnline = payload.trim() == 'online';
    return NodeStatus(
      nodeId: nodeId,
      isOnline: isOnline,
      // An offline status is live evidence of *absence*, not presence: the
      // payload is the bare string the Pi publishes the instant it gives up
      // on a node (see serial_bridge.py), carrying no timestamp of its own.
      // Dating a node by it would show one that just dropped as "seen now".
      lastSeen: isOnline ? _seenOnArrival(retain: retain) : null,
      source: NodeStatusSource.status,
      retain: retain,
    );
  }

  factory NodeStatus.fromMqttBattery(String nodeId, String payload, {bool retain = false}) =>
      NodeStatus(
        nodeId: nodeId,
        isOnline: true,
        batteryPercent: double.tryParse(payload.trim()),
        lastSeen: _seenOnArrival(retain: retain),
        source: NodeStatusSource.battery,
        retain: retain,
      );

  // Liveness-hint semantics match fromMqttBattery: a `/mesh` message implies
  // the node is alive, but `isOnline` ownership stays with `/status` in the
  // repository merge (Task 3) — this factory's `true` is just the default
  // for a first-ever event.
  factory NodeStatus.fromMqttMesh(String nodeId, String jsonPayload, {bool retain = false}) {
    final json = jsonDecode(jsonPayload) as Map<String, dynamic>;
    // `ts` (epoch seconds) is stamped by serial_bridge.py at the moment the
    // sighting actually happened. Unlike arrival time it stays true through
    // a retained replay, which is the only way a reconnecting app can date a
    // node it wasn't running to witness. Absent on units still running
    // pre-2026-08-11 Pi code, hence the fallback.
    final ts = (json['ts'] as num?)?.toInt();
    return NodeStatus(
      nodeId: nodeId,
      isOnline: true,
      lastSeen: ts != null
          ? DateTime.fromMillisecondsSinceEpoch(ts * 1000)
          : _seenOnArrival(retain: retain),
      parentId: json['parent'] as String?,
      meshRank: (json['rank'] as num?)?.toInt(),
      parentRssi: (json['rssi'] as num?)?.toInt(),
      isSleepy: json['sleepy'] as bool?,
      zone: json['zone'] as String?,
      batteryMv: (json['battery_mv'] as num?)?.toInt(),
      source: NodeStatusSource.mesh,
      retain: retain,
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
        retain: retain,
      );

  /// Unlike [copyWith] (whose `?? this.x` pattern treats a null argument as
  /// "keep the existing value", for ergonomic partial updates), this always
  /// sets [lastSeen] to exactly what's passed — including null. "We don't
  /// actually know" is a real, distinct state from "keep whatever we had",
  /// and copyWith has no way to express clearing a field.
  NodeStatus withLastSeen(DateTime? lastSeen) => NodeStatus(
        nodeId: nodeId,
        isOnline: isOnline,
        batteryPercent: batteryPercent,
        lastSeen: lastSeen,
        parentId: parentId,
        meshRank: meshRank,
        parentRssi: parentRssi,
        isSleepy: isSleepy,
        zone: zone,
        batteryMv: batteryMv,
        source: source,
        retain: retain,
      );
}
