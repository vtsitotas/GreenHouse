import 'package:flutter_test/flutter_test.dart';
import 'package:greenhouse_app/models/connection_config.dart';

// api_token gates the Pi's /api/history*; cam_token gates the camera's HTTP
// API including /stream. Both arrive in the PIN-gated /pair/confirm response.
void main() {
  Map<String, dynamic> basePayload() => {
        'host_lan': 'greenhouse.local',
        'host_remote': 'abc.hivemq.cloud',
        'port': 8883,
        'tls_fingerprint': 'AA:BB',
        'username': 'app',
        'password': 'pw',
        'remote_username': 'ruser',
        'remote_password': 'rpw',
      };

  test('fromJson reads api_token and cam_token from the pairing payload', () {
    final config = ConnectionConfig.fromJson({
      ...basePayload(),
      'api_token': 'api-tok',
      'cam_token': 'cam-tok',
    });
    expect(config.apiToken, 'api-tok');
    expect(config.camToken, 'cam-tok');
  });

  test('fromJson defaults both tokens to empty for a pre-auth stored config', () {
    // Configs paired before these fields existed must still load rather than
    // throwing — the app degrades to a visible 401, not a crash.
    final config = ConnectionConfig.fromJson(basePayload());
    expect(config.apiToken, '');
    expect(config.camToken, '');
  });

  test('round-trips both tokens through toJson/fromJson', () {
    final original = ConnectionConfig.fromJson({
      ...basePayload(),
      'api_token': 'api-tok',
      'cam_token': 'cam-tok',
    });
    final restored = ConnectionConfig.fromJson(original.toJson());
    expect(restored.apiToken, 'api-tok');
    expect(restored.camToken, 'cam-tok');
    expect(restored.password, 'pw');
  });
}
