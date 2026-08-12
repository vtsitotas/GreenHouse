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
    // "Pending" reads as a state the device is in; the truth is that we asked
    // and haven't heard back, which is what the user needs to know.
    expect(find.text('Waiting for it to respond…'), findsOneWidget);
    final sw = tester.widget<Switch>(find.byType(Switch));
    expect(sw.onChanged, isNull);
  });

  testWidgets('ActuatorToggle shows the raw id only until the user names it',
      (tester) async {
    const state = ActuatorState(actuatorId: 'pump1', isOn: true);

    await tester.pumpWidget(const MaterialApp(
        home: Scaffold(body: ActuatorToggle(state: state, onToggle: _noop))));
    // No name yet: the id's tail is all we can honestly show.
    expect(find.text('ump1'), findsOneWidget);

    await tester.pumpWidget(const MaterialApp(
      home: Scaffold(
        body: ActuatorToggle(
          state: state,
          names: {'pump1': 'Water pump'},
          onToggle: _noop,
        ),
      ),
    ));
    expect(find.text('Water pump'), findsOneWidget);
    expect(find.text('pump1'), findsNothing);
  });

  testWidgets('long-press triggers rename when a handler is given', (tester) async {
    var renamed = false;
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: ActuatorToggle(
          state: const ActuatorState(actuatorId: 'pump1', isOn: false),
          onToggle: (_) {},
          onRename: () => renamed = true,
        ),
      ),
    ));

    await tester.longPress(find.byType(ListTile));
    expect(renamed, isTrue);
  });
}

void _noop(bool _) {}
