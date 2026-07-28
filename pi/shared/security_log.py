#!/usr/bin/env python3
"""Security event logging and owner alerting.

Everything else in this project's hardening is *prevention* — authentication,
encryption, sandboxing, rate limits. This is the missing half: a greenhouse
that is being attacked currently tells nobody. An attacker can grind against
the pairing PIN or the camera token indefinitely and the owner finds out never.

Two jobs:

1. **Audit trail.** Append structured, greppable records of security-relevant
   events to /var/log/greenhouse-security.log — enough to answer "was anyone
   trying?" after the fact, which is exactly what the journal doesn't give you
   once it rotates.

2. **Alerting.** Push a notification to the owner's phone for events that
   actually warrant one, over the FCM pipeline that already exists.

The alerting is itself rate-limited, and that matters: without it, the
notification channel becomes a weapon. Anyone able to trigger a failed auth
could flood the owner's phone until they mute greenhouse alerts entirely — at
which point the real alert is the one they miss. So repeated events of the same
kind collapse into at most one push per ALERT_COOLDOWN_SECONDS.

Never raises. A logging failure must not take down the service that called it,
and must never turn into a way to break authentication by making it error out.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

SECURITY_LOG = '/var/log/greenhouse-security.log'

# Per-kind cooldown between owner notifications. 15 minutes is short enough to
# notice an ongoing attack and long enough that a sustained one produces a
# handful of pushes, not thousands.
ALERT_COOLDOWN_SECONDS = 900

# Events worth waking the owner for. Everything else is logged only: a single
# wrong PIN is a typo, not an incident.
ALERTABLE = {
    'pair_lockout',        # someone burned through the PIN attempt limit
    'cam_auth_failure',    # something tried to impersonate the camera
    'history_auth_failure',
    'cert_mismatch',       # a client rejected our cert, or we rejected theirs
}

_last_alert: dict = {}   # kind -> monotonic timestamp of last push


def _write_record(record: dict) -> None:
    try:
        with open(SECURITY_LOG, 'a') as f:
            f.write(json.dumps(record, sort_keys=True) + '\n')
    except Exception as exc:
        # Log file unwritable (permissions, disk full) must not break the
        # caller's auth path — fall back to stdout, which journald captures.
        print(f'[security] (log write failed: {exc}) {record}', flush=True)


def _should_alert(kind: str, now: float) -> bool:
    if kind not in ALERTABLE:
        return False
    last = _last_alert.get(kind)
    if last is not None and now - last < ALERT_COOLDOWN_SECONDS:
        return False
    _last_alert[kind] = now
    return True


def log_security_event(kind: str, detail: str = '', source: str = '',
                       alert: bool | None = None) -> dict:
    """Record a security event, and notify the owner if it warrants it.

    `alert=None` uses the ALERTABLE policy; pass True/False to override.
    Returns the record written, so callers and tests can assert on it.
    """
    now_wall = time.time()
    record = {
        'ts': int(now_wall),
        'iso': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(now_wall)),
        'kind': kind,
        'detail': detail,
        'source': source,
    }
    _write_record(record)

    want = _should_alert(kind, time.monotonic()) if alert is None else alert
    if want:
        try:
            from push import send_push
            where = f' from {source}' if source else ''
            send_push('Greenhouse security alert',
                      f'{kind.replace("_", " ")}{where}. {detail}'.strip())
        except Exception as exc:
            print(f'[security] alert push failed: {exc}', flush=True)
    return record


def read_recent(limit: int = 50, path: str | None = None) -> list:
    """Most recent events, newest last. For selftest and after-the-fact review."""
    try:
        with open(path or SECURITY_LOG) as f:
            lines = f.readlines()[-limit:]
    except Exception:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def summarize(events: list) -> dict:
    """Counts per event kind — what selftest prints."""
    counts: dict = {}
    for e in events:
        counts[e.get('kind', 'unknown')] = counts.get(e.get('kind', 'unknown'), 0) + 1
    return counts
