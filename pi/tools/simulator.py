#!/usr/bin/env python3
"""
Greenhouse sensor simulator.
Publishes realistic fake readings for all MQTT topics via loopback port 1883.
Usage: python3 simulator.py [--interval 10]
"""
import argparse
import math
import random
import time
import paho.mqtt.client as mqtt

BROKER = "127.0.0.1"
PORT   = 1883
ZONES  = ["zone1", "zone2", "zone3"]
NODES  = ["node1", "node2", "node3"]
ACTS   = ["pump1", "fan1", "light1"]

def _wave(t, period=3600, lo=0.0, hi=1.0):
    return lo + (hi - lo) * (0.5 + 0.5 * math.sin(2 * math.pi * t / period))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=10.0)
    args = ap.parse_args()

    c = mqtt.Client(client_id="simulator", clean_session=True)
    c.connect(BROKER, PORT, keepalive=60)
    c.loop_start()

    for node in NODES:
        c.publish(f"greenhouse/nodes/{node}/status", "online", qos=1, retain=True)
        c.publish(f"greenhouse/nodes/{node}/battery", str(round(random.uniform(60, 100), 1)), qos=1, retain=True)
    for act in ACTS:
        c.publish(f"greenhouse/actuators/{act}/state", "OFF", qos=1, retain=True)

    print(f"[sim] publishing every {args.interval}s — Ctrl+C to stop")
    t0 = time.time()
    try:
        while True:
            t = time.time() - t0
            for zone in ZONES:
                temp = round(_wave(t, 86400, 18, 36) + random.gauss(0, 0.3), 1)
                hum  = round(_wave(t, 86400, 40, 90) + random.gauss(0, 1.0), 1)
                soil = round(_wave(t, 7200,  10, 80) + random.gauss(0, 2.0), 1)
                lux  = round(max(0, _wave(t, 86400, 0, 80000) + random.gauss(0, 500)), 0)
                c.publish(f"greenhouse/{zone}/air/temperature", str(temp), retain=True)
                c.publish(f"greenhouse/{zone}/air/humidity",    str(hum),  retain=True)
                c.publish(f"greenhouse/{zone}/soil/moisture",   str(soil), retain=True)
                c.publish(f"greenhouse/{zone}/light/lux",       str(lux),  retain=True)
            pressure = round(1013 + random.gauss(0, 2), 1)
            c.publish("greenhouse/weather/pressure", str(pressure), retain=True)
            for i, node in enumerate(NODES):
                pct = round(max(0, 100 - t / 3600 - i * 5), 1)
                c.publish(f"greenhouse/nodes/{node}/battery", str(pct), retain=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[sim] stopped")
        for node in NODES:
            c.publish(f"greenhouse/nodes/{node}/status", "offline", qos=1, retain=True)
        c.loop_stop()
        c.disconnect()

if __name__ == "__main__":
    main()
