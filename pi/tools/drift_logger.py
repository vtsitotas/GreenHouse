#!/usr/bin/env python3
"""Bench-only: log the Pi-side arrival time of every LIVE /mesh message.

The Pi clock (NTP-disciplined) is the crystal reference; arrival spacing of a
sleepy node's reports measures that node's RC-clock cycle length. Retained
replays carry no timing information and are skipped.
"""
import argparse
import sys
import time

import paho.mqtt.client as mqtt


def format_line(topic: str, retain: bool, now: float):
    parts = topic.split('/')
    if retain or len(parts) != 4 or parts[0] != 'greenhouse' or parts[1] != 'nodes' \
            or parts[3] != 'mesh':
        return None
    return f'{parts[2]},{now:.6f}'


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--host', default='127.0.0.1')
    args = ap.parse_args(argv)
    out = open(args.out, 'a', buffering=1)

    def on_message(_c, _u, msg):
        line = format_line(msg.topic, msg.retain, time.time())
        if line:
            out.write(line + '\n')

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id='drift-logger')
    client.on_message = on_message
    client.connect(args.host, 1883, 30)
    client.subscribe('greenhouse/nodes/+/mesh', qos=1)
    client.loop_forever()


if __name__ == '__main__':
    sys.exit(main())
