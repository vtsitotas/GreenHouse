import base64
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import hazards
import lora_gateway_bridge as gb
import lora_payload as lp

CFG = {'application_id': 'app-1', 'sites': {'ac1f09fffe000001': 'north'}}


def _up(fport, data):
    return json.dumps({'fPort': fport, 'data': base64.b64encode(data).decode(),
                       'rxInfo': [{'rssi': -112, 'snr': -7.5}]}).encode()


def test_uplink_is_parsed_from_topic_and_body():
    raw = _up(1, b'\x01\x00')
    up = gb.parse_uplink('application/app-1/device/AC1F09FFFE000001/event/up', raw)
    assert up == {'dev_eui': 'ac1f09fffe000001', 'fport': 1, 'data': b'\x01\x00',
                  'rssi': -112, 'snr': -7.5}


def test_summary_becomes_site_scoped_retained_readings():
    data = lp.encode_summaries([lp.ZoneSummary(3, 21.5, None, 40.0)])[0]
    pubs = gb.uplink_publications('north', {'fport': 1, 'data': data, 'rssi': -112,
                                            'snr': -7.5}, now=1700000000)
    topics = {t: p for t, p, _r in pubs}
    assert topics['greenhouse/sites/north/zone3/air/temperature'] == '21.5'
    assert topics['greenhouse/sites/north/zone3/soil/moisture'] == '40.0'
    assert 'greenhouse/sites/north/zone3/air/humidity' not in topics
    assert json.loads(topics['greenhouse/sites/north/lora'])['rssi'] == -112


def test_alert_becomes_site_alert(monkeypatch):
    pushed = []
    monkeypatch.setattr(gb, '_push', lambda title, body: pushed.append((title, body)))
    pubs = gb.uplink_publications('north', {'fport': 2, 'data': lp.encode_alert('warning', 'dry'),
                                            'rssi': -100, 'snr': 1}, now=1)
    assert any(t == 'greenhouse/sites/north/weather/alert' for t, _p, _r in pubs)
    assert pushed and 'north' in pushed[0][0]


def test_hazard_alert_from_a_remote_site_gets_a_readable_push(monkeypatch):
    pushed = []
    monkeypatch.setattr(gb, '_push', lambda title, body: pushed.append((title, body)))
    pubs = gb.uplink_publications('north', {'fport': 2, 'data': lp.encode_alert('critical', 'fire-zone3'),
                                            'rssi': -100, 'snr': 1}, now=5)
    alert = json.loads(dict((t, p) for t, p, _r in pubs)['greenhouse/sites/north/weather/alert'])
    assert alert['type'] == 'hazard-fire'
    assert alert['zone'] == 'zone3'
    assert alert['severity'] == 'critical'
    [(title, body)] = pushed
    assert title == hazards.title('fire') + ' — north / zone3'
    assert 'Rule' not in body and 'critical' in body


def test_site_command_becomes_chirpstack_downlink():
    topic, body = gb.downlink_request('greenhouse/sites/north/actuators/pump1/set', b'ON', CFG)
    assert topic == 'application/app-1/device/ac1f09fffe000001/command/down'
    msg = json.loads(body)
    assert msg['fPort'] == lp.PORT_COMMAND and msg['confirmed'] is False
    assert lp.decode_command(base64.b64decode(msg['data'])) == ('pump1', True)


def test_unknown_site_command_is_dropped():
    assert gb.downlink_request('greenhouse/sites/south/actuators/pump1/set', b'ON', CFG) is None


# --- robustness beyond the brief (see global-constraints.md point 4): a
# malformed input must never raise out of the MQTT on_message callback ---

def test_uplink_with_non_object_body_is_dropped():
    raw = json.dumps(['not', 'an', 'object']).encode()
    assert gb.parse_uplink('application/app-1/device/AC1F09FFFE000001/event/up', raw) is None


def test_uplink_with_non_object_rxinfo_entry_is_dropped():
    raw = json.dumps({'fPort': 1, 'data': base64.b64encode(b'\x01\x00').decode(),
                      'rxInfo': ['not-an-object']}).encode()
    assert gb.parse_uplink('application/app-1/device/AC1F09FFFE000001/event/up', raw) is None


def test_uplink_with_bad_base64_data_is_dropped():
    raw = json.dumps({'fPort': 1, 'data': '***not-base64***', 'rxInfo': [{}]}).encode()
    assert gb.parse_uplink('application/app-1/device/AC1F09FFFE000001/event/up', raw) is None


def test_uplink_with_undecodable_summary_payload_does_not_raise():
    # Right fPort, but the bytes don't decode as a valid summary payload.
    pubs = gb.uplink_publications('north', {'fport': 1, 'data': b'\xff\xff\xff',
                                            'rssi': -112, 'snr': -7.5}, now=1)
    assert [t for t, _p, _r in pubs] == ['greenhouse/sites/north/lora']
