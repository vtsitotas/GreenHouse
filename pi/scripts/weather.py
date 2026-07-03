#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════════════════
# Greenhouse IoT — weather.py
# Fetches current + forecast weather from Open-Meteo (no API key required),
# publishes readings via local Mosquitto, and evaluates automation rules.
# ═══════════════════════════════════════════════════════════════════════════
import json
import os
import signal
import socket
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ── Config paths ────────────────────────────────────────────────────────────
WEATHER_CFG = '/etc/greenhouse/weather.json'
RULES_CFG   = '/etc/greenhouse/rules.json'
DEVICE_CFG  = '/etc/greenhouse/device.json'
RELOAD_FLAG = '/tmp/greenhouse-weather-reload'
RECORDER_DB = '/var/lib/greenhouse/greenhouse.db'

# ── Open-Meteo endpoint ──────────────────────────────────────────────────────
OPEN_METEO_URL = (
    'https://api.open-meteo.com/v1/forecast'
    '?latitude={lat}&longitude={lon}'
    '&current=temperature_2m,relative_humidity_2m,wind_speed_10m,uv_index'
    '&hourly=temperature_2m,precipitation,uv_index'
    '&forecast_days=2'
    '&timezone=auto'
)

# ── MQTT publish via mosquitto_pub CLI (no paho needed) ─────────────────────
# Port 1883 is loopback-only and anonymous — no TLS or credentials needed.
# The Mosquitto bridge forwards everything to HiveMQ over TLS automatically.
MQTT_HOST = '127.0.0.1'
MQTT_PORT = '1883'

_mqtt_user = None
_mqtt_pass = None

def _load_mqtt_creds():
    global _mqtt_user, _mqtt_pass
    try:
        with open(DEVICE_CFG) as f:
            d = json.load(f)
        _mqtt_user = d['username']
        _mqtt_pass = d['password']
    except Exception as e:
        print(f'[weather] WARN: cannot load device creds: {e}', flush=True)

def mqtt_publish(topic: str, payload: str, retain: bool = False):
    """Publish via loopback port 1883 (anonymous, no TLS — bridge handles the rest)."""
    import subprocess
    cmd = ['mosquitto_pub', '-h', MQTT_HOST, '-p', MQTT_PORT, '-t', topic, '-m', payload]
    if retain:
        cmd.append('-r')
    try:
        subprocess.run(cmd, timeout=10, capture_output=True, check=True)
    except Exception as e:
        print(f'[weather] WARN: mqtt_publish failed: {e}', flush=True)

# ── Location config ──────────────────────────────────────────────────────────
def _pull_location_from_mqtt():
    """Check for a retained location/set message published by the app."""
    import subprocess
    try:
        result = subprocess.run(
            ['mosquitto_sub', '-h', MQTT_HOST, '-p', MQTT_PORT,
             '-t', 'greenhouse/weather/location/set', '-C', '1', '-W', '2'],
            capture_output=True, text=True, timeout=5,
        )
        msg = result.stdout.strip()
        if msg:
            data = json.loads(msg)
            lat, lon = float(data['latitude']), float(data['longitude'])
            cfg = {'latitude': lat, 'longitude': lon,
                   'timezone': data.get('timezone', 'auto')}
            if 'interval_seconds' in data:
                cfg['interval_seconds'] = int(data['interval_seconds'])
            with open(WEATHER_CFG, 'w') as f:
                json.dump(cfg, f)
            print(f'[weather] Config updated from app: {lat},{lon} interval={cfg.get("interval_seconds","default")}', flush=True)
    except Exception as e:
        print(f'[weather] WARN: location pull: {e}', flush=True)


def load_location() -> tuple[float, float, int]:
    try:
        with open(WEATHER_CFG) as f:
            d = json.load(f)
        interval = int(d.get('interval_seconds', INTERVAL))
        return float(d['latitude']), float(d['longitude']), interval
    except Exception as e:
        print(f'[weather] WARN: using default location (Athens): {e}', flush=True)
        return 37.97, 23.72, INTERVAL

