import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/cam_event.dart';

void main() {
  test('fromJson parses event_id and ts', () {
    final event = CamEvent.fromJson({'event_id': 'evt1', 'ts': 1700000000});
    expect(event.eventId, 'evt1');
    expect(event.ts, 1700000000);
  });

  test('timestamp converts unix seconds to a DateTime', () {
    final event = CamEvent.fromJson({'event_id': 'evt1', 'ts': 1700000000});
    expect(event.timestamp, DateTime.fromMillisecondsSinceEpoch(1700000000 * 1000));
  });
}
