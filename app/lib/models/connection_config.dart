class ConnectionConfig {
  final String lanHost;
  final String remoteHost;
  final int port;
  final String tlsFingerprint;
  final String username;
  final String password;
  final String remoteUsername;
  final String remotePassword;

  const ConnectionConfig({
    required this.lanHost,
    required this.remoteHost,
    required this.port,
    required this.tlsFingerprint,
    required this.username,
    required this.password,
    required this.remoteUsername,
    required this.remotePassword,
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
      };
}
