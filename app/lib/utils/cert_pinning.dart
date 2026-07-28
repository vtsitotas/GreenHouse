import 'dart:io';

import 'package:crypto/crypto.dart';

/// Certificate pinning shared by every TLS link to the Pi (MQTT on 8883 and
/// the portal's HTTPS on 8443).
///
/// The Pi's certificates are self-signed and per-unit, so no public CA can
/// vouch for them — the fingerprint captured at pairing time is the whole
/// trust anchor. Both transports pin the *same* fingerprint list because
/// `gen_certs.sh` gives the portal a copy of the broker's keypair: one
/// identity per unit, one thing to verify.
///
/// Format matches the Pi's `openssl x509 -fingerprint -sha256` output:
/// colon-separated uppercase hex byte pairs, comma-separated for the list
/// (CA first, then the leaf — see first_boot.sh).
bool matchesPinnedFingerprint(List<int> certDer, String expectedFingerprint) {
  final expected = expectedFingerprint
      .split(',')
      .map((f) => f.trim().toUpperCase())
      .where((f) => f.isNotEmpty)
      .toSet();
  if (expected.isEmpty) return false; // nothing to pin against — fail closed
  final actual = sha256
      .convert(certDer)
      .bytes
      .map((b) => b.toRadixString(16).padLeft(2, '0'))
      .join(':')
      .toUpperCase();
  return expected.contains(actual);
}

/// A short, human-comparable code identifying the greenhouse you are paired to.
///
/// This is the fix for the one case certificate pinning cannot cover: pairing
/// via mDNS discovery, where the app has no fingerprint yet and must trust the
/// certificate for that first exchange. An active man-in-the-middle would have
/// substituted its own certificate, so the code the app derives would differ
/// from the one the Pi prints (`selftest.sh`). Comparing them out of band — by
/// eye, once — detects that, exactly like an SSH host-key fingerprint or a
/// Signal safety number.
///
/// Derived from the pinned fingerprint rather than shown raw because a
/// 64-hex-character string is not something anyone actually compares. Hashing
/// first means the code changes completely if the certificate changes.
///
/// Format: `ABCD-EFGH` (uppercase hex, hyphenated for readability).
String safetyCode(String tlsFingerprint) {
  final normalized = tlsFingerprint
      .split(',')
      .map((f) => f.trim().toUpperCase())
      .where((f) => f.isNotEmpty)
      .join(',');
  if (normalized.isEmpty) return '';
  final digest = sha256.convert(normalized.codeUnits).bytes;
  final hex = digest
      .take(4)
      .map((b) => b.toRadixString(16).padLeft(2, '0'))
      .join()
      .toUpperCase();
  return '${hex.substring(0, 4)}-${hex.substring(4, 8)}';
}

/// An [HttpClient] that accepts *only* the pinned certificate.
///
/// `badCertificateCallback` fires because the chain is self-signed; returning
/// false for anything that doesn't match the pin means a MITM presenting its
/// own cert gets the connection dropped rather than silently proxied. An empty
/// fingerprint fails closed, exactly like the MQTT path.
HttpClient pinnedHttpClient(String fingerprint, {Duration? timeout}) {
  final client = HttpClient()
    ..badCertificateCallback =
        (X509Certificate cert, String host, int port) =>
            matchesPinnedFingerprint(cert.der, fingerprint);
  if (timeout != null) client.connectionTimeout = timeout;
  return client;
}
