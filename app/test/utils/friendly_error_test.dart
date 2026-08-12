import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/screens/common/friendly_error_view.dart';
import 'package:greenhouse_app/services/history_service.dart';
import 'package:greenhouse_app/utils/friendly_error.dart';

void main() {
  group('describeError', () {
    test('a missing history token points at re-pairing, the one real remedy', () {
      final f = describeError(const HistoryTokenMissingException());
      expect(f.title, contains('pairing again'));
      expect(f.steps.single, contains('Settings'));
    });

    test('a certificate mismatch leads with the innocent cause but still warns',
        () {
      final f = describeError(const HandshakeException('bad cert'));
      // The likely case (hub reinstalled) comes first so a user isn't
      // frightened by a security alert that is usually benign...
      expect(f.steps.first, contains('reset or reinstalled'));
      // ...but the real security meaning is not softened away.
      expect(f.message, contains('pretending to be it'));
    });

    test('a timeout suggests waiting before anything more drastic', () {
      final f = describeError(TimeoutException('too slow'));
      expect(f.title, contains('too long'));
      expect(f.steps.first, contains('Wait'));
    });

    test('an unreachable host explains reachability, not sockets', () {
      final f = describeError(const SocketException('Connection refused'));
      expect(f.title, contains("Can't reach"));
      expect(f.message, isNot(contains('SocketException')));
      expect(f.steps.first, contains('power'));
    });

    test('an unnamed IOException still gets reachability advice, not a dump', () {
      final f = describeError(const HttpException('boom'));
      expect(f.steps, isNotEmpty);
      expect(f.message, isNot(contains('HttpException')));
    });

    test('a malformed reply is attributed to a version mismatch', () {
      final f = describeError(const FormatException('unexpected token'));
      expect(f.title, contains('unexpected'));
      expect(f.steps.first, contains('Update'));
    });

    test('an unrecognised error never puts the raw text in the headline', () {
      final f = describeError(Exception('kaboom'));
      expect(f.title, 'Something went wrong');
      expect(f.title, isNot(contains('kaboom')));
      expect(f.message, isNot(contains('kaboom')));
      expect(f.steps, isNotEmpty);
    });

    test('HandshakeException is matched before the generic IOException branch',
        () {
      // HandshakeException IS an IOException, so branch order decides whether
      // a possible impersonation is reported as a generic connection failure.
      final f = describeError(const HandshakeException('bad cert'));
      expect(f.title, isNot(contains('connection failed')));
    });
  });

  group('FriendlyErrorView', () {
    testWidgets('shows the friendly text and hides the raw error until asked',
        (tester) async {
      await tester.pumpWidget(const MaterialApp(
        home: Scaffold(
          body: FriendlyErrorView(error: SocketException('Connection refused')),
        ),
      ));

      expect(find.textContaining("Can't reach the greenhouse"), findsOneWidget);
      expect(find.text('What to try'), findsOneWidget);
      // The raw exception is present in the tree but collapsed, so it is not
      // rendered until the user expands it.
      expect(find.textContaining('Connection refused'), findsNothing);

      await tester.tap(find.text('Technical details'));
      await tester.pumpAndSettle();

      expect(find.textContaining('Connection refused'), findsOneWidget);
    });

    testWidgets('offers a retry only when a handler is given', (tester) async {
      await tester.pumpWidget(const MaterialApp(
        home: Scaffold(body: FriendlyErrorView(error: SocketException('x'))),
      ));
      expect(find.text('Try again'), findsNothing);

      var retried = false;
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: FriendlyErrorView(
            error: const SocketException('x'),
            onRetry: () => retried = true,
          ),
        ),
      ));

      await tester.tap(find.text('Try again'));
      expect(retried, isTrue);
    });
  });
}
