#!/usr/bin/env python3
"""UART-to-MQTT bridge — reads newline-delimited JSON from the bridge ESP32
over a direct GPIO UART link and republishes it to the existing loopback
Mosquitto broker exactly as the old WiFi/MQTT bridge firmware used to.

See docs/superpowers/specs/2026-07-20-uart-bridge-design.md (§Architecture,
§Fault Handling) and firmware/bridge_esp32/bridge_esp32.ino (the actual,
already-committed source of truth for the JSON line protocol this parses —
the spec's own wire-protocol section predates it and is superseded).

The bridge is no longer an MQTT client itself, so it has no TCP connection
for the broker to fire a Last-Will on. Instead it sends a heartbeat line on
the same cadence as its rank-0 beacon; this script tracks that heartbeat
per-mac (keyed off the mac the heartbeat itself carries -- never hardcoded)
and flips the bridge's own retained greenhouse/nodes/<mac>/status offline if
too many heartbeats are missed, then back online the moment they resume.
Same MESH_OFFLINE_AFTER-multiplier convention the firmware already uses for
edge-node liveness (firmware/libraries/GreenhouseMesh/mesh_config.h).
"""
import json
import time

import serial
import paho.mqtt.client as mqtt

SERIAL_PORT = '/dev/serial0'
BAUD = 115200
MQTT_HOST = '127.0.0.1'
MQTT_PORT = 1883

# Matches MESH_BRIDGE_BEACON_INTERVAL_MS (2000UL) in mesh_config.h -- the
# bridge sends one heartbeat line per beacon.
HEARTBEAT_INTERVAL_S = 2.0
# Matches MESH_OFFLINE_AFTER in mesh_config.h -- same "x expected report
# interval" multiplier convention used for every other offline check in this
# codebase.
HEARTBEAT_OFFLINE_AFTER = 3
HEARTBEAT_TIMEOUT_S = HEARTBEAT_INTERVAL_S * HEARTBEAT_OFFLINE_AFTER

# How often the main loop re-checks heartbeat staleness -- doesn't need to be
# tighter than this since HEARTBEAT_TIMEOUT_S is itself several seconds.
OFFLINE_CHECK_INTERVAL_S = 1.0

# How often the bridge's OWN mesh record is republished with a fresh timestamp
# while heartbeats keep arriving. The firmware sends that record once at boot
# (setup()'s sendMesh(bridgeMac, nullptr, 0, ...)), so without this its
# "last seen" would freeze at boot time even though the bridge proves itself
# alive every HEARTBEAT_INTERVAL_S. Deliberately much slower than the
# heartbeat: this is a retained publish, and one per minute is enough
# resolution for a human-readable "last seen" without churning the broker.
BRIDGE_MESH_REFRESH_S = 60.0

# Mesh JSON keys republished verbatim to greenhouse/nodes/<mac>/mesh -- "type"
# and "mac" are transport/routing fields (mac becomes the topic segment
# instead), not part of the MQTT contract. Matches the payload shape already
# established by pi/tools/simulator.py's build_mesh_payload() and consumed by
# the app (app/lib/models/node_status.dart), per
# docs/superpowers/specs/2026-07-26-mesh-deep-sleep-design.md §Telemetry.
_MESH_PAYLOAD_KEYS = ('parent', 'rank', 'rssi', 'sleepy', 'battery_mv', 'zone')

# The bridge's own root mesh record, byte-identical to what the firmware
# emits for itself at boot (verified against the retained payload on the
# bench unit). Used only as a fallback when no real one has been seen this
# run -- see _handle_heartbeat.
_BRIDGE_ROOT_MESH = {
    'parent': None, 'rank': 0, 'rssi': None,
    'sleepy': False, 'battery_mv': None, 'zone': None,
}


def new_state() -> dict:
    """Fresh liveness-tracking state for one serial_bridge run."""
    return {
        'bridge_mac': None,       # learned from the first heartbeat line seen
        'last_heartbeat': None,   # time.monotonic() of the last heartbeat
        'bridge_online': None,    # None = unknown yet, else True/False
        # Startup staleness sweep. Liveness tracking above can only start once
        # a heartbeat has been seen, so a restart while the ESP32 is dead used
        # to leave every retained "online" standing forever -- the app, and
        # anyone reading MQTT, saw a fleet that looked healthy while no byte
        # had arrived on the UART for hours. These two fields let the first
        # heartbeat-less timeout window retract those claims exactly once.
        'started': time.monotonic(),
        'retained_online': set(),  # MACs found retained-online at startup
        'startup_swept': False,    # sweep runs at most once per process
        # The bridge's own root mesh record, cached from the one the firmware
        # sends at boot so heartbeats can republish it with a fresh timestamp
        # (see BRIDGE_MESH_REFRESH_S).
        'bridge_mesh': None,
        'bridge_mesh_pub': None,   # time.monotonic() of that last republish
    }


