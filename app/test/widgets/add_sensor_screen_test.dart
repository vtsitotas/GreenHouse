// app/test/widgets/add_sensor_screen_test.dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/sensor_enrolment.dart';
import 'package:greenhouse_app/screens/devices/add_sensor_screen.dart';

void main() {
  const sensor = SensorEnrolment(
      mac: '206EF16C9DB0', key: '000102030405060708090a0b0c0d0e0f');

  Widget host({required Future<void> Function(String, String, bool) onSubmit}) =>
      MaterialApp(
          home: AddSensorScreen(scanned: sensor, onSubmit: onSubmit));

  testWidgets('tells the owner to hold the sensor near the hub', (t) async {
    await t.pumpWidget(host(onSubmit: (_, __, ___) async {}));
    expect(find.textContaining('near the hub'), findsOneWidget);
  });

  testWidgets('will not submit without a zone name', (t) async {
    var submitted = false;
    await t.pumpWidget(host(onSubmit: (_, __, ___) async { submitted = true; }));
    await t.tap(find.text('Add sensor'));
    await t.pump();
    expect(submitted, isFalse);
    expect(find.textContaining('Give this sensor a place'), findsOneWidget);
  });

  testWidgets('submits the zone, name and battery choice', (t) async {
    String? zone;
    bool? sleepy;
    await t.pumpWidget(host(onSubmit: (z, _, s) async { zone = z; sleepy = s; }));
    await t.enterText(find.byKey(const Key('zoneField')), 'Basil bed');
    await t.tap(find.byKey(const Key('batteryToggle')));
    await t.pump();
    await t.tap(find.text('Add sensor'));
    await t.pumpAndSettle();
    expect(zone, 'Basil bed');
    expect(sleepy, isFalse);
  });

  testWidgets('shows a plain-language error when enrolment fails', (t) async {
    await t.pumpWidget(host(onSubmit: (_, __, ___) async {
      throw Exception('boom');
    }));
    await t.enterText(find.byKey(const Key('zoneField')), 'Basil bed');
    await t.tap(find.text('Add sensor'));
    await t.pumpAndSettle();
    expect(find.textContaining("couldn't add"), findsOneWidget);
  });
}