# ── Rules ────────────────────────────────────────────────────────────────────
def load_rules() -> list[dict]:
    try:
        with open(RULES_CFG) as f:
            return json.load(f)
    except Exception as e:
        print(f'[weather] WARN: cannot load rules: {e}', flush=True)
        return []

def duration_coverage(values: list, op: str, threshold: float, expected_buckets: int):
    """Given avg values in the lookback window, return (fires, coverage_ratio).

    Fires when coverage is at least 80% of the expected minute-buckets AND
    every present bucket satisfies the condition.
    """
    ops = {
        '>':  lambda a, b: a > b,
        '<':  lambda a, b: a < b,
        '>=': lambda a, b: a >= b,
        '<=': lambda a, b: a <= b,
        '==': lambda a, b: a == b,
    }
    op_fn = ops.get(op)
    if op_fn is None or expected_buckets <= 0:
        return False, 0.0
    coverage = len(values) / expected_buckets
    if not values:
        return False, coverage
    all_match = all(op_fn(v, threshold) for v in values)
    return (coverage >= 0.8 and all_match), coverage


def eval_duration_rule(conn: sqlite3.Connection, zone, metric: str, op: str,
                        threshold: float, duration_minutes: int, now: int) -> bool:
    kind = 'zone' if zone else 'weather'
    row = conn.execute(
        'SELECT id FROM series WHERE kind=? AND zone IS ? AND metric=?',
        (kind, zone, metric)).fetchone()
    if row is None:
        return False
    series_id = row[0]
    cutoff = now - duration_minutes * 60
    result = conn.execute(
        'SELECT avg, ts FROM readings WHERE series_id=? AND ts >= ? ORDER BY ts',
        (series_id, cutoff)).fetchall()
    if not result:
        return False
    values = [r[0] for r in result]

    # One expected bucket per minute of the requested window — coverage must
    # be measured against the full requested duration, not against however
    # dense the returned data happens to be, or the 80% guard can't reject
    # sparse/startup data (e.g. right after a recorder restart).
    expected_buckets = duration_minutes

    fires, _ = duration_coverage(values, op, threshold, expected_buckets=expected_buckets)
    return fires


_last_fired: dict[str, float] = {}  # rule id -> monotonic time last fired


def eval_rules(rules: list, metrics: dict):
    """Evaluate each rule; publish actuator commands and alerts as needed."""
    ops = {
        '>':  lambda a, b: a > b,
        '<':  lambda a, b: a < b,
        '>=': lambda a, b: a >= b,
        '<=': lambda a, b: a <= b,
        '==': lambda a, b: a == b,
    }

    def _fire(rule, message):
        action   = rule['action']
        actuator = action['actuator']
        command  = action['command']
        topic = f'greenhouse/actuators/{actuator}/set'
        print(f'[weather] Rule "{rule.get("name")}" triggered → {topic} {command}', flush=True)
        mqtt_publish(topic, command)
        alert = {
            'type': rule.get('id', 'rule'),
            'message': message,
            'severity': 'warning',
            'rule_id': rule.get('id'),
        }
        mqtt_publish('greenhouse/weather/alert', json.dumps(alert))

    for rule in rules:
        if not rule.get('enabled', True):
            continue
        try:
            trigger  = rule['trigger']
            metric   = trigger['metric']
            op_name  = trigger['op']
            thresh   = float(trigger['value'])
            duration_minutes = trigger.get('duration_minutes')

            if duration_minutes:
                rule_id = rule.get('id', '')
                cooldown_minutes = rule.get('cooldown_minutes', 0)
                last = _last_fired.get(rule_id, 0.0)
                if time.monotonic() - last < cooldown_minutes * 60:
                    continue
                zone, sep, bare_metric = metric.partition('/')
                if not sep:
                    zone, bare_metric = None, metric
                try:
                    conn = sqlite3.connect(f'file:{RECORDER_DB}?mode=ro', uri=True)
                except Exception as e:
                    print(f'[weather] WARN: cannot open recorder DB for duration rule '
                          f'{rule.get("id")}: {e}', flush=True)
                    continue
                try:
                    fired = eval_duration_rule(
                        conn, zone, bare_metric, op_name, thresh,
                        int(duration_minutes), int(time.time()))
                finally:
                    conn.close()
                if fired:
                    _last_fired[rule_id] = time.monotonic()
                    _fire(rule, f'{rule.get("name","Rule")} triggered '
                                f'({metric} {op_name} {thresh} sustained for '
                                f'{int(duration_minutes)} min)')
                continue

            # Live-metric rules (unchanged behavior)
            val = metrics.get(metric)
            if val is None:
                continue
            op_fn = ops.get(op_name)
            if op_fn is None:
                print(f'[weather] WARN: unknown op "{op_name}" in rule {rule.get("id")}', flush=True)
                continue
            if op_fn(val, thresh):
                _fire(rule, f'{rule.get("name","Rule")} triggered '
                            f'({metric} {op_name} {thresh}, current: {val:.1f})')
        except Exception as e:
            print(f'[weather] WARN: rule eval error ({rule.get("id")}): {e}', flush=True)

