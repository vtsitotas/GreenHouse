#!/usr/bin/env python3
"""Replacement for Mosquitto's native `connection` bridge to HiveMQ Cloud.

The built-in Mosquitto bridge directive never completes a handshake against
this HiveMQ Cloud cluster (verified: zero successful CONNACKs over 9 days of
logs) — a real incompatibility in Mosquitto's bridge TLS/CONNECT code path,
not a HiveMQ-side policy. A plain paho-mqtt client against the exact same
host/credentials connects and stays connected without issue, so this script
just re-implements the two-way `greenhouse/#` forward using paho-mqtt
instead of relying on Mosquitto's bridge.
"""
import json
import ssl
import time

import paho.mqtt.client as mqtt

LOCAL_HOST = '127.0.0.1'
LOCAL_PORT = 1883
TOPIC = 'greenhouse/#'
HIVEMQ_CONFIG = '/etc/greenhouse/hivemq.json'

# Shared last-forwarded-value cache used to stop the two connections from
# echoing a message back and forth forever: whichever side forwards a
# message records it here, and the receiving side skips re-forwarding an
# unchanged value it just received.
_last_seen = {}


def _load_remote_config():
    with open(HIVEMQ_CONFIG) as f:
        return json.load(f)


def _make_forwarder(name, target_holder):
    """Returns an on_message handler that forwards to target_holder['client']."""

    def on_message(client, userdata, msg):
        target = target_holder['client']
        if target is None:
            return
        key = (msg.topic, msg.retain)
        if _last_seen.get(key) == msg.payload:
            return  # echo of what we ourselves just forwarded
        _last_seen[key] = msg.payload
        target.publish(msg.topic, msg.payload, qos=1, retain=msg.retain)

    return on_message


def main():
    remote_cfg = _load_remote_config()

    local = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id='greenhouse-hivemq-bridge-local')
    remote = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id='greenhouse-hivemq-bridge-remote')

    holder_for_local_msgs = {'client': None}   # local  -> forwards to remote
    holder_for_remote_msgs = {'client': None}  # remote -> forwards to local

    local.on_message = _make_forwarder('local', holder_for_local_msgs)
    remote.on_message = _make_forwarder('remote', holder_for_remote_msgs)

    def on_local_connect(client, userdata, flags, rc):
        print(f'[hivemq-bridge] local connected rc={rc}', flush=True)
        client.subscribe(TOPIC, qos=1)

    def on_remote_connect(client, userdata, flags, rc):
        print(f'[hivemq-bridge] remote connected rc={rc}', flush=True)
        client.subscribe(TOPIC, qos=1)

    def on_local_disconnect(client, userdata, rc):
        print(f'[hivemq-bridge] local disconnected rc={rc}', flush=True)

    def on_remote_disconnect(client, userdata, rc):
        print(f'[hivemq-bridge] remote disconnected rc={rc}', flush=True)

    local.on_connect = on_local_connect
    remote.on_connect = on_remote_connect
    local.on_disconnect = on_local_disconnect
    remote.on_disconnect = on_remote_disconnect

    remote.username_pw_set(remote_cfg['username'], remote_cfg['password'])
    remote.tls_set(ca_certs='/etc/ssl/certs/ca-certificates.crt', tls_version=ssl.PROTOCOL_TLSv1_2)
    remote.reconnect_delay_set(min_delay=1, max_delay=30)
    local.reconnect_delay_set(min_delay=1, max_delay=30)

    local.connect(LOCAL_HOST, LOCAL_PORT, keepalive=60)
    remote.connect(remote_cfg['host'], remote_cfg['port'], keepalive=60)

    holder_for_local_msgs['client'] = remote
    holder_for_remote_msgs['client'] = local

    local.loop_start()
    remote.loop_start()

    print('[hivemq-bridge] running', flush=True)
    while True:
        time.sleep(60)


if __name__ == '__main__':
    main()
