import 'package:flutter/services.dart';

// Holds Android's WifiManager.MulticastLock for the duration of mDNS
// discovery (see MainActivity.kt). Without it, Android may silently filter
// out incoming mDNS packets to save power — multicast_dns has no way to
// request this itself, so pairing_screen.dart must acquire/release around
// its MDnsClient lookup. No-op on iOS/desktop (channel simply isn't handled
// there; MissingPluginException is swallowed rather than crashing discovery).
class MulticastLock {
  static const _channel = MethodChannel('greenhouse/multicast');

  static Future<void> acquire() async {
    try {
      await _channel.invokeMethod('acquire');
    } on MissingPluginException {
      // Not Android — mDNS discovery proceeds without it.
    }
  }

  static Future<void> release() async {
    try {
      await _channel.invokeMethod('release');
    } on MissingPluginException {
      // Not Android.
    }
  }
}