# ── HTTP fetch ───────────────────────────────────────────────────────────────
def fetch_weather(lat: float, lon: float) -> dict | None:
    url = OPEN_METEO_URL.format(lat=lat, lon=lon)
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'greenhouse-iot/1.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as e:
        print(f'[weather] ERROR fetching weather: {e}', flush=True)
        return None
    except Exception as e:
        print(f'[weather] ERROR unexpected: {e}', flush=True)
        return None

# ── Daily summary ────────────────────────────────────────────────────────────
_last_summary_date: str | None = None

def maybe_send_daily_summary(data: dict, metrics: dict[str, float]):
    global _last_summary_date
    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    if now.hour != 7:
        return
    if _last_summary_date == today:
        return

    try:
        hourly_temps = data['hourly']['temperature_2m'][:24]
        hourly_precip = data['hourly']['precipitation'][:24]
        max_t = max(hourly_temps)
        min_t = min(hourly_temps)
        total_rain = sum(hourly_precip)
        rain_str = f'{total_rain:.1f} mm rain expected' if total_rain > 0.1 else 'dry day'
        summary_msg = (
            f"Today's forecast: {min_t:.0f}–{max_t:.0f} °C, {rain_str}. "
            f"Current: {metrics.get('temperature', 0):.1f} °C, "
            f"wind {metrics.get('wind_kmh', 0):.0f} km/h."
        )
        alert = {
            'type': 'daily_summary',
            'message': summary_msg,
            'severity': 'info',
        }
        mqtt_publish('greenhouse/weather/alert', json.dumps(alert))
        _last_summary_date = today
        print(f'[weather] Daily summary sent: {summary_msg}', flush=True)
    except Exception as e:
        print(f'[weather] WARN: daily summary error: {e}', flush=True)

# ── Frost alert ──────────────────────────────────────────────────────────────
_last_frost_alert: str | None = None  # date string to avoid spam

def maybe_send_frost_alert(data: dict):
    global _last_frost_alert
    today = datetime.now().strftime('%Y-%m-%d')
    if _last_frost_alert == today:
        return
    try:
        # Check next 12 hours for sub-zero temps
        hourly_temps = data['hourly']['temperature_2m'][:12]
        min_t = min(hourly_temps)
        if min_t < 0:
            alert = {
                'type': 'frost',
                'message': f'Frost expected tonight ({min_t:.1f} °C). Frost protection activated.',
                'severity': 'warning',
            }
            mqtt_publish('greenhouse/weather/alert', json.dumps(alert))
            _last_frost_alert = today
            print(f'[weather] Frost alert sent: min_t={min_t}', flush=True)
    except Exception as e:
        print(f'[weather] WARN: frost alert error: {e}', flush=True)

# ── Main loop ────────────────────────────────────────────────────────────────
_running = True

def _handle_signal(sig, frame):
    global _running
    print(f'[weather] Signal {sig} received, stopping.', flush=True)
    _running = False

