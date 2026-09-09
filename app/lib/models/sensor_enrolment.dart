// app/lib/models/sensor_enrolment.dart
import 'dart:convert';

/// A sensor's identity as printed on its box, scanned from the QR that
/// `pi/tools/provision_sensor.py` generates. The key travels no other way.
class SensorEnrolment {
  const SensorEnrolment({required this.mac, required this.key});

  final String mac; // 12 uppercase hex, no separators
  final String key; // 32 hex chars = 16 bytes

  static final _macPattern = RegExp(r'^[0-9A-F]{12}$');
  static final _keyPattern = RegExp(r'^[0-9a-fA-F]{32}$');

  factory SensorEnrolment.fromQr(String raw) {
    final Object? decoded;
    try {
      decoded = jsonDecode(raw);
    } on FormatException {
      throw const FormatException('That is not a sensor code.');
    }
    if (decoded is! Map<String, dynamic>) {
      throw const FormatException('That is not a sensor code.');
    }
    if (decoded['v'] != 1) {
      throw const FormatException(
          'This sensor needs a newer app version to add.');
    }
    final mac = (decoded['mac'] as String? ?? '')
        .replaceAll(RegExp(r'[^0-9A-Fa-f]'), '')
        .toUpperCase();
    final key = decoded['k'] as String? ?? '';
    if (!_macPattern.hasMatch(mac) || !_keyPattern.hasMatch(key)) {
      throw const FormatException('That sensor code is damaged or incomplete.');
    }
    return SensorEnrolment(mac: mac, key: key);
  }
}
