class ConnectionConfig {
  final String lanHost;
  final String remoteHost;
  final int port;
  final String tlsFingerprint;
  final String username;
  final String password;
  final String remoteUsername;
  final String remotePassword;

  /// Bearer token for the Pi's read-only history API (`/api/history*`).
  /// Delivered by the PIN-gated `/pair/confirm` response. Empty on configs
  /// paired before the API gained authentication, or entered by hand — the
  /// history screen surfaces the resulting 401 rather than silently failing.
  final String apiToken;

  /// Shared token gating the ESP32-CAM's HTTP API, including the `/stream`
  /// the app opens directly over the LAN. Same value as the Pi's
  /// `/etc/greenhouse/cam_token.txt` and the camera's flashed `CAM_TOKEN`.
  final String camToken;

  const ConnectionConfig({
    required this.lanHost,
    required this.remoteHost,
    required this.port,
    required this.tlsFingerprint,
    required this.username,
    required this.password,
    required this.remoteUsername,
    required this.remotePassword,
    this.apiToken = '',
    this.camToken = '',
  });

  factory ConnectionConfig.fromJson(Map<String, dynamic> json) => ConnectionConfig(
        lanHost:        json['host_lan']        as String,
        remoteHost:     json['host_remote']     as String? ??
                        json['host_tailscale']  as String? ?? '',
        port:           json['port']            as int,
        tlsFingerprint: json['tls_fingerprint'] as String,
        username:       json['username']        as String,
        password:       json['password']        as String,
        remoteUsername: json['remote_username'] as String? ?? '',
        remotePassword: json['remote_password'] as String? ?? '',
        apiToken:       json['api_token']       as String? ?? '',
        camToken:       json['cam_token']       as String? ?? '',
      );

  Map<String, dynamic> toJson() => {
        'host_lan':        lanHost,
        'host_remote':     remoteHost,
        'port':            port,
        'tls_fingerprint': tlsFingerprint,
        'username':        username,
        'password':        password,
        'remote_username': remoteUsername,
        'remote_password': remotePassword,
        'api_token':       apiToken,
        'cam_token':       camToken,
      };
}
