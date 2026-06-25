import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/actuator_state.dart';
import 'package:greenhouse_app/screens/control/actuator_toggle.dart';

void main() {
  testWidgets('ActuatorToggle switch is ON when state.isOn is true', (tester) async {
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: ActuatorToggle(
      state: const ActuatorState(actuatorId: 'pump1', isOn: true),
      onToggle: (_) {},
    ))));
    final sw = tester.widget<Switch>(find.byType(Switch));
    expect(sw.value, isTrue);
  });

  testWidgets('ActuatorToggle is disabled (onChanged null) when isPending', (tester) async {
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: ActuatorToggle(
      state: const ActuatorState(actuatorId: 'pump1', isOn: false, isPending: true),
      onToggle: (_) {},
    ))));
    expect(find.text('Pending'), findsOneWidget);
    final sw = tester.widget<Switch>(find.byType(Switch));
    expect(sw.onChanged, isNull);
  });
}
