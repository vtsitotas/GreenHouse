/// Turning an internal id into something a greenhouse owner recognises.
///
/// Three sources of truth, in descending order of how much the user will
/// recognise them:
///   1. a name they typed themselves ("tomato bed")
///   2. the zone the device reports ("zone1" -> "Zone 1")
///   3. the tail of the raw id, as a last resort
///
/// The raw id is never *hidden* by these helpers — callers still show it as a
/// subtitle, because it is what you match against a label on the physical box.
/// It just stops being the headline.
library;

/// Human label for a zone key. `zone1` -> `Zone 1`; anything else is simply
/// capitalised, so a hand-named zone ("greenhouse-a") survives intact.
///
/// Lifted out of `ZoneCard` so the dashboard, the device list and the mesh map
/// cannot drift apart on what a zone is called.
String zoneLabel(String zone) {
  if (zone.isEmpty) return zone;
  if (zone.startsWith('zone')) {
    final suffix = zone.substring(4);
    if (suffix.isNotEmpty) return 'Zone $suffix';
  }
  return '${zone[0].toUpperCase()}${zone.substring(1)}';
}

/// Short fallback for an id with no name and no zone: the tail of a MAC is
/// what is printed on the device's own sticker, so the last 4 characters are
/// the part an owner can actually match against hardware in their hand.
String shortId(String id) =>
    id.length > 4 ? id.substring(id.length - 4) : id;

/// The name to show for [deviceId], given the user's saved [names] and
/// (optionally) the [zone] the device reports.
///
/// [zone] is nullable because it only exists once a `/mesh` message has
/// arrived; before that a node genuinely has nothing but its MAC.
String displayNameFor(
  String deviceId,
  Map<String, String> names, {
  String? zone,
}) {
  final custom = names[deviceId];
  if (custom != null && custom.trim().isNotEmpty) return custom.trim();
  if (zone != null && zone.trim().isNotEmpty) return zoneLabel(zone.trim());
  return shortId(deviceId);
}

/// True when [displayNameFor] would fall back to the raw id — lets a screen
/// decide whether showing the id *again* as a subtitle would just be noise.
bool hasFriendlyName(
  String deviceId,
  Map<String, String> names, {
  String? zone,
}) {
  final custom = names[deviceId];
  if (custom != null && custom.trim().isNotEmpty) return true;
  return zone != null && zone.trim().isNotEmpty;
}
