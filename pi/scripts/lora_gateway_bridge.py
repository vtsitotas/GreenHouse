#!/usr/bin/env python3
"""ChirpStack <-> greenhouse/sites/<site_id>/... bridge (runs on the gateway Pi).

Remote-site data is published under greenhouse/sites/..., which the existing
hivemq_bridge.py already forwards (greenhouse/#), and which the local site's
single-level '+' subscriptions cannot match -- so the gateway's own greenhouse
dashboard and recorder are unaffected.
"""
import base64
import json
import os
import sys
import time

import paho.mqtt.client as mqtt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
import lora_payload as lp

CONFIG_PATH = '/etc/greenhouse/lora_sites.json'
_READING_TOPICS = (('temperature', 'air/temperature'), ('humidity', 'air/humidity'),
                   ('soil', 'soil/moisture'))


def _push(title, body):
    try:
        from push import send_push
        send_push(title, body)
    except Exception as exc:          # a push failure must not stop the bridge
        print(f'[lora-gw] push failed: {exc}', flush=True)


def parse_uplink(topic, raw):
    """Parse a ChirpStack `application/<id>/device/<dev_eui>/event/up` message.

    Never raises: a malformed topic, JSON body, base64 payload, or field
    shape (e.g. `rxInfo` entries or the body itself not being an object)
    just returns None so the on_message callback can skip it and keep going.
    """
    parts = topic.split('/')
    if len(parts) != 6 or parts[0] != 'application' or parts[2] != 'device' or parts[5] != 'up':
        return None
    try:
        body = json.loads(raw)
        rx = (body.get('rxInfo') or [{}])[0]
        return {'dev_eui': parts[3].lower(), 'fport': int(body['fPort']),
                'data': base64.b64decode(body.get('data') or ''),
                'rssi': rx.get('rssi'), 'snr': rx.get('snr')}
    except (ValueError, KeyError, TypeError, AttributeError):
        # AttributeError covers a well-formed-JSON-but-wrong-shape body, e.g.
        # a JSON array/string instead of an object, or an rxInfo entry that
        # isn't itself an object -- both make the ensuing .get() blow up.
        return None


def uplink_publications(site, up, now):
    base = f'greenhouse/sites/{site}'
    pubs = [(f'{base}/lora', json.dumps({'rssi': up['rssi'], 'snr': up['snr'], 'ts': int(now)}), True)]
    try:
        if up['fport'] == lp.PORT_SUMMARY:
            for z in lp.decode_summary(up['data']):
                for field, suffix in _READING_TOPICS:
                    v = getattr(z, field)
                    if v is not None:
                        pubs.append((f'{base}/zone{z.zone}/{suffix}', f'{v:.1f}', True))
        elif up['fport'] == lp.PORT_ALERT:
            a = lp.decode_alert(up['data'])
            pubs.append((f'{base}/weather/alert', json.dumps({**a, 'site': site, 'ts': int(now)}), False))
            _push(f'Greenhouse {site}', f'Rule {a["rule_id"]} ({a["severity"]})')
        # any other fPort: only the /lora status publish above -- unknown
        # ports are silently ignored rather than treated as an error.
    except (ValueError, TypeError) as exc:
        print(f'[lora-gw] bad payload from {site}: {exc}', flush=True)
    return pubs


def downlink_request(topic, payload, cfg):
    parts = topic.split('/')
    if len(parts) != 6 or parts[:2] != ['greenhouse', 'sites'] or parts[3] != 'actuators' \
            or parts[5] != 'set':
        return None
    site, actuator = parts[2], parts[4]
    dev_eui = next((d for d, s in cfg['sites'].items() if s == site), None)
    if dev_eui is None:
        return None
    on = payload.strip().upper() == b'ON'
    try:
        data = lp.encode_command(actuator, on)
    except ValueError:
        return None  # e.g. empty actuator name segment
    body = {'devEui': dev_eui, 'confirmed': False, 'fPort': lp.PORT_COMMAND,
            'data': base64.b64encode(data).decode()}
    return f'application/{cfg["application_id"]}/device/{dev_eui}/command/down', json.dumps(body)


def run() -> None:
    cfg = json.load(open(CONFIG_PATH))
    cfg['sites'] = {k.lower(): v for k, v in cfg['sites'].items()}

    # Everything below runs on a single thread (loop_forever() dispatches
    # on_message on the calling thread itself, unlike loop_start()'s
    # background thread), so there is no state shared across threads here
    # and no lock is needed -- unlike lora_uplink.py's ZoneAggregator, which
    # is written to from paho's callback thread and read from a separate
    # main loop.
    def on_message(client, _u, msg):
        if msg.topic.startswith('application/'):
            up = parse_uplink(msg.topic, msg.payload)
            site = cfg['sites'].get(up['dev_eui']) if up else None
            if site:
                for topic, payload, retain in uplink_publications(site, up, time.time()):
                    client.publish(topic, payload, retain=retain)
        elif not msg.retain:
            req = downlink_request(msg.topic, msg.payload, cfg)
            if req:
                client.publish(*req)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id='greenhouse-lora-gateway')
    client.username_pw_set(cfg['mqtt_user'], cfg['mqtt_password'])
    client.on_message = on_message
    client.connect('127.0.0.1', 1883, 30)
    client.subscribe(f'application/{cfg["application_id"]}/device/+/event/up', qos=1)
    client.subscribe('greenhouse/sites/+/actuators/+/set', qos=1)
    client.loop_forever()


if __name__ == '__main__':
    run()
