import 'dart:convert';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:greenhouse_app/models/connection_config.dart';

const _configKey = 'greenhouse_connection_config';

class PairingService {
  final FlutterSecureStorage _storage;
  const PairingService(this._storage);

  Future<void> saveConfig(ConnectionConfig config) =>
      _storage.write(key: _configKey, value: jsonEncode(config.toJson()));

  Future<ConnectionConfig?> loadConfig() async {
    final raw = await _storage.read(key: _configKey);
    if (raw == null) return null;
    return ConnectionConfig.fromJson(jsonDecode(raw) as Map<String, dynamic>);
  }

  Future<void> clearConfig() => _storage.delete(key: _configKey);

  Future<bool> get isPaired async => (await loadConfig()) != null;
}

final pairingServiceProvider = Provider(
  (_) => const PairingService(FlutterSecureStorage()),
);
