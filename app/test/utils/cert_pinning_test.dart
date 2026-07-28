import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/utils/cert_pinning.dart';

// Shared by the MQTT link (8883) and the portal's HTTPS (8443) — both pin the
// same per-unit fingerprint, so this logic must not drift between them.
void main() {
  _safetyCodeTests();
  String fingerprintOf(List<int> der) => sha256
      .convert(der)
      .bytes
      .map((b) => b.toRadixString(16).padLeft(2, '0'))
      .join(':')
      .toUpperCase();

  final der = [1, 2, 3, 4, 5];

  test('accepts a certificate whose fingerprint is in the pinned list', () {
    expect(matchesPinnedFingerprint(der, fingerprintOf(der)), isTrue);
  });

  test('accepts either entry of the CA,leaf comma-separated list', () {
    // first_boot.sh publishes both: the CA (what Dart hands the callback for a
    // self-signed chain) and the leaf.
    final other = [9, 9, 9];
    final list = '${fingerprintOf(other)},${fingerprintOf(der)}';
    expect(matchesPinnedFingerprint(der, list), isTrue);
    expect(matchesPinnedFingerprint(other, list), isTrue);
  });

  test('rejects a certificate that is not pinned (the MITM case)', () {
    expect(matchesPinnedFingerprint([7, 7, 7], fingerprintOf(der)), isFalse);
  });

  test('fails closed on an empty fingerprint rather than accepting anything', () {
    expect(matchesPinnedFingerprint(der, ''), isFalse);
    expect(matchesPinnedFingerprint(der, '   '), isFalse);
    expect(matchesPinnedFingerprint(der, ',,'), isFalse);
  });

  test('is case- and whitespace-insensitive about the stored value', () {
    final fp = fingerprintOf(der);
    expect(matchesPinnedFingerprint(der, '  ${fp.toLowerCase()}  '), isTrue);
  });

  test('pinnedHttpClient builds a client and honours the connect timeout', () {
    // badCertificateCallback is setter-only, so the policy itself is covered
    // by the matchesPinnedFingerprint cases above; this just verifies the
    // client is constructed and configured rather than silently defaulted.
    final client = pinnedHttpClient(fingerprintOf(der),
        timeout: const Duration(seconds: 5));
    expect(client.connectionTimeout, const Duration(seconds: 5));
    client.close();
  });
}

// ── Safety code (out-of-band MITM detection) ────────────────────────────────
// Certificate pinning covers every path except the very first mDNS pairing,
// where there is no fingerprint yet. Comparing this short code against the one
// the Pi prints closes that: a MITM's substituted certificate yields a
// different code.
void _safetyCodeTests() {
  test('safety code is stable, short, and human-comparable', () {
    final code = safetyCode('AA:BB:CC,DD:EE:FF');
    expect(code, matches(RegExp(r'^[0-9A-F]{4}-[0-9A-F]{4}$')));
    expect(safetyCode('AA:BB:CC,DD:EE:FF'), code); // deterministic
  });

  test('matches the derivation selftest.sh prints on the Pi', () {
    // Cross-checked against the Python implementation in selftest.sh; if either
    // side changes format, the two stop matching and this catches it.
    expect(safetyCode('AA:BB:CC,DD:EE:FF'), '48B5-A30C');
  });

  test('ignores case and surrounding whitespace', () {
    expect(safetyCode('  aa:bb:cc , dd:ee:ff '), safetyCode('AA:BB:CC,DD:EE:FF'));
  });

  test('a substituted certificate produces a different code', () {
    // This is the property the whole mechanism rests on.
    expect(safetyCode('AA:BB:CC'), isNot(safetyCode('AA:BB:CC,DD:EE:FF')));
    expect(safetyCode('11:22:33'), isNot(safetyCode('AA:BB:CC')));
  });

  test('empty fingerprint yields an empty code, not a fake one', () {
    expect(safetyCode(''), '');
    expect(safetyCode('  ,  '), '');
  });
}
