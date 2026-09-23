import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import lora_payload as lp
import lora_uplink as lu


def test_readings_are_averaged_per_zone_over_the_window():
    agg = lu.ZoneAggregator()
    assert lu.route_reading('greenhouse/zone3/air/temperature', b'20.0', agg)
    assert lu.route_reading('greenhouse/zone3/air/temperature', b'22.0', agg)
    assert lu.route_reading('greenhouse/zone3/soil/moisture', b'40.0', agg)
    assert agg.summaries() == [lp.ZoneSummary(3, 21.0, None, 40.0)]


def test_non_reading_topics_and_non_numeric_zones_are_ignored():
    agg = lu.ZoneAggregator()
    assert not lu.route_reading('greenhouse/weather/temperature', b'30', agg)
    assert not lu.route_reading('greenhouse/Tomatoes/air/temperature', b'30', agg)
    assert not lu.route_reading('greenhouse/zone2/air/temperature', b'nan?', agg)
    assert agg.summaries() == []


def test_reset_starts_a_new_window():
    agg = lu.ZoneAggregator()
    lu.route_reading('greenhouse/zone1/air/humidity', b'55', agg)
    agg.reset()
    assert agg.summaries() == []


def test_alert_limiter_allows_one_per_rule_per_900s():
    lim = lu.AlertLimiter(900)
    assert lim.allow('r1', 0) and not lim.allow('r1', 899) and lim.allow('r1', 900)
    assert lim.allow('r2', 10)


def test_class_c_command_becomes_a_local_actuator_publish():
    evt = {'type': 'rx', 'port': lp.PORT_COMMAND, 'payload': lp.encode_command('pump1', True)}
    assert lu.command_publication(evt) == ('greenhouse/actuators/pump1/set', 'ON')
    assert lu.command_publication({'type': 'rx', 'port': 3, 'payload': b'\x01'}) is None
