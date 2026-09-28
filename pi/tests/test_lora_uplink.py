import os
import sys
import threading

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


def test_critical_hazard_alert_is_sent_as_severity_2():
    lim = lu.AlertLimiter(900)
    port, data = lu.alert_uplink('critical', 'fire-zone1', lim, 0)
    assert port == lp.PORT_ALERT
    assert data[1] == 2
    assert lp.decode_alert(data) == {'severity': 'critical', 'rule_id': 'fire-zone1'}


def test_escalated_alert_is_not_swallowed_by_the_rate_limit():
    lim = lu.AlertLimiter(900)
    assert lu.alert_uplink('warning', 'frost-zone1', lim, 0)
    assert lu.alert_uplink('warning', 'frost-zone1', lim, 60) is None
    assert lu.alert_uplink('critical', 'frost-zone1', lim, 120)


def test_class_c_command_becomes_a_local_actuator_publish():
    evt = {'type': 'rx', 'port': lp.PORT_COMMAND, 'payload': lp.encode_command('pump1', True)}
    assert lu.command_publication(evt) == ('greenhouse/actuators/pump1/set', 'ON')
    assert lu.command_publication({'type': 'rx', 'port': 3, 'payload': b'\x01'}) is None


def test_command_publication_tolerates_a_missing_payload_key():
    assert lu.command_publication({'type': 'rx', 'port': lp.PORT_COMMAND}) is None


def test_aggregator_survives_concurrent_add_and_summaries_reset():
    # add() runs on paho's network thread (on_message -> route_reading) while
    # the main loop calls summaries()/reset() concurrently. A first reading
    # for a brand-new zone during summaries()'s `sorted(self._sums)` used to
    # be able to race a dict resize in add()'s `setdefault`, raising
    # "dictionary changed size during iteration" and killing the service.
    agg = lu.ZoneAggregator()
    stop = threading.Event()

    def writer():
        zone = 1
        while not stop.is_set():
            lu.route_reading(f'greenhouse/zone{zone}/air/temperature', b'20.0', agg)
            zone = zone + 1 if zone < 200 else 1

    t = threading.Thread(target=writer)
    t.start()
    try:
        for _ in range(2000):
            summaries = agg.summaries()
            for s in summaries:
                assert 1 <= s.zone <= 200
            agg.reset()
    finally:
        stop.set()
        t.join()
