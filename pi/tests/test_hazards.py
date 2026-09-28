import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
import hazards

MIN = 60


def feed(mon, zone, metric, values, start=0, step=MIN):
    """Feed one reading per `step` seconds; return every event raised."""
    events = []
    for i, v in enumerate(values):
        events += mon.add_reading(zone, metric, v, start + i * step)
    return events


def kinds(events):
    return [(e.kind, e.severity) for e in events]


# ── fire ─────────────────────────────────────────────────────────────────────

def test_fire_on_absolute_temperature():
    mon = hazards.HazardMonitor()
    events = feed(mon, 'zone1', 'temperature', [30.0, 56.0])
    assert kinds(events) == [('fire', 'critical')]
    assert events[0].zone == 'zone1'
    assert '56' in events[0].message


def test_fire_on_fast_rise_with_humidity_drop():
    mon = hazards.HazardMonitor()
    mon.add_reading('zone1', 'temperature', 22.0, 0)
    mon.add_reading('zone1', 'humidity', 70.0, 0)
    mon.add_reading('zone1', 'temperature', 26.0, 300)
    mon.add_reading('zone1', 'humidity', 60.0, 300)
    events = mon.add_reading('zone1', 'temperature', 31.0, 540)
    events += mon.add_reading('zone1', 'humidity', 52.0, 540)
    assert kinds(events) == [('fire', 'critical')]


def test_fast_rise_without_humidity_drop_is_not_fire():
    # a sunny morning: the air warms fast but humidity barely moves
    mon = hazards.HazardMonitor()
    mon.add_reading('zone1', 'humidity', 70.0, 0)
    events = feed(mon, 'zone1', 'temperature', [18.0, 21.0, 24.0, 27.0], step=180)
    events += mon.add_reading('zone1', 'humidity', 66.0, 540)
    assert events == []


def test_humidity_alone_never_raises_fire():
    mon = hazards.HazardMonitor()
    assert feed(mon, 'zone1', 'humidity', [80.0, 60.0, 40.0, 20.0]) == []


def test_slow_rise_outside_the_window_is_not_fire():
    mon = hazards.HazardMonitor()
    mon.add_reading('zone1', 'humidity', 70.0, 0)
    mon.add_reading('zone1', 'temperature', 20.0, 0)
    mon.add_reading('zone1', 'humidity', 50.0, 30 * MIN)
    assert mon.add_reading('zone1', 'temperature', 30.0, 30 * MIN) == []


# ── flood ────────────────────────────────────────────────────────────────────

def test_flood_needs_30_minutes_and_carries_the_pump_off_action():
    mon = hazards.HazardMonitor()
    early = feed(mon, 'zone2', 'soil', [96.0] * 30)          # t = 0 .. 29 min
    assert early == []
    events = mon.add_reading('zone2', 'soil', 97.0, 30 * MIN)
    assert kinds(events) == [('flood', 'warning')]
    assert events[0].action == {'actuator': 'pump1', 'command': 'OFF'}


def test_flood_timer_restarts_when_the_soil_drains():
    mon = hazards.HazardMonitor()
    feed(mon, 'zone2', 'soil', [96.0] * 20)
    mon.add_reading('zone2', 'soil', 80.0, 20 * MIN)
    assert feed(mon, 'zone2', 'soil', [96.0] * 20, start=21 * MIN) == []


def test_sustained_condition_survives_a_reporting_gap():
    mon = hazards.HazardMonitor()
    mon.add_reading('zone2', 'soil', 96.0, 0)
    # node silent for 40 minutes, comes back still saturated
    assert kinds(mon.add_reading('zone2', 'soil', 96.0, 40 * MIN)) == [('flood', 'warning')]


# ── frost ────────────────────────────────────────────────────────────────────

def test_frost_warning_then_critical_escalates_once():
    mon = hazards.HazardMonitor()
    events = feed(mon, 'zone1', 'temperature', [5.0, 1.5, 1.0, -0.5, -1.0, 1.0])
    assert kinds(events) == [('frost', 'warning'), ('frost', 'critical')]


