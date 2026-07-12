import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/cam_status.dart';

void main() {
  test('fromJson parses online status with last event', () {
    final status = CamStatus.fromJson({
      'online': true,
      'last_seen': 1700000000.0,
      'ip': '192.168.1.50',
      'last_event': {'event_id': 'evt1', 'ts': 1700000000},
    });
    expect(status.online, isTrue);
    expect(status.ip, '192.168.1.50');
    expect(status.lastEvent?.eventId, 'evt1');
  });

  test('fromJson handles a null last_event (no motion yet)', () {
    final status = CamStatus.fromJson({
      'online': false, 'last_seen': null, 'ip': null, 'last_event': null,
    });
    expect(status.online, isFalse);
    expect(status.lastEvent, isNull);
  });
}
