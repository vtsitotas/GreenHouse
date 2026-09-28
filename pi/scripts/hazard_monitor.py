#!/usr/bin/env python3
"""Live hazard alerts: fire, flood, frost, heat, drought (plan 2026-09-28).

weather.py evaluates rules every 30 min -- far too slow for a fire -- so this
service watches every live zone reading and runs it through
shared/hazards.py. A raised hazard is published on greenhouse/weather/alert,
the topic the app already shows and lora_uplink.py already forwards as a LoRa
alert, so remote sites report their own hazards within seconds. It also
triggers the hazard's action (flood: pump off) and a phone push, unless the
user turned "hazard alerts" off in the app.

Retained readings are ignored: they are stale at start-up and would raise
alerts for conditions that may be long gone.
"""
import json
import os
import sys
import threading
import time

import paho.mqtt.client as mqtt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
import hazards

CONFIG_PATH = '/etc/greenhouse/hazards.json'
ALERT_TOPIC = 'greenhouse/weather/alert'
READING_TOPICS = ('greenhouse/+/air/temperature', 'greenhouse/+/air/humidity',
                  'greenhouse/+/soil/moisture')
# The app publishes changes on the first; weather.py republishes the stored
# settings, retained, on the second.
SETTINGS_TOPICS = ('greenhouse/settings/notifications',
                   'greenhouse/settings/notifications/current')
_METRICS = {('air', 'temperature'): 'temperature', ('air', 'humidity'): 'humidity',
            ('soil', 'moisture'): 'soil'}
_NOT_ZONES = {'weather', 'sites', 'settings', 'actuators', 'nodes', 'app'}


def parse_reading(topic: str, payload: bytes):
    """greenhouse/<zone>/<group>/<metric> -> (zone, metric, value), or None."""
    parts = topic.split('/')
    if len(parts) != 4 or parts[0] != 'greenhouse' or parts[1] in _NOT_ZONES:
        return None
    metric = _METRICS.get((parts[2], parts[3]))
    if metric is None:
        return None
    try:
        return parts[1], metric, float(payload.decode('ascii'))
    except (UnicodeDecodeError, ValueError):
        return None


def hazard_push_enabled(payload: bytes):
    """The hazard_alerts flag from a notification-settings payload (default
    on), or None if the payload is not a settings object."""
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return bool(data.get('hazard_alerts', True))


def load_config(path: str = CONFIG_PATH) -> dict:
    try:
        with open(path) as f:
            return hazards.merge_config(json.load(f))
    except (OSError, ValueError) as exc:
        if not isinstance(exc, FileNotFoundError):
            print(f'[hazards] WARN: ignoring {path}: {exc}', flush=True)
        return hazards.merge_config(None)


def alert_payload(ev: hazards.Event, now: float) -> dict:
    return {'type': f'hazard-{ev.kind}', 'rule_id': hazards.rule_id(ev),
            'severity': ev.severity, 'message': ev.message, 'zone': ev.zone,
            'ts': int(now)}


def handle(monitor, topic, payload, retain, now, publish, push, push_enabled) -> list:
    """One MQTT reading in, events out; delivers each event via
    publish(topic, payload) and push(title, body)."""
    if retain:
        return []
    reading = parse_reading(topic, payload)
    if reading is None:
        return []
    events = monitor.add_reading(*reading, now)
    for ev in events:
        print(f'[hazards] {ev.severity}: {ev.message}', flush=True)
        publish(ALERT_TOPIC, json.dumps(alert_payload(ev, now)))
        if ev.action:
            publish(f'greenhouse/actuators/{ev.action["actuator"]}/set', ev.action['command'])
        if push_enabled:
            push(f'{hazards.title(ev.kind)} — {ev.zone}', ev.message)
    return events


def _push_async(title, body):
    # send_push shells out to mosquitto_sub and calls Firebase: never do that
    # on paho's network thread, or readings back up behind it.
    from push import send_push
    threading.Thread(target=send_push, args=(title, body), daemon=True).start()


def run() -> None:
    monitor = hazards.HazardMonitor(load_config())
    state = {'push': True}
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id='greenhouse-hazards')

    def publish(topic, payload):
        client.publish(topic, payload, qos=1)

    def on_connect(c, _u, _f, rc):
        if rc == 0:
            for t in READING_TOPICS + SETTINGS_TOPICS:
                c.subscribe(t, qos=0)

    def on_message(_c, _u, msg):
        if msg.topic in SETTINGS_TOPICS:
            enabled = hazard_push_enabled(msg.payload)
            if enabled is not None:
                state['push'] = enabled
            return
        try:
            handle(monitor, msg.topic, msg.payload, msg.retain, time.time(),
                   publish, _push_async, state['push'])
        except Exception as exc:   # one bad reading must not kill the service
            print(f'[hazards] ERROR on {msg.topic}: {exc}', flush=True)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect('127.0.0.1', 1883, 30)
    print('[hazards] watching zone readings', flush=True)
    client.loop_forever()


if __name__ == '__main__':
    run()
