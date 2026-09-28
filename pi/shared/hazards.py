"""Per-zone hazard detection from the sensors the greenhouse already has.

Pure logic, no MQTT: hazard_monitor.py feeds every live reading into
HazardMonitor.add_reading() and delivers the Events it returns. Each (zone,
hazard) pair is a small state machine:

  * raise and clear thresholds differ (hysteresis), so a reading hovering at
    a threshold raises once instead of flapping;
  * "sustained" hazards (flood, heat, drought) must hold continuously for a
    number of minutes. The timer starts at the first reading that meets the
    condition and is reset only by a reading that does not -- a node that
    goes silent does not reset it; the next reading decides;
  * an active hazard is re-announced every `renotify_h` hours, and when its
    severity rises (frost warning -> critical). Clearing is silent.

These are detections from air temperature/humidity and soil moisture, not
certified fire or flood sensors: "fire" is an abnormal heat event, "flood"
is waterlogged soil.
"""
import copy
import math
from collections import deque
from dataclasses import dataclass

KINDS = ('fire', 'flood', 'frost', 'heat', 'drought')
METRICS = ('temperature', 'humidity', 'soil')
RULE_ID_MAX = 40            # LoRa alert payload limit (lora_payload._TEXT_MAX)
_SEVERITY_RANK = {'warning': 1, 'critical': 2}

DEFAULTS = {
    'fire':    {'enabled': True, 'temp_c': 55.0, 'clear_c': 45.0,
                'rise_c': 8.0, 'hum_drop': 15.0, 'window_min': 10},
    'flood':   {'enabled': True, 'soil_pct': 95.0, 'clear_pct': 90.0, 'for_min': 30,
                'action': {'actuator': 'pump1', 'command': 'OFF'}},
    'frost':   {'enabled': True, 'warn_c': 2.0, 'critical_c': 0.0, 'clear_c': 3.0},
    'heat':    {'enabled': True, 'temp_c': 40.0, 'clear_c': 38.0, 'for_min': 10},
    'drought': {'enabled': True, 'soil_pct': 15.0, 'clear_pct': 20.0, 'for_min': 60},
    'renotify_h': 6,
}

_TITLES = {
    'fire': '🔥 Possible fire',
    'flood': '🌊 Soil flooded',
    'frost': '❄️ Frost',
    'heat': '🌡️ Extreme heat',
    'drought': '🏜️ Soil too dry',
}


@dataclass
class Event:
    kind: str
    zone: str
    severity: str           # 'warning' | 'critical'
    message: str
    action: dict | None     # {'actuator': ..., 'command': 'ON'|'OFF'} or None


def merge_config(user) -> dict:
    """DEFAULTS with the user's overrides laid over them, key by key.
    Unknown hazards and malformed sections are ignored."""
    cfg = copy.deepcopy(DEFAULTS)
    if not isinstance(user, dict):
        return cfg
    for kind in KINDS:
        section = user.get(kind)
        if isinstance(section, dict):
            cfg[kind].update(section)
    if isinstance(user.get('renotify_h'), (int, float)):
        cfg['renotify_h'] = user['renotify_h']
    return cfg


def title(kind: str) -> str:
    return _TITLES.get(kind, '⚠️ Hazard')


def rule_id(ev: Event) -> str:
    return f'{ev.kind}-{ev.zone}'[:RULE_ID_MAX]


def parse_rule_id(rid: str):
    """'flood-zone2' -> ('flood', 'zone2'); None for anything that is not a
    hazard id (user rule ids such as 'rain-close')."""
    kind, sep, zone = (rid or '').partition('-')
    if sep and kind in KINDS and zone:
        return kind, zone
    return None


class _ZoneState:
    def __init__(self):
        self.temp = None
        self.hum = None
        self.soil = None
        self.temps = deque()        # (ts, value) within the fire window
        self.hums = deque()
        self.since = {}             # kind -> ts the raise condition started holding
        self.active = {}            # kind -> [severity, last_notified_ts]