def test_frost_does_not_flap_around_the_threshold():
    mon = hazards.HazardMonitor()
    events = feed(mon, 'zone1', 'temperature', [1.9, 2.1, 1.9, 2.1, 2.9, 1.9])
    assert kinds(events) == [('frost', 'warning')]


# ── heat, drought ────────────────────────────────────────────────────────────

def test_heat_needs_10_minutes():
    mon = hazards.HazardMonitor()
    assert feed(mon, 'zone1', 'temperature', [41.0] * 10) == []
    events = mon.add_reading('zone1', 'temperature', 41.0, 10 * MIN)
    assert kinds(events) == [('heat', 'critical')]


def test_drought_needs_60_minutes():
    mon = hazards.HazardMonitor()
    assert feed(mon, 'zone3', 'soil', [10.0] * 60) == []
    assert kinds(mon.add_reading('zone3', 'soil', 10.0, 60 * MIN)) == [('drought', 'warning')]


# ── notification behaviour ───────────────────────────────────────────────────

def test_active_hazard_renotifies_after_6_hours_only():
    mon = hazards.HazardMonitor()
    assert len(mon.add_reading('zone1', 'temperature', -1.0, 0)) == 1
    assert mon.add_reading('zone1', 'temperature', -1.0, 5 * 3600) == []
    assert kinds(mon.add_reading('zone1', 'temperature', -1.0, 6 * 3600)) == [('frost', 'critical')]
    assert mon.add_reading('zone1', 'temperature', -1.0, 7 * 3600) == []


def test_hazard_clears_and_can_raise_again():
    mon = hazards.HazardMonitor()
    events = feed(mon, 'zone1', 'temperature', [1.0, 4.0, 1.0])
    assert kinds(events) == [('frost', 'warning'), ('frost', 'warning')]


def test_two_zones_are_independent():
    mon = hazards.HazardMonitor()
    a = mon.add_reading('zone1', 'temperature', 60.0, 0)
    b = mon.add_reading('zone2', 'temperature', 60.0, 0)
    assert kinds(a) == kinds(b) == [('fire', 'critical')]
    assert hazards.rule_id(a[0]) != hazards.rule_id(b[0])


# ── configuration ────────────────────────────────────────────────────────────

def test_disabled_hazard_never_fires():
    mon = hazards.HazardMonitor({'frost': {'enabled': False}})
    assert feed(mon, 'zone1', 'temperature', [-5.0, -6.0]) == []


def test_user_config_overrides_one_threshold_and_keeps_the_rest():
    cfg = hazards.merge_config({'heat': {'temp_c': 35.0}, 'bogus': {'x': 1}})
    assert cfg['heat']['temp_c'] == 35.0
    assert cfg['heat']['for_min'] == hazards.DEFAULTS['heat']['for_min']
    assert cfg['fire'] == hazards.DEFAULTS['fire']
    assert 'bogus' not in cfg
    # the defaults themselves are untouched
    assert hazards.DEFAULTS['heat']['temp_c'] == 40.0


def test_merge_config_tolerates_garbage():
    assert hazards.merge_config('nonsense') == hazards.merge_config(None)
    assert hazards.merge_config({'heat': 'nope'})['heat'] == hazards.DEFAULTS['heat']


def test_non_finite_values_are_ignored():
    mon = hazards.HazardMonitor()
    assert mon.add_reading('zone1', 'temperature', float('nan'), 0) == []
    assert mon.add_reading('zone1', 'temperature', float('inf'), 0) == []
    assert mon.add_reading('zone1', 'pressure', 99.0, 0) == []


# ── helpers ──────────────────────────────────────────────────────────────────

def test_rule_id_round_trips_and_fits_lora():
    ev = hazards.Event('flood', 'zone12', 'warning', 'm', None)
    rid = hazards.rule_id(ev)
    assert rid == 'flood-zone12'
    assert hazards.parse_rule_id(rid) == ('flood', 'zone12')
    long_zone = hazards.Event('fire', 'z' * 80, 'critical', 'm', None)
    assert len(hazards.rule_id(long_zone)) <= 40
    assert hazards.parse_rule_id('rain-close') is None
    assert hazards.parse_rule_id('fire-') is None


def test_every_kind_has_a_title():
    for kind in hazards.KINDS:
        assert hazards.title(kind)