def _status_topic(mac: str) -> str:
    return f'greenhouse/nodes/{mac}/status'


def _battery_topic(mac: str) -> str:
    return f'greenhouse/nodes/{mac}/battery'


def _mesh_topic(mac: str) -> str:
    return f'greenhouse/nodes/{mac}/mesh'


def _reading_topic(zone: str, group: str, metric: str) -> str:
    return f'greenhouse/{zone}/{group}/{metric}'


def _handle_reading(client, msg: dict) -> None:
    topic = _reading_topic(msg['zone'], msg['group'], msg['metric'])
    client.publish(topic, f"{float(msg['value']):.1f}", retain=True)


def _handle_status(client, msg: dict) -> None:
    client.publish(_status_topic(msg['mac']), msg['status'], retain=True)


def _handle_battery(client, msg: dict) -> None:
    client.publish(_battery_topic(msg['mac']), f"{float(msg['pct']):.1f}", retain=True)


def _publish_mesh(client, mac: str, payload: dict) -> None:
    """Publish a mesh record stamped with when it was actually observed.

    The `ts` (epoch seconds, wall clock) is the whole point: MQTT retains
    every one of these topics and replays them in full on every client
    (re)connect, with nothing in the protocol to say whether a message is
    seconds or days old. Without a timestamp in the payload itself, an app
    reconnecting can only date a node by when the replay happened to arrive
    -- which made every node look freshly seen on every app launch. Since
    this is a retained publish, the stamp of the LAST real sighting is what
    survives on the broker, which is exactly the value "last seen" wants.
    """
    stamped = dict(payload, ts=int(time.time()))
    client.publish(_mesh_topic(mac), json.dumps(stamped), retain=True)


def _handle_mesh(client, msg: dict, state: dict) -> None:
    mac = msg['mac']
    payload = {key: msg[key] for key in _MESH_PAYLOAD_KEYS}
    # The bridge's own root record -- identified by the firmware's own
    # definition of it (parent null + rank 0, see bridge_esp32.ino's comment
    # on sendMesh) rather than by comparing against state['bridge_mac'],
    # which isn't known yet: at boot the firmware sends this record from
    # setup(), before the first heartbeat line that teaches us the MAC.
    if payload['parent'] is None and payload['rank'] == 0:
        state['bridge_mesh'] = payload
    _publish_mesh(client, mac, payload)


def _handle_heartbeat(client, msg: dict, state: dict) -> None:
    mac = msg['mac']
    state['bridge_mac'] = mac
    now = time.monotonic()
    state['last_heartbeat'] = now
    # Only publish "online" on the transition into online (or the very first
    # heartbeat ever) -- not on every heartbeat, to avoid needless retained-
    # message spam, matching how the firmware/old bridge only publish on
    # state transitions.
    if state['bridge_online'] is not True:
        client.publish(_status_topic(mac), 'online', retain=True)
        state['bridge_online'] = True
    # Heartbeats are the bridge's only proof of life -- unlike an edge node,
    # it never sends a fresh mesh record of its own after boot. Refresh it so
    # its "last seen" tracks reality instead of freezing at boot.
    #
    # Falls back to synthesising the record rather than requiring a cached
    # one: the firmware sends it from setup(), so a serial_bridge that starts
    # (or is redeployed) while the ESP32 is already running would otherwise
    # never see one and never refresh at all -- the common case on any
    # service restart. The synthesised shape is not a guess: "parent null,
    # rank 0" IS the bridge's definition of its own root record
    # (bridge_esp32.ino's sendMesh(bridgeMac, nullptr, 0, ...)).
    last_pub = state.get('bridge_mesh_pub')
    if last_pub is None or now - last_pub >= BRIDGE_MESH_REFRESH_S:
        _publish_mesh(client, mac, state.get('bridge_mesh') or _BRIDGE_ROOT_MESH)
        state['bridge_mesh_pub'] = now


# Handlers that need no run-state. `heartbeat` and `mesh` are dispatched
# separately below because both read/write `state`.
_HANDLERS = {
    'reading': _handle_reading,
    'status': _handle_status,
    'battery': _handle_battery,
}


