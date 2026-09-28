import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import hazard_monitor as hm
import hazards


class Recorder:
    def __init__(self):
        self.published = []
        self.pushed = []

    def publish(self, topic, payload):
        self.published.append((topic, payload))

    def push(self, title, body):
        self.pushed.append((title, body))


def run(monitor, rec, topic, payload, retain=False, now=1000.0, push_enabled=True):
    return hm.handle(monitor, topic, payload, retain, now,
                     rec.publish, rec.push, push_enabled)


def test_zone_readings_are_parsed_and_weather_and_sites_are_not():
    assert hm.parse_reading('greenhouse/zone1/air/temperature', b'21.5') == ('zone1', 'temperature', 21.5)
    assert hm.parse_reading('greenhouse/zone2/air/humidity', b'60') == ('zone2', 'humidity', 60.0)
    assert hm.parse_reading('greenhouse/zone3/soil/moisture', b'44') == ('zone3', 'soil', 44.0)
    assert hm.parse_reading('greenhouse/weather/air/temperature', b'30') is None
    assert hm.parse_reading('greenhouse/sites/farm/zone1/air/temperature', b'30') is None
    assert hm.parse_reading('greenhouse/zone1/air/pressure', b'1000') is None


def test_garbage_payload_is_ignored():
    rec = Recorder()
    mon = hazards.HazardMonitor()
    assert hm.parse_reading('greenhouse/zone1/air/temperature', b'hot') is None
    assert hm.parse_reading('greenhouse/zone1/air/temperature', b'\xff\xfe') is None
    assert run(mon, rec, 'greenhouse/zone1/air/temperature', b'nan') == []
    assert rec.published == [] and rec.pushed == []


def test_retained_readings_are_ignored():
    rec = Recorder()
    mon = hazards.HazardMonitor()
    assert run(mon, rec, 'greenhouse/zone1/air/temperature', b'80', retain=True) == []
    assert rec.published == []


def test_a_raised_hazard_publishes_the_alert_the_action_and_a_push():
    rec = Recorder()
    mon = hazards.HazardMonitor({'flood': {'for_min': 0}})
    events = run(mon, rec, 'greenhouse/zone2/soil/moisture', b'98')
    assert [e.kind for e in events] == ['flood']

    (alert_topic, alert_raw), (action_topic, action_payload) = rec.published
    assert alert_topic == 'greenhouse/weather/alert'
    alert = json.loads(alert_raw)
    assert alert['type'] == 'hazard-flood'
    assert alert['rule_id'] == 'flood-zone2'
    assert alert['severity'] == 'warning'
    assert alert['zone'] == 'zone2'
    assert alert['ts'] == 1000
    assert 'zone2' in alert['message']
    assert (action_topic, action_payload) == ('greenhouse/actuators/pump1/set', 'OFF')

    [(title, body)] = rec.pushed
    assert title == hazards.title('flood') + ' — zone2'
    assert body == alert['message']


def test_push_is_skipped_when_hazard_alerts_are_off():
    rec = Recorder()
    mon = hazards.HazardMonitor()
    run(mon, rec, 'greenhouse/zone1/air/temperature', b'70', push_enabled=False)
    assert len(rec.published) == 1      # the alert itself still reaches the app/LoRa
    assert rec.pushed == []


def test_hazard_push_setting_is_read_from_the_settings_payload():
    assert hm.hazard_push_enabled(b'{"hazard_alerts": false}') is False
    assert hm.hazard_push_enabled(b'{"frost_forecast": true}') is True
    assert hm.hazard_push_enabled(b'not json') is None
    assert hm.hazard_push_enabled(b'') is None


def test_load_config_falls_back_to_defaults(tmp_path):
    assert hm.load_config(str(tmp_path / 'missing.json')) == hazards.merge_config(None)
    bad = tmp_path / 'bad.json'
    bad.write_text('{nope')
    assert hm.load_config(str(bad)) == hazards.merge_config(None)
    good = tmp_path / 'hazards.json'
    good.write_text('{"heat": {"temp_c": 35}}')
    assert hm.load_config(str(good))['heat']['temp_c'] == 35
