class ConnectionConfig {
  final String lanHost;
  final String tailscaleHost;
  final int port;
  final String tlsFingerprint;
  final String username;
  final String password;

  const ConnectionConfig({
    required this.lanHost,
    required this.tailscaleHost,
    required this.port,
    required this.tlsFingerprint,
    required this.username,
    required this.password,
  });

  factory ConnectionConfig.fromJson(Map<String, dynamic> json) => ConnectionConfig(
        lanHost: json['host_lan'] as String,
        tailscaleHost: json['host_tailscale'] as String,
        port: json['port'] as int,
        tlsFingerprint: json['tls_fingerprint'] as String,
        username: json['username'] as String,
        password: json['password'] as String,
      );

  Map<String, dynamic> toJson() => {
        'host_lan': lanHost,
        'host_tailscale': tailscaleHost,
        'port': port,
        'tls_fingerprint': tlsFingerprint,
        'username': username,
        'password': password,
      };
}
