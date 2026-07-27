import 'dart:convert';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:greenhouse_app/models/connection_config.dart';

const _configKey = 'greenhouse_connection_config';

class PairingService {
  final FlutterSecureStorage _storage;
  const PairingService(this._storage);

  Future<void> saveConfig(ConnectionConfig config) =>
      _storage.write(key: _configKey, value: jsonEncode(config.toJson()));

  // Unreadable stored credentials are treated as "not paired" rather than
  // allowed to throw. On Android the encrypted blob lives in SharedPreferences
  // while its key lives in the Keystore; a reinstall or a device/backup restore
  // can resurrect one without the other, and the resulting read throws
  // BadPaddingException. This runs inside the router's redirect (app.dart), so
  // an escaping exception leaves the router with no route at all — a permanent
  // black screen on every launch, unrecoverable without clearing app data.
  Future<ConnectionConfig?> loadConfig() async {
    try {
      final raw = await _storage.read(key: _configKey);
      if (raw == null) return null;
      return ConnectionConfig.fromJson(jsonDecode(raw) as Map<String, dynamic>);
    } on PlatformException {
      await _discardCorruptConfig();
      return null;
    } on FormatException {
      await _discardCorruptConfig();
      return null;
    }
  }

  // Best-effort: recovery must never itself throw, or it recreates the bug.
  Future<void> _discardCorruptConfig() async {
    try {
      await clearConfig();
    } catch (_) {
      // Nothing more to do — returning "not paired" already unblocks the app.
    }
  }

  Future<void> clearConfig() => _storage.delete(key: _configKey);

  Future<bool> get isPaired async => (await loadConfig()) != null;
}

final pairingServiceProvider = Provider(
  (_) => const PairingService(
    // resetOnError lets the plugin drop undecryptable data instead of throwing.
    FlutterSecureStorage(aOptions: AndroidOptions(resetOnError: true)),
  ),
);
