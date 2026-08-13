import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/connection_status.dart';
import 'package:greenhouse_app/screens/dashboard/connection_banner.dart';
import 'package:greenhouse_app/utils/plain_language.dart';

void main() {
  Future<void> pump(WidgetTester tester, ConnectionStatus status,
          {DateTime? lastSighting}) =>
      tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: ConnectionBanner(status: status, lastSighting: lastSighting),
        ),
      ));

  testWidgets('offline names how old the data is, not just that it is old',
      (tester) async {
    final now = DateTime.now();
    await pump(tester, ConnectionStatus.offline, lastSighting: now);

    final hm = '${now.hour.toString().padLeft(2, '0')}:'
        '${now.minute.toString().padLeft(2, '0')}';
    expect(find.textContaining('showing readings from $hm'), findsOneWidget);
  });

  testWidgets('offline with an old sighting shows the date, not a bare time',
      (tester) async {
    await pump(tester, ConnectionStatus.offline,
        lastSighting: DateTime(2026, 6, 25, 9, 0));

    expect(find.textContaining('Jun 25'), findsOneWidget);
  });

  testWidgets('offline with nothing ever confirmed says so honestly',
      (tester) async {
    // Must NOT invent a time here: lastSeen is null precisely when all the
    // app has had is a retained MQTT replay, which carries no timestamp.
    await pump(tester, ConnectionStatus.offline);

    expect(find.textContaining('no readings yet'), findsOneWidget);
    expect(find.textContaining('showing readings from'), findsNothing);
  });

  testWidgets('connected states read as plain status, not route names',
      (tester) async {
    await pump(tester, ConnectionStatus.local);
    // Matches connectionStatusLabel() in plain_language.dart exactly -- this
    // banner used to say the shorter "Connected" here, which disagreed with
    // Settings' Connection row for the same ConnectionStatus.local.
    expect(find.text('Connected on your home network'), findsOneWidget);
    expect(find.text('Local'), findsNothing);

    await pump(tester, ConnectionStatus.remote);
    expect(find.text('Connected from away'), findsOneWidget);
    expect(find.text('Remote'), findsNothing);
  });

  testWidgets('local wording matches Settings\' Connection row -- same source of truth',
      (tester) async {
    await pump(tester, ConnectionStatus.local);
    expect(find.text(connectionStatusLabel(ConnectionStatus.local)), findsOneWidget);
  });
}
