/// Turning a thrown object into something a grower can act on.
///
/// The app used to render `Text('Error: $e')`, which puts
/// `SocketException: OS Error: Connection refused, errno = 111` on screen as
/// the entire explanation. That tells the one person who doesn't need it
/// (the developer) everything, and the person holding the phone nothing.
///
/// Every case here names a likely cause and gives steps in cheapest-first
/// order. The raw text is never discarded — `FriendlyErrorView` keeps it
/// under "Technical details" — it just stops being the headline.
library;

import 'dart:async';
import 'dart:io';

import 'package:greenhouse_app/services/history_service.dart';

class FriendlyError {
  final String title;
  final String message;

  /// Ordered cheapest/likeliest first: check a switch before moving hardware.
  final List<String> steps;

  const FriendlyError({
    required this.title,
    required this.message,
    this.steps = const [],
  });
}

/// Steps that apply whenever the phone can't reach the greenhouse at all.
/// Shared so the several ways that failure surfaces give the same advice.
const List<String> _reachabilitySteps = [
  'Check the greenhouse hub has power',
  "Make sure your phone is on the same Wi-Fi as the hub, or that you're "
      'signed in for remote access',
  'If you have just moved the hub, its address may have changed — try '
      'pairing again from Settings',
];

FriendlyError describeError(Object error) {
  // Ordered most specific first: HandshakeException is a subtype of
  // IOException, so a broader catch would swallow it.
  if (error is HistoryTokenMissingException) {
    return const FriendlyError(
      title: 'This phone needs pairing again',
      message: 'It was set up before the greenhouse started protecting its '
          'records, so it has no key to read them with.',
      steps: ['Open Settings and pair with the greenhouse again'],
    );
  }

  if (error is HandshakeException) {
    return const FriendlyError(
      title: "This doesn't look like your greenhouse",
      message: 'Something answered, but it could not prove it is really your '
          'hub. Usually this means the hub was reset or reinstalled — but it '
          'can also mean another device is pretending to be it, so nothing '
          'was sent.',
      steps: [
        'If you recently reset or reinstalled the hub, pair again using its '
            'QR code',
        "If you didn't, do not continue on this network — check the safety "
            'code in Settings first',
      ],
    );
  }

  if (error is TimeoutException) {
    return const FriendlyError(
      title: 'The greenhouse took too long to answer',
      message: 'It may be starting up, out of range, or busy.',
      steps: [
        'Wait a moment and try again',
        ..._reachabilitySteps,
      ],
    );
  }

  if (error is SocketException) {
    return const FriendlyError(
      title: "Can't reach the greenhouse",
      message: 'Your phone could not open a connection to the hub.',
      steps: _reachabilitySteps,
    );
  }

  // Also covers HttpException, TlsException and friends — anything that got
  // as far as I/O but failed in a way we have not specifically named.
  if (error is IOException) {
    return const FriendlyError(
      title: 'The connection failed',
      message: 'Something went wrong while talking to the greenhouse.',
      steps: _reachabilitySteps,
    );
  }

  if (error is FormatException) {
    return const FriendlyError(
      title: 'The greenhouse sent something unexpected',
      message: 'The reply could not be understood. This usually means the hub '
          'is running an older version than the app.',
      steps: [
        'Update the greenhouse hub, then try again',
        'If it keeps happening, pair again from Settings',
      ],
    );
  }

  return const FriendlyError(
    title: 'Something went wrong',
    message: "The app hit a problem it doesn't have specific advice for.",
    steps: [
      'Try again',
      'If it keeps happening, open the technical details below when asking '
          'for help — they say exactly what failed',
    ],
  );
}
