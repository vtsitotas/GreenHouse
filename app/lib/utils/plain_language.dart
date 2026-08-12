/// Turning the mesh's engineering units into words a grower can act on.
///
/// Nothing here hides data — the raw dBm/rank/mV still appear in the detail
/// sheet's "Technical details" section. What these do is answer the question
/// the numbers *don't*: is −72 dBm a problem or not?
///
/// The thresholds live here and are imported by everything that needs them
/// (the link painter's colours, the battery icon), so a card, the map legend
/// and a link colour can never disagree about what "Weak" means.
library;

import 'package:greenhouse_app/models/connection_status.dart';
import 'package:greenhouse_app/models/node_status.dart';

// ── Signal thresholds ───────────────────────────────────────────────────────
// Shared with `linkQualityOf` in mesh_map/mesh_link_painter.dart. Changing a
// number here changes the colours, the card labels and the legend together;
// that coupling is the point.

/// At or above this (dBm), a link is healthy.
const int kRssiGoodDbm = -60;

/// At or above this (but below [kRssiGoodDbm]), a link still works but has
/// little headroom.
const int kRssiFairDbm = -75;

// ── Battery thresholds ──────────────────────────────────────────────────────
// Shared with `batteryIconFor` in screens/devices/battery_icon.dart.

const double kBatteryFullPct = 80;
const double kBatteryGoodPct = 50;
const double kBatteryLowPct = 20;

/// How good the radio link to this node's parent is.
///
/// Null RSSI means no `/mesh` message has arrived yet — genuinely unknown,
/// not zero.
String signalLabel(int? rssi) {
  if (rssi == null) return 'Unknown';
  if (rssi >= kRssiGoodDbm) return 'Strong';
  if (rssi >= kRssiFairDbm) return 'Fair';
  return 'Weak';
}

/// Battery level in words. Mirrors [batteryIconFor]'s bands exactly so the
/// icon and the text never tell different stories.
String batteryLabel(double? percent) {
  if (percent == null) return 'Unknown';
  if (percent > kBatteryFullPct) return 'Full';
  if (percent > kBatteryGoodPct) return 'Good';
  if (percent > kBatteryLowPct) return 'Low';
  return 'Replace soon';
}

/// Whether this node sleeps between readings.
///
/// "Sleepy" is firmware vocabulary for a deliberate power-saving cycle, but it
/// reads like a fault. It is the opposite: it is why the battery lasts.
String sleepLabel(bool? isSleepy) {
  if (isSleepy == null) return 'Unknown';
  return isSleepy ? 'Battery saver' : 'Always on';
}

/// How this node's readings reach the hub.
///
/// `meshRank` already *is* hop-count-from-the-hub in this mesh (one hub, rank
/// assigned by hop distance — docs/technical/03-mesh-routing.md), so rank 1
/// means directly reachable without cross-referencing `parentId`.
///
/// [parentName] lets the caller pass an already-resolved friendly name for
/// the relaying node; without it the raw parent id is used, which is better
/// than nothing but worse than a name.
String connectionLabel(NodeStatus node, {String? parentName}) {
  final rank = node.meshRank;
  if (rank == null) return 'Not connected yet';
  if (rank == 0) return 'This is the hub';
  if (rank == 1) return 'Talks straight to the hub';
  final via = parentName ?? node.parentId;
  return via == null
      ? 'Passes through $rank other sensors'
      : 'Passes through $via ($rank steps to the hub)';
}

/// Short form of [connectionLabel] for the node card, where there is room for
/// two words at most. Null means "don't show a label at all" — true for the
/// hub itself and for a node with no mesh data yet.
String? shortConnectionLabel(NodeStatus node) {
  final rank = node.meshRank;
  if (rank == null || rank == 0) return null;
  return rank == 1 ? 'Direct' : '$rank steps';
}

/// How the app is currently reaching the greenhouse.
///
/// The enum names describe the *route* ("local", "remote"); a user cares
/// whether it is working and roughly why, so these say that instead.
String connectionStatusLabel(ConnectionStatus status) => switch (status) {
      ConnectionStatus.local => 'Connected on your home network',
      ConnectionStatus.remote => 'Connected from away',
      ConnectionStatus.reconnecting => 'Reconnecting…',
      ConnectionStatus.offline => 'Not connected',
    };

/// What to actually do about an offline node, in the order worth trying.
///
/// A red "Offline" pill tells a grower something is wrong but not what to do
/// next, which is where they get stuck. Ordered cheapest-first: check power
/// before moving hardware.
const List<String> kOfflineNodeSteps = [
  'Check the sensor has power (battery charged, or plugged in)',
  'If it runs on battery, it may just be asleep — give it a few minutes',
  'Move it closer to the hub, or to another sensor, if the signal was weak',
];
