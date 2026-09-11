# pi/shared/nodes.py
"""The sensor trust store — the single source of truth for who is in the mesh.

Holds every sensor's AppKey, so it is written 0600 and never served over HTTP.
Nothing else in the system stores keys: the bridge is deliberately keyless.
"""
import json
import os
import re
import tempfile
from dataclasses import dataclass

NODES_PATH = '/etc/greenhouse/nodes.json'

_MAC_RE = re.compile(r'^[0-9A-F]{12}$')
APP_KEY_LEN = 16


class NodeStoreError(RuntimeError):
    """The store exists but cannot be trusted — refuse rather than drop sensors."""


@dataclass
class Node:
    mac: str
    app_key: bytes
    zone: str
    name: str
    sleepy: bool


def normalise_mac(value: str) -> str:
    mac = re.sub(r'[^0-9A-Fa-f]', '', value or '').upper()
    if not _MAC_RE.match(mac):
        raise ValueError(f'not a MAC address: {value!r}')
    return mac


def load(path: str = NODES_PATH) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as fh:
            content = fh.read()
        # install.sh/first_boot.sh create this file as an empty 0-byte
        # placeholder (same touch-then-fill pattern as every other per-unit
        # config) -- a fresh Pi that has never had a sensor enrolled must
        # load as "no nodes", not crash every /api/nodes call.
        if not content.strip():
            return {}
        raw = json.loads(content)
        out = {}
        for entry in raw['nodes']:
            key = bytes.fromhex(entry['app_key'])
            if len(key) != APP_KEY_LEN:
                raise ValueError(f'app_key for {entry["mac"]} is {len(key)} bytes')
            mac = normalise_mac(entry['mac'])
            out[mac] = Node(mac, key, entry['zone'], entry['name'],
                            bool(entry['sleepy']))
        return out
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise NodeStoreError(f'{path} is unreadable or malformed: {exc}') from exc


def save(store: dict, path: str = NODES_PATH) -> None:
    payload = {'version': 1, 'nodes': [
        {'mac': n.mac, 'app_key': n.app_key.hex(), 'zone': n.zone,
         'name': n.name, 'sleepy': n.sleepy}
        for n in store.values()]}
    directory = os.path.dirname(path) or '.'
    # Write-then-rename so a power cut mid-write cannot truncate the store.
    fd, tmp = tempfile.mkstemp(dir=directory)
    try:
        with os.fdopen(fd, 'w') as fh:
            json.dump(payload, fh, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def add(node: Node, path: str = NODES_PATH) -> None:
    store = load(path)
    store[normalise_mac(node.mac)] = node
    save(store, path)


def remove(mac: str, path: str = NODES_PATH) -> bool:
    store = load(path)
    if store.pop(normalise_mac(mac), None) is None:
        return False
    save(store, path)
    return True
