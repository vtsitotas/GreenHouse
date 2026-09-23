#!/usr/bin/env python3
"""Remote greenhouse -> LoRaWAN uplink (spec 2026-09-14, rev. 2026-09-23).

Averages this site's local readings over report_interval_s and sends one
compact summary uplink; forwards local rule alerts immediately (rate-limited);
turns Class C downlink commands into local actuator publishes. The site keeps
working exactly as before if the gateway is unreachable -- nothing here is on
the local data path.
"""
import json
import os
import sys
import time

import paho.mqtt.client as mqtt
import serial

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
import lora_payload as lp
from rak3172 import Rak3172, RakError

CONFIG_PATH = '/etc/greenhouse/lora.json'
LORA_PORT = '/dev/lora'
_METRICS = {('air', 'temperature'): 'temperature', ('air', 'humidity'): 'humidity',
            ('soil', 'moisture'): 'soil'}


class ZoneAggregator:
    def __init__(self):
        self.reset()

    def reset(self):
        self._sums = {}

    def add(self, zone, metric, value):
        z = self._sums.setdefault(zone, {})
        s, n = z.get(metric, (0.0, 0))
        z[metric] = (s + value, n + 1)

    def summaries(self):
        out = []
        for zone in sorted(self._sums):
            m = self._sums[zone]
            mean = lambda k: (m[k][0] / m[k][1]) if k in m else None  # noqa: E731
            out.append(lp.ZoneSummary(zone, mean('temperature'), mean('humidity'), mean('soil')))
        return out


def route_reading(topic, payload, agg) -> bool:
    parts = topic.split('/')
    if len(parts) != 4 or parts[0] != 'greenhouse':
        return False
    metric = _METRICS.get((parts[2], parts[3]))
    zone = lp.zone_number(parts[1])
    if metric is None or zone is None:
        return False
    try:
        agg.add(zone, metric, float(payload))
    except ValueError:
        return False
    return True


class AlertLimiter:
    def __init__(self, min_gap_s):
        self.min_gap_s = min_gap_s
        self.last = {}

    def allow(self, rule_id, now) -> bool:
        prev = self.last.get(rule_id)
        if prev is not None and now - prev < self.min_gap_s:
            return False
        self.last[rule_id] = now
        return True


def command_publication(evt):
    if evt.get('type') != 'rx' or evt.get('port') != lp.PORT_COMMAND:
        return None
    try:
        actuator, on = lp.decode_command(evt['payload'])
    except ValueError:
        return None
    return f'greenhouse/actuators/{actuator}/set', 'ON' if on else 'OFF'


def run() -> None:
    cfg = json.load(open(CONFIG_PATH))
    interval = int(cfg.get('report_interval_s', 600))
    dev = Rak3172(serial.Serial(LORA_PORT, 115200, timeout=0.5))
    dev.configure_otaa(cfg['dev_eui'], cfg['app_eui'], cfg['app_key'])
    while not dev.join():
        print('[lora] join failed -- retrying in 60 s', flush=True)
        time.sleep(60)
    print('[lora] joined', flush=True)

    agg = ZoneAggregator()
    limiter = AlertLimiter(900)
    alerts = []

    def on_message(_c, _u, msg):
        if msg.topic == 'greenhouse/weather/alert':
            try:
                a = json.loads(msg.payload)
                alerts.append((a.get('severity', 'warning'), str(a.get('rule_id') or a.get('type'))))
            except (ValueError, TypeError):
                pass
        elif not msg.retain:
            route_reading(msg.topic, msg.payload, agg)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id='greenhouse-lora-uplink')
    client.on_message = on_message
    client.connect('127.0.0.1', 1883, 30)
    for t in ('greenhouse/+/air/temperature', 'greenhouse/+/air/humidity',
              'greenhouse/+/soil/moisture', 'greenhouse/weather/alert'):
        client.subscribe(t, qos=0)
    client.loop_start()

    next_report = time.monotonic() + interval
    while True:
        try:
            while alerts:
                severity, rule_id = alerts.pop(0)
                if limiter.allow(rule_id, time.monotonic()):
                    dev.send(lp.PORT_ALERT, lp.encode_alert(severity, rule_id))
            if time.monotonic() >= next_report:
                for msg in lp.encode_summaries(agg.summaries()):
                    dev.send(lp.PORT_SUMMARY, msg)
                agg.reset()
                next_report += interval
            for evt in dev.poll_events():
                pub = command_publication(evt)
                if pub:
                    client.publish(*pub)
        except RakError as exc:
            print(f'[lora] module error: {exc}', flush=True)
            time.sleep(5)
        time.sleep(1)


if __name__ == '__main__':
    run()
