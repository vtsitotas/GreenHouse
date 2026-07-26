#!/usr/bin/env python3
"""
Greenhouse sensor simulator.
Publishes realistic fake readings for all MQTT topics via loopback port 1883.
Usage: python3 simulator.py [--interval 10]
"""
import argparse
import json
import math
import random
import time
import paho.mqtt.client as mqtt

BROKER = "127.0.0.1"
PORT   = 1883
ZONES  = ["zone1", "zone2", "zone3"]
NODES  = ["node1", "node2", "node3"]
ACTS   = ["pump1", "fan1", "light1"]

BRIDGE = "A0B1C2D3E4F5"

# Base mesh topology: node1 -> bridge (rank 1), node2 -> node1 (rank 2,
# sleepy), node3 -> bridge (rank 1). node3 periodically re-parents to
# node1 (see main()) to exercise the re-link animation.
NODE_PARENT = {"node1": BRIDGE, "node2": "node1", "node3": BRIDGE}
NODE_RANK   = {"node1": 1,      "node2": 2,       "node3": 1}
NODE_SLEEPY = {"node1": False,  "node2": True,    "node3": False}
NODE_ZONE   = dict(zip(NODES, ZONES))

RSSI_MIN = -85
RSSI_MAX = -45

def _wave(t, period=3600, lo=0.0, hi=1.0):
    return lo + (hi - lo) * (0.5 + 0.5 * math.sin(2 * math.pi * t / period))

def build_mesh_payload(node, parent, rank, rssi, sleepy, battery_mv, zone):
    """Pure function: build the retained /mesh JSON payload for one node.

    Matches the MQTT contract in
    docs/superpowers/specs/2026-07-26-mesh-deep-sleep-design.md §Telemetry
    exactly (key set + null semantics) -- `ts` is optional per the contract
    and omitted here since the simulator has no bridge uptime clock.
    """
    payload = {
        "parent": parent,
        "rank": rank,
        "rssi": rssi,
        "sleepy": sleepy,
        "battery_mv": battery_mv,
        "zone": zone,
    }
    return json.dumps(payload)

def walk_rssi(rssi):
    """One step of a slow random walk, clamped to [RSSI_MIN, RSSI_MAX]."""
    rssi += random.uniform(-2, 2)
    return max(RSSI_MIN, min(RSSI_MAX, rssi))

def battery_mv_from_pct(pct):
    """Derive a millivolt reading from the existing pct decay model."""
    return round(2800 + pct * 6)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=10.0)
    args = ap.parse_args()

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="simulator", clean_session=True)
    c.connect(BROKER, PORT, keepalive=60)
    c.loop_start()

    for node in NODES:
        c.publish(f"greenhouse/nodes/{node}/status", "online", qos=1, retain=True)
        c.publish(f"greenhouse/nodes/{node}/battery", str(round(random.uniform(60, 100), 1)), qos=1, retain=True)
    c.publish(f"greenhouse/nodes/{BRIDGE}/status", "online", qos=1, retain=True)
    c.publish(f"greenhouse/nodes/{BRIDGE}/mesh",
              build_mesh_payload(BRIDGE, parent=None, rank=0, rssi=None,
                                  sleepy=False, battery_mv=None, zone=None),
              qos=1, retain=True)
    for act in ACTS:
        c.publish(f"greenhouse/actuators/{act}/state", "OFF", qos=1, retain=True)

    rssi = {node: random.uniform(RSSI_MIN, RSSI_MAX) for node in NODES}
    node3_flipped = False

    print(f"[sim] publishing every {args.interval}s — Ctrl+C to stop")
    t0 = time.time()
    cycle = 0
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

            # node3 flips parent between bridge and node1 every ~10 cycles,
            # exercising the mesh map's re-link animation.
            if cycle > 0 and cycle % 10 == 0:
                node3_flipped = not node3_flipped
            node3_parent = "node1" if node3_flipped else BRIDGE
            node3_rank   = 2 if node3_flipped else 1

            for i, node in enumerate(NODES):
                pct = round(max(0, 100 - t / 3600 - i * 5), 1)
                c.publish(f"greenhouse/nodes/{node}/battery", str(pct), retain=True)

                rssi[node] = walk_rssi(rssi[node])
                if node == "node3":
                    parent, rank = node3_parent, node3_rank
                else:
                    parent, rank = NODE_PARENT[node], NODE_RANK[node]
                payload = build_mesh_payload(
                    node, parent=parent, rank=rank, rssi=round(rssi[node]),
                    sleepy=NODE_SLEEPY[node], battery_mv=battery_mv_from_pct(pct),
                    zone=NODE_ZONE[node])
                c.publish(f"greenhouse/nodes/{node}/mesh", payload, qos=1, retain=True)

            cycle += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[sim] stopped")
        for node in NODES:
            c.publish(f"greenhouse/nodes/{node}/status", "offline", qos=1, retain=True)
        c.publish(f"greenhouse/nodes/{BRIDGE}/status", "offline", qos=1, retain=True)
        c.loop_stop()
        c.disconnect()

if __name__ == "__main__":
    main()
