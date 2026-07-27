import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/connection/mqtt_connection.dart';

// Two distinct DER blobs standing in for the Pi's two certs. The pinning logic
// only ever hashes the bytes it is handed, so real X.509 encoding is irrelevant
// here — what matters is that they hash differently.
final _caDer = List<int>.generate(64, (i) => i);
final _leafDer = List<int>.generate(64, (i) => 255 - i);

String _fp(List<int> der) => sha256
    .convert(der)
    .bytes
    .map((b) => b.toRadixString(16).padLeft(2, '0'))
    .join(':')
    .toUpperCase();

void main() {
  final caFp = _fp(_caDer);
  final leafFp = _fp(_leafDer);

  test('accepts a single pinned fingerprint', () {
    expect(MqttConnection.matchesPinnedFingerprint(_caDer, caFp), isTrue);
  });

  // The regression this file exists for: the Pi's chain triggers
  // onBadCertificate twice — once for the self-signed CA, once for the leaf
  // (CN=greenhouse.local, no SAN, so connecting by IP is a hostname mismatch).
  // Rejecting either one aborts the handshake, so both must be pinned.
  test('accepts both certs when the CA and leaf are pinned together', () {
    final pinned = '$caFp,$leafFp';
    expect(MqttConnection.matchesPinnedFingerprint(_caDer, pinned), isTrue);
    expect(MqttConnection.matchesPinnedFingerprint(_leafDer, pinned), isTrue);
  });

  test('pinning only the leaf rejects the CA — the original bug', () {
    expect(MqttConnection.matchesPinnedFingerprint(_caDer, leafFp), isFalse);
  });

  test('rejects a cert that matches neither pin', () {
    final other = List<int>.generate(64, (i) => (i * 7) % 251);
    expect(
      MqttConnection.matchesPinnedFingerprint(other, '$caFp,$leafFp'),
      isFalse,
    );
  });

  test('fails closed on an empty or whitespace-only pin', () {
    expect(MqttConnection.matchesPinnedFingerprint(_caDer, ''), isFalse);
    expect(MqttConnection.matchesPinnedFingerprint(_caDer, '  ,  '), isFalse);
  });

  test('tolerates whitespace and lowercase in the pinned list', () {
    final pinned = ' ${caFp.toLowerCase()} , ${leafFp.toLowerCase()} ';
    expect(MqttConnection.matchesPinnedFingerprint(_caDer, pinned), isTrue);
    expect(MqttConnection.matchesPinnedFingerprint(_leafDer, pinned), isTrue);
  });

  // mqtt_client 10.11.11's MqttServerClient.connect() does
  // `onBadCertificate as bool Function(Object certificate)?` internally
  // (mqtt_server_client.dart:132) before ever inspecting a real certificate.
  // A closure declared as `(X509Certificate cert) => ...` has a runtime type
  // of `bool Function(X509Certificate)`, which is NOT a subtype of
  // `bool Function(Object)` — Dart's parameter contravariance rules make
  // that cast throw a TypeError immediately, on every single secure connect
  // call, regardless of host/password/fingerprint correctness. That's what
  // broke every LAN/remote connection from the moment TLS pinning was added
  // (PR #12) until this fix — and _tryConnect's catch swallowed the
  // exception, so it always surfaced as a generic "Could not connect",
  // indistinguishable from a real bad-credentials failure.
  //
  // The fix (mqtt_connection.dart _tryConnect) declares the closure's
  // parameter as `Object` and casts to X509Certificate inside the body —
  // contravariance still lets that assign to the X509Certificate-typed
  // field, but its runtime type now matches what the internal cast expects.
  // This test doesn't touch the app's closure directly (that requires a live
  // TLS handshake — verified manually against the real Pi instead); it pins
  // down the underlying Dart semantics so nobody "cleans up" the Object
  // parameter back to X509Certificate without knowing why it's required.
  test('an Object-typed cert callback casts to bool Function(Object) — '
      'an X509Certificate-typed one does not', () {
    bool onObject(Object cert) => cert is X509Certificate;
    final Object storedAsObjectParam = onObject;
    expect(
      () => storedAsObjectParam as bool Function(Object),
      returnsNormally,
    );

    bool onCert(X509Certificate cert) => true;
    final Object storedAsCertParam = onCert;
    expect(
      () => storedAsCertParam as bool Function(Object),
      throwsA(isA<TypeError>()),
    );
  });
}
