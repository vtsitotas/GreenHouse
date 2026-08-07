"""The firewall's allowlist must match the ports this project actually serves.

A default-deny firewall is only as good as its allowlist. Two ways it rots:
a service gains a port and gets silently blocked, or a port is opened "just in
case" and quietly widens the attack surface. Both are caught here by deriving
the expected set from the real config files rather than restating it.
"""
import os
import re

_HERE = os.path.dirname(__file__)
_PI = os.path.join(_HERE, '..')

FIREWALL_SH = os.path.join(_PI, 'scripts', 'firewall.sh')
MOSQUITTO_CONF = os.path.join(_PI, 'mosquitto', 'mosquitto.conf')
PORTAL = os.path.join(_PI, 'portal', 'portal.py')


def _read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def _allowed_tcp():
    m = re.search(r'^TCP_PORTS="([^"]+)"', _read(FIREWALL_SH), re.M)
    assert m, 'TCP_PORTS not found in firewall.sh'
    return {int(p) for p in m.group(1).split()}


def _allowed_udp():
    m = re.search(r'^UDP_PORTS="([^"]+)"', _read(FIREWALL_SH), re.M)
    assert m, 'UDP_PORTS not found in firewall.sh'
    return {int(p) for p in m.group(1).split()}


def test_ssh_is_always_allowed():
    """Removing this locks the owner out of a remote unit permanently."""
    assert 22 in _allowed_tcp()
    src = _read(FIREWALL_SH)
    # Also present as an explicit early rule, not just via the loop.
    assert '--dport 22 -j ACCEPT' in src


def test_mosquitto_tls_port_is_allowed():
    conf = _read(MOSQUITTO_CONF)
    tls_ports = {int(p) for p in re.findall(r'^listener (\d+)\s*$', conf, re.M)}
    assert tls_ports, 'no externally-bound mosquitto listener found'
    assert tls_ports <= _allowed_tcp()


def test_mosquitto_plaintext_listener_is_never_opened():
    """1883 binds 127.0.0.1 only; exposing it would mean unauthenticated MQTT
    (allow_anonymous true) reachable from the network."""
    conf = _read(MOSQUITTO_CONF)
    assert re.search(r'^listener 1883 127\.0\.0\.1', conf, re.M)
    assert 1883 not in _allowed_tcp()


def test_cam_bridge_port_is_closed_while_the_camera_is_parked():
    """The camera lives under parked/camera/ and its bridge is not installed,
    so 8090 must not be open — that is precisely the 'opened just in case'
    case this file exists to catch. Reopen it when restoring the camera."""
    assert not os.path.exists(os.path.join(_PI, 'scripts', 'cam_bridge.py')), \
        'cam_bridge.py is back in the live tree — reopen 8090 and restore this test'
    assert 8090 not in _allowed_tcp()


def test_portal_https_port_is_allowed():
    m = re.search(r'HTTPS_PORT = (\d+)', _read(PORTAL))
    assert m
    assert int(m.group(1)) in _allowed_tcp()


def test_portal_http_port_is_allowed():
    assert 80 in _allowed_tcp()


def test_mdns_is_allowed_or_discovery_breaks():
    assert 5353 in _allowed_udp()


def test_no_unused_ports_are_opened():
    """Every allowed port must correspond to something this project serves.
    Catches 'opened just in case', which is what default-deny exists to stop."""
    justified = {
        22,    # SSH
        80,    # portal HTTP / captive portal
        8443,  # portal HTTPS
        8883,  # mosquitto TLS
    }
    assert _allowed_tcp() == justified, (
        f'unexpected TCP ports opened: {_allowed_tcp() - justified}; '
        f'missing: {justified - _allowed_tcp()}')


def test_udp_allowlist_is_limited_to_discovery_and_ap_mode():
    assert _allowed_udp() == {5353, 67, 53}


def test_default_policy_is_drop():
    assert ':INPUT DROP' in _read(FIREWALL_SH)


def test_established_connections_accepted_before_policy_flip():
    """Without this the SSH session applying the rules dies mid-run."""
    src = _read(FIREWALL_SH)
    est = src.index('ESTABLISHED,RELATED')
    ssh = src.index('--dport 22 -j ACCEPT')
    assert est < ssh, 'conntrack rule must come before the per-port rules'


def test_apply_path_has_a_rollback():
    src = _read(FIREWALL_SH)
    assert 'iptables-save' in src          # backup taken
    assert 'iptables-restore < "$BACKUP"' in src   # rollback on failed verify


def test_firewall_is_not_enabled_automatically():
    """Turning on a firewall unattended on a remote unit is a decision, not a
    default — install.sh must not invoke it."""
    install = _read(os.path.join(_PI, 'install.sh'))
    assert 'firewall.sh --apply' not in install
