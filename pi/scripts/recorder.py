#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════════════════
# Greenhouse IoT — recorder.py
# Subscribes to sensor/weather MQTT topics and writes minute-resolution
# history to a local SQLite database, with hourly rollups and retention.
# ═══════════════════════════════════════════════════════════════════════════
import json
import os
import signal
import sqlite3
import time

import paho.mqtt.client as mqtt

RECORDER_CFG = '/etc/greenhouse/recorder.json'
MQTT_HOST = '127.0.0.1'
MQTT_PORT = 1883

DEFAULT_CONFIG = {
    'db_path': '/var/lib/greenhouse/greenhouse.db',
    'flush_seconds': 60,
    'raw_days': 90,
    'hourly_days': 730,
}

SUBSCRIBE_TOPICS = [
    'greenhouse/+/air/temperature',
    'greenhouse/+/air/humidity',
    'greenhouse/+/soil/moisture',
    'greenhouse/+/light/lux',
    'greenhouse/weather/+',
]

# ── Minute-bucket buffer ─────────────────────────────────────────────────────
class MinuteBucketBuffer:
    """Accumulates readings into per-(series, minute) avg/min/max/count buckets.

    Never writes to disk itself — the caller flushes ready buckets on a timer.
    """

    def __init__(self):
        self._buckets = {}  # (series_key, minute_ts) -> [sum, min, max, n]

    def add(self, series_key: tuple, timestamp: int, value: float):
        minute_ts = timestamp - (timestamp % 60)
        key = (series_key, minute_ts)
        if key not in self._buckets:
            self._buckets[key] = [value, value, value, 1]
        else:
            b = self._buckets[key]
            b[0] += value
            b[1] = min(b[1], value)
            b[2] = max(b[2], value)
            b[3] += 1

    def flush_ready(self, now: int) -> list:
        """Return and remove buckets whose minute has fully elapsed."""
        ready = []
        for key in list(self._buckets.keys()):
            series_key, minute_ts = key
            if minute_ts + 60 <= now:
                total, mn, mx, n = self._buckets.pop(key)
                ready.append((series_key, minute_ts, total / n, mn, mx, n))
        return ready

    def flush_all(self) -> list:
        """Return and remove every bucket regardless of elapsed time (shutdown path)."""
        ready = []
        for (series_key, minute_ts), (total, mn, mx, n) in self._buckets.items():
            ready.append((series_key, minute_ts, total / n, mn, mx, n))
        self._buckets.clear()
        return ready


if __name__ == '__main__':
    pass  # run() is added in Task 4
