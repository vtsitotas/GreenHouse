import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:greenhouse_app/models/node_status.dart';
import 'package:greenhouse_app/providers/connection_provider.dart';

final nodesProvider = StreamProvider<Map<String, NodeStatus>>((ref) {
  ref.watch(connectOnStartProvider);
  return ref.watch(repositoryProvider).nodes;
});

/// The most recent moment ANY sensor was confirmed alive, or null when none
/// has been.
///
/// Drives the "showing data from …" line on the offline banner. Null is a
/// real answer, not a missing one: `NodeStatus.lastSeen` is null whenever the
/// only thing the app has heard is a retained MQTT replay, which carries no
/// timestamp — so this must not fall back to `DateTime.now()`, which is
/// exactly the lie the retained-`ts` work removed.
final lastSensorSightingProvider = Provider<DateTime?>((ref) {
  final nodes = ref.watch(nodesProvider).valueOrNull;
  if (nodes == null) return null;
  DateTime? newest;
  for (final node in nodes.values) {
    final seen = node.lastSeen;
    if (seen == null) continue;
    if (newest == null || seen.isAfter(newest)) newest = seen;
  }
  return newest;
});
