import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/utils/last_seen_format.dart';

void main() {
  final now = DateTime(2026, 8, 11, 14, 30);

  test('same calendar day returns a bare HH:mm', () {
    expect(formatLastSeen(DateTime(2026, 8, 11, 9, 5), now: now), '09:05');
  });

  test('yesterday is qualified with "Yesterday"', () {
    expect(formatLastSeen(DateTime(2026, 8, 10, 23, 59), now: now), 'Yesterday 23:59');
  });

  test('2-6 days ago is qualified with a day count', () {
    expect(formatLastSeen(DateTime(2026, 8, 8, 12, 0), now: now), '3 d ago, 12:00');
  });

  test('7+ days ago switches to a dated label', () {
    expect(formatLastSeen(DateTime(2026, 8, 1, 8, 0), now: now), 'Aug 1, 08:00');
  });

  test('a much older date still shows month/day (no year in the label)', () {
    expect(formatLastSeen(DateTime(2025, 12, 25, 8, 0), now: now), 'Dec 25, 08:00');
  });

  test('defaults to DateTime.now() when now is omitted', () {
    final justNow = DateTime.now();
    final expectedHm = '${justNow.hour.toString().padLeft(2, '0')}:'
        '${justNow.minute.toString().padLeft(2, '0')}';
    expect(formatLastSeen(justNow), expectedHm);
  });
}