def handle_line(client, line: bytes, state: dict) -> None:
    """Parse and republish one UART line. Never raises -- malformed JSON,
    unknown/missing type, wrong-shape-for-its-type, and empty/timeout lines
    are all silently dropped so the caller's read loop can just keep going.
    """
    if not line:
        return  # read timeout (pyserial returns b'' on timeout) -- no-op
    try:
        msg = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    if not isinstance(msg, dict):
        return
    msg_type = msg.get('type')
    try:
        if msg_type == 'heartbeat':
            _handle_heartbeat(client, msg, state)
        elif msg_type == 'mesh':
            _handle_mesh(client, msg, state)
        else:
            handler = _HANDLERS.get(msg_type)
            if handler is not None:
                handler(client, msg)
            # unknown/missing type: drop silently
    except (KeyError, TypeError, ValueError):
        return  # well-formed JSON but wrong shape for its type -- drop


def check_heartbeat_offline(client, state: dict) -> None:
    """Flip the bridge's own retained status to offline if too long has
    passed since the last heartbeat. No-op if no heartbeat has ever been
    seen, or if already known offline (published once per transition)."""
    mac = state['bridge_mac']
    last = state['last_heartbeat']
    if mac is None or last is None:
        return
    if state['bridge_online'] is False:
        return  # already reported offline, nothing changed
    if time.monotonic() - last > HEARTBEAT_TIMEOUT_S:
        client.publish(_status_topic(mac), 'offline', retain=True)
        state['bridge_online'] = False


def note_retained_status(state: dict, topic: str, payload: bytes) -> None:
    """Record a node the broker still claims is online, so the sweep below can
    retract it if the UART turns out to be dead."""
    if state['startup_swept'] or payload.strip() != b'online':
        return
    parts = topic.split('/')
    if len(parts) == 4 and parts[0] == 'greenhouse' and parts[1] == 'nodes':
        state['retained_online'].add(parts[2])


def sweep_stale_online(client, state: dict) -> None:
    """If no heartbeat has arrived at all within the timeout window, the UART
    link is dead and NOTHING behind it can be asserted online -- retract every
    retained claim we inherited at startup.

    Deliberately gated on "no heartbeat ever seen this run" rather than on each
    node's own age: an edge node's status is only republished by the bridge on
    a transition, so a healthy node can legitimately go many minutes without
    one. A live bridge, by contrast, heartbeats every 2 s -- so silence across
    the whole window is specific evidence about the link, not about any node.
    """
    if state['startup_swept'] or state['last_heartbeat'] is not None:
        return
    if time.monotonic() - state['started'] <= HEARTBEAT_TIMEOUT_S:
        return
    for mac in sorted(state['retained_online']):
        client.publish(_status_topic(mac), 'offline', retain=True)
    if state['retained_online']:
        print(f"[serial-bridge] no heartbeat within {HEARTBEAT_TIMEOUT_S:.0f}s of "
              f"start -- marked {len(state['retained_online'])} stale node(s) offline",
              flush=True)
    state['startup_swept'] = True
    state['retained_online'] = set()


def run() -> None:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id='greenhouse-serial-bridge')
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    # Callbacks MUST be registered before connect(): a handler attached
    # afterwards can miss the CONNACK for the connection already in flight, and
    # then the subscribe below never happens at all. Bench-observed as a sweep
    # that silently never ran while the same code worked when driven by hand.
    state = new_state()
    client.on_message = lambda _c, _u, msg: note_retained_status(state, msg.topic, msg.payload)
    # Subscribe from on_connect rather than inline, so it also re-subscribes
    # after a broker reconnect -- and so the SUBSCRIBE is only sent once the
    # session is actually established.
    client.on_connect = lambda c, _u, _f, _rc: c.subscribe('greenhouse/nodes/+/status', qos=0)

    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()

    ser = serial.Serial(SERIAL_PORT, BAUD, timeout=1)
    print(f'[serial-bridge] listening on {SERIAL_PORT} @ {BAUD}', flush=True)

    last_offline_check = time.monotonic()
    while True:
        line = ser.readline()
        handle_line(client, line, state)

        now = time.monotonic()
        if now - last_offline_check >= OFFLINE_CHECK_INTERVAL_S:
            last_offline_check = now
            check_heartbeat_offline(client, state)
            sweep_stale_online(client, state)


if __name__ == '__main__':
    run()