def _handle_reload(sig, frame):
    """SIGUSR1 signals a rules reload."""
    print('[weather] SIGUSR1: will reload rules on next cycle.', flush=True)
    open(RELOAD_FLAG, 'w').close()

signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT,  _handle_signal)
if hasattr(signal, 'SIGUSR1'):
    signal.signal(signal.SIGUSR1, _handle_reload)

INTERVAL = int(os.environ.get('WEATHER_INTERVAL', 1800))  # seconds

def publish_rules():
    """Publish current rules as retained so app gets them on connect."""
    try:
        with open(RULES_CFG) as f:
            raw = f.read()
        mqtt_publish('greenhouse/rules/current', raw, retain=True)
        print('[weather] Published rules/current (retained)', flush=True)
    except Exception as e:
        print(f'[weather] WARN: could not publish rules: {e}', flush=True)


def run():
    _load_mqtt_creds()
    print(f'[weather] Starting — interval {INTERVAL}s', flush=True)
    _pull_location_from_mqtt()
    publish_rules()

    while _running:
        # Pick up location/interval changes published from the app
        _pull_location_from_mqtt()

        # Check for a reload flag written by the MQTT rules-update handler
        if os.path.exists(RELOAD_FLAG):
            os.remove(RELOAD_FLAG)
            print('[weather] Reloading rules from disk.', flush=True)

        lat, lon, cycle_interval = load_location()
        rules = load_rules()

        data = fetch_weather(lat, lon)
        if data is None:
            print('[weather] Skipping this cycle due to fetch error.', flush=True)
        else:
            try:
                cur = data['current']
                metrics = {
                    'temperature': cur['temperature_2m'],
                    'humidity':    cur['relative_humidity_2m'],
                    'wind_kmh':    cur['wind_speed_10m'],
                    'uv_index':    cur.get('uv_index', 0),
                }
                # Next-hour precipitation from hourly[0]
                hourly = data.get('hourly', {})
                precip_list = hourly.get('precipitation', [0])
                metrics['rain_mm_1h'] = precip_list[0] if precip_list else 0.0

                # Publish individual metrics
                metric_topics = {
                    'temperature': f'{metrics["temperature"]:.1f}',
                    'humidity':    f'{metrics["humidity"]:.0f}',
                    'wind_kmh':    f'{metrics["wind_kmh"]:.1f}',
                    'uv_index':    f'{metrics["uv_index"]:.1f}',
                    'rain_mm_1h':  f'{metrics["rain_mm_1h"]:.2f}',
                }
                for key, val in metric_topics.items():
                    mqtt_publish(f'greenhouse/weather/{key}', val, retain=True)

                print(f'[weather] Published: temp={metrics["temperature"]:.1f}°C '
                      f'hum={metrics["humidity"]:.0f}% '
                      f'wind={metrics["wind_kmh"]:.1f}km/h '
                      f'rain={metrics["rain_mm_1h"]:.2f}mm '
                      f'uv={metrics["uv_index"]:.1f}', flush=True)

                # Publish hourly forecast as JSON for the app chart
                forecast = {
                    'times':   hourly.get('time', [])[:24],
                    'temps':   [round(t, 1) for t in hourly.get('temperature_2m', [])[:24]],
                    'precip':  [round(p, 2) for p in hourly.get('precipitation',  [])[:24]],
                    'uv':      [round(u, 1) for u in hourly.get('uv_index',       [])[:24]],
                }
                mqtt_publish('greenhouse/weather/forecast', json.dumps(forecast), retain=True)

                # Alerts
                maybe_send_frost_alert(data)
                maybe_send_daily_summary(data, metrics)

                # Automation rules
                eval_rules(rules, metrics)

            except KeyError as e:
                print(f'[weather] ERROR: missing key in API response: {e}', flush=True)

        # Sleep in 1-second ticks so SIGTERM is handled promptly
        for _ in range(cycle_interval):
            if not _running:
                break
            time.sleep(1)
            if os.path.exists(RELOAD_FLAG):
                break  # process reload immediately

    print('[weather] Stopped.', flush=True)

if __name__ == '__main__':
    run()
