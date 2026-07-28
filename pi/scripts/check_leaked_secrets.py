#!/usr/bin/env python3
"""Fail if this unit is still using a credential that leaked into git history.

Rotation is only meaningful if you can tell whether it actually happened.
Deleting a secret from the working tree does not invalidate it — anyone with a
clone still has it — so this checks the *live* configuration against the values
that are known-published and reports any that are still in use.

Run directly, or via selftest.sh which calls it on every deploy:

    python3 pi/scripts/check_leaked_secrets.py

Exit code 0 = clean, 1 = at least one compromised value still in use.

Compromised values are stored as SHA-256 hashes, not plaintext. That is not
security theatre — the values are already public in git history, so hiding them
buys nothing. It is so that this file does not *re-introduce* them: SECURITY.md
recommends scrubbing those strings out of history with git-filter-repo, and a
checked-in list of the literals would put them straight back on the next
commit, silently undoing the scrub. Hashes let the check keep working after the
history is cleaned.

Adding a newly-discovered leak:
    python3 -c "import hashlib;print(hashlib.sha256(b'THEVALUE').hexdigest())"
and paste the hash below with a note on where it was exposed.
"""
import hashlib
import json
import os
import sys


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# sha256(value) -> where it leaked. Verified with `git log --all -p -S '<value>'`
# before hashing; see SECURITY.md §1 for how to recover the literals from
# history if you need them for a filter-repo run.
KNOWN_LEAKED = {
    "fb9070ecf721c5843a047ff79eb959267cc7d38562455567c02bb1649aa5012a":
        "commit c0383b3 — home WiFi password in bridge_esp32.ino",
    "a6f17ddb33fc284ed6d15ee677c48f8f581444e826362337541a69645ed6b662":
        "commit c0383b3 — MQTT password in bridge_esp32.ino",
}

# Values shipped in the repo as placeholders. Not leaks, but equally useless as
# credentials: they are public and identical on every unit that never changed
# them, so a unit still holding one is effectively unauthenticated.
KNOWN_PLACEHOLDERS = {
    _h("your-cam-shared-token"): "pi/cam_token.txt.example",
    _h("your-wifi-password"): "secrets.h.example",
    _h("your-mqtt-bridge-password"): "secrets.h.example",
    _h("changeme"): "generic placeholder",
}

DEVICE_CFG = "/etc/greenhouse/device.json"
HIVEMQ_CFG = "/etc/greenhouse/hivemq.json"
CAM_TOKEN_FILE = "/etc/greenhouse/cam_token.txt"


def _load_json(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _read_text(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return ""


def collect_live_secrets() -> dict:
    """Every credential currently in use, as {label: value}."""
    device = _load_json(DEVICE_CFG)
    hivemq = _load_json(HIVEMQ_CFG)
    secrets = {
        "device.json password":        device.get("password", ""),
        "device.json bridge_password": device.get("bridge_password", ""),
        "device.json api_token":       device.get("api_token", ""),
        "device.json pair_pin":        device.get("pair_pin", ""),
        "hivemq.json password":        hivemq.get("password", ""),
        "cam_token.txt":               _read_text(CAM_TOKEN_FILE),
    }
    return {k: v for k, v in secrets.items() if v}


def find_problems(live: dict) -> list:
    """Return [(label, value, why)] for every credential that must be rotated."""
    problems = []
    for label, value in live.items():
        digest = _h(value)
        if digest in KNOWN_LEAKED:
            problems.append((label, value, f"LEAKED — {KNOWN_LEAKED[digest]}"))
        elif digest in KNOWN_PLACEHOLDERS:
            problems.append((label, value,
                             f"PLACEHOLDER — public value from {KNOWN_PLACEHOLDERS[digest]}"))
    return problems


def main() -> int:
    live = collect_live_secrets()
    if not live:
        print("[leak-check] no provisioned secrets found — run install.sh first")
        return 1
    problems = find_problems(live)
    if not problems:
        print(f"[leak-check] OK — {len(live)} live secrets, none known-compromised")
        return 0
    print("[leak-check] COMPROMISED CREDENTIALS STILL IN USE:", file=sys.stderr)
    for label, _value, why in problems:
        print(f"    {label}: {why}", file=sys.stderr)
    print("", file=sys.stderr)
    print("    Fix: sudo bash pi/scripts/rotate_secrets.sh", file=sys.stderr)
    print("    (and rotate the router/HiveMQ passwords too — see SECURITY.md)",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
