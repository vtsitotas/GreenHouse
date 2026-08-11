/// Human-readable "last seen" label. Bare `HH:mm` when [lastSeen] falls on
/// the same calendar day as [now] (defaults to `DateTime.now()`) — qualified
/// with a day once it doesn't, so a node that has been offline since
/// yesterday (or longer) doesn't read as "seen a few minutes ago" just
/// because its clock-time happens to be close to the current one.
///
/// Null means the app has never actually confirmed this node alive — e.g.
/// everything it knows so far came from a retained MQTT redelivery, which
/// carries no timestamp of its own (see NodeStatus.lastSeen) — so this
/// returns "Unknown" rather than a fabricated time.
String formatLastSeen(DateTime? lastSeen, {DateTime? now}) {
  if (lastSeen == null) return 'Unknown';
  final n = now ?? DateTime.now();
  final hm = '${lastSeen.hour.toString().padLeft(2, '0')}:'
      '${lastSeen.minute.toString().padLeft(2, '0')}';

  final today = DateTime(n.year, n.month, n.day);
  final seenDay = DateTime(lastSeen.year, lastSeen.month, lastSeen.day);
  final dayDiff = today.difference(seenDay).inDays;

  if (dayDiff == 0) return hm;
  if (dayDiff == 1) return 'Yesterday $hm';
  if (dayDiff > 1 && dayDiff < 7) return '$dayDiff d ago, $hm';

  const months = [
    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
  ];
  final dateLabel = '${months[lastSeen.month - 1]} ${lastSeen.day}';
  // Negative dayDiff (lastSeen is in the future) shouldn't normally happen —
  // clock skew between the Pi and the phone is the only realistic cause —
  // but falls through to the same dated format rather than a nonsensical
  // "N d ago", so a skewed clock is visibly odd rather than silently wrong.
  return '$dateLabel, $hm';
}