class HazardMonitor:
    def __init__(self, config=None):
        self.cfg = merge_config(config)
        self._zones = {}

    def add_reading(self, zone: str, metric: str, value: float, now: float) -> list:
        if metric not in METRICS or not math.isfinite(value):
            return []
        z = self._zones.setdefault(zone, _ZoneState())
        window = self.cfg['fire']['window_min'] * 60
        if metric == 'temperature':
            z.temp = value
            _push_window(z.temps, now, value, window)
            checks = ('fire', 'frost', 'heat')
        elif metric == 'humidity':
            z.hum = value
            _push_window(z.hums, now, value, window)
            checks = ('fire',)
        else:
            z.soil = value
            checks = ('flood', 'drought')

        events = []
        for kind in checks:
            if not self.cfg[kind].get('enabled', True):
                continue
            ev = self._step(kind, zone, z, now)
            if ev:
                events.append(ev)
        return events

    # ── per-hazard evaluation ────────────────────────────────────────────────

    def _step(self, kind, zone, z, now):
        """Works out, for this reading, the severity the hazard would raise at
        (None: raise condition not met), its message, and whether the clear
        condition holds -- then applies the shared state machine."""
        c = self.cfg[kind]
        if kind == 'fire':
            level, msg, clear = self._fire(c, zone, z)
        elif kind == 'frost':
            t = z.temp
            level = ('critical' if t <= c['critical_c'] else
                     'warning' if t <= c['warn_c'] else None)
            msg = f'Frost risk in {zone}: {t:.1f} °C'
            clear = t > c['clear_c']
        elif kind == 'heat':
            level, clear = self._sustained(kind, z, now, z.temp >= c['temp_c'],
                                           z.temp < c['clear_c'], 'critical')
            msg = f'Extreme heat in {zone}: {z.temp:.1f} °C for {c["for_min"]} min'
        elif kind == 'flood':
            level, clear = self._sustained(kind, z, now, z.soil >= c['soil_pct'],
                                           z.soil < c['clear_pct'], 'warning')
            msg = f'Soil in {zone} saturated ({z.soil:.0f} %) for {c["for_min"]} min'
            action = c.get('action')
            if action:
                msg += f' — {action["actuator"]} turned {action["command"]}'
        else:  # drought
            level, clear = self._sustained(kind, z, now, z.soil <= c['soil_pct'],
                                           z.soil > c['clear_pct'], 'warning')
            msg = f'Soil in {zone} very dry ({z.soil:.0f} %) for {c["for_min"]} min'
        return self._transition(kind, zone, z, now, level, msg, clear)

    def _fire(self, c, zone, z):
        if z.temp is None:
            return None, '', True
        rise = z.temp - min(v for _, v in z.temps) if z.temps else 0.0
        drop = (max(v for _, v in z.hums) - z.hum) if (z.hums and z.hum is not None) else None
        fast = drop is not None and rise >= c['rise_c'] and drop >= c['hum_drop']
        if z.temp >= c['temp_c']:
            msg = f'Possible fire in {zone}: {z.temp:.1f} °C'
        else:
            msg = (f'Possible fire in {zone}: temperature rose {rise:.1f} °C and '
                   f'humidity fell {drop or 0:.0f} points in {c["window_min"]} min')
        level = 'critical' if (z.temp >= c['temp_c'] or fast) else None
        clear = z.temp < c['clear_c'] and not fast
        return level, msg, clear

    def _sustained(self, kind, z, now, raise_cond, clear_cond, severity):
        if not raise_cond:
            z.since.pop(kind, None)
            return None, clear_cond
        start = z.since.setdefault(kind, now)
        held = now - start >= self.cfg[kind]['for_min'] * 60
        return (severity if held else None), clear_cond

    def _transition(self, kind, zone, z, now, level, msg, clear):
        active = z.active.get(kind)
        action = self.cfg[kind].get('action') if kind == 'flood' else None
        if active is None:
            if level is None:
                return None
            z.active[kind] = [level, now]
            return Event(kind, zone, level, msg, action)
        if clear:
            del z.active[kind]
            return None
        severity, last = active
        if level and _SEVERITY_RANK[level] > _SEVERITY_RANK[severity]:
            z.active[kind] = [level, now]
            return Event(kind, zone, level, msg, None)
        if now - last >= self.cfg['renotify_h'] * 3600:
            active[1] = now
            return Event(kind, zone, severity, msg, None)
        return None


def _push_window(dq, now, value, window_s):
    dq.append((now, value))
    while dq and now - dq[0][0] > window_s:
        dq.popleft()
