# pi/tests/test_portal_nodes.py
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'portal'))

import nodes
import portal

KEY = bytes(range(16)).hex()
MAC = '206EF16C9DB0'


def _auth():
    # Mocking or getting the API token properly since portal._api_token doesn't exist
    return {'Authorization': f'Bearer test_token'}

def _mock_require_api_token():
    from flask import request
    return request.headers.get('Authorization') == 'Bearer test_token'

@pytest.fixture
def client(tmp_path, monkeypatch):
    store = str(tmp_path / 'nodes.json')
    monkeypatch.setattr(portal, 'NODES_PATH', store, raising=False)
    monkeypatch.setattr(portal, '_require_api_token', _mock_require_api_token, raising=False)
    monkeypatch.setattr(portal, '_clear_retained', lambda topics: None)   # never touch a real broker
    portal.app.config['TESTING'] = True
    with portal.app.test_client() as c:
        c._store = store
        yield c


def test_post_requires_a_token(client):
    r = client.post('/api/nodes', json={'mac': MAC, 'key': KEY,
                                        'zone': 'zone2', 'name': 'x', 'sleepy': True})
    assert r.status_code == 401


def test_post_enrols_a_sensor(client):
    r = client.post('/api/nodes', headers=_auth(),
                    json={'mac': MAC, 'key': KEY, 'zone': 'zone2',
                          'name': 'Tomato bed', 'sleepy': True})
    assert r.status_code == 201
    assert MAC in nodes.load(client._store)


def test_post_rejects_a_key_that_is_not_16_bytes(client):
    r = client.post('/api/nodes', headers=_auth(),
                    json={'mac': MAC, 'key': 'aabb', 'zone': 'z',
                          'name': 'x', 'sleepy': False})
    assert r.status_code == 400


def test_post_rejects_a_malformed_mac(client):
    r = client.post('/api/nodes', headers=_auth(),
                    json={'mac': 'nope', 'key': KEY, 'zone': 'z',
                          'name': 'x', 'sleepy': False})
    assert r.status_code == 400


def test_get_never_leaks_app_keys(client):
    client.post('/api/nodes', headers=_auth(),
                json={'mac': MAC, 'key': KEY, 'zone': 'zone2',
                      'name': 'Tomato bed', 'sleepy': True})
    r = client.get('/api/nodes', headers=_auth())
    assert r.status_code == 200
    assert KEY not in r.get_data(as_text=True)
    assert 'key' not in r.get_json()['nodes'][0]
    assert r.get_json()['nodes'][0]['zone'] == 'zone2'


def test_delete_removes_and_is_idempotent(client):
    client.post('/api/nodes', headers=_auth(),
                json={'mac': MAC, 'key': KEY, 'zone': 'z', 'name': 'x',
                      'sleepy': False})
    assert client.delete(f'/api/nodes/{MAC}', headers=_auth()).status_code == 204
    assert client.delete(f'/api/nodes/{MAC}', headers=_auth()).status_code == 404


def test_leaf_only_refused_for_firmware_without_caps(client, monkeypatch):
    monkeypatch.setattr(portal, 'read_node_caps', lambda: {MAC: 0})
    r = client.post('/api/nodes', headers=_auth(),
                    json={'mac': MAC, 'key': KEY, 'zone': 'zone2', 'leaf_only': True})
    assert r.status_code == 409
    assert MAC not in nodes.load(client._store)


def test_leaf_only_accepted_for_capable_firmware(client, monkeypatch):
    monkeypatch.setattr(portal, 'read_node_caps', lambda: {MAC: 1})
    r = client.post('/api/nodes', headers=_auth(),
                    json={'mac': MAC, 'key': KEY, 'zone': 'zone2', 'leaf_only': True})
    assert r.status_code == 201
    assert nodes.load(client._store)[MAC].leaf_only is True


MAC2 = '206EF16C75EC'


@pytest.fixture
def cleared(monkeypatch):
    """Capture what the DELETE path asks the broker to clear (no real MQTT)."""
    topics = []
    monkeypatch.setattr(portal, '_clear_retained', lambda ts: topics.extend(ts))
    return topics


def _enrol(client, mac, zone):
    client.post('/api/nodes', headers=_auth(),
                json={'mac': mac, 'key': KEY, 'zone': zone, 'name': zone, 'sleepy': True})


def test_delete_clears_every_per_node_topic(client, cleared):
    _enrol(client, MAC, 'tomatoes')
    client.delete(f'/api/nodes/{MAC}', headers=_auth())
    for sub in ('status', 'battery', 'mesh'):
        assert f'greenhouse/nodes/{MAC}/{sub}' in cleared


def test_delete_of_the_last_sensor_in_a_zone_clears_its_readings(client, cleared):
    # Otherwise the zone lingers on the Dashboard from its retained readings.
    _enrol(client, MAC, 'tomatoes')
    client.delete(f'/api/nodes/{MAC}', headers=_auth())
    for t in ('greenhouse/tomatoes/air/temperature', 'greenhouse/tomatoes/air/humidity',
              'greenhouse/tomatoes/soil/moisture'):
        assert t in cleared


def test_delete_keeps_readings_of_a_zone_another_sensor_still_reports(client, cleared):
    _enrol(client, MAC, 'tomatoes')
    _enrol(client, MAC2, 'tomatoes')
    client.delete(f'/api/nodes/{MAC}', headers=_auth())
    assert not any(t.startswith('greenhouse/tomatoes/') for t in cleared)
    assert f'greenhouse/nodes/{MAC}/mesh' in cleared


def test_delete_of_an_unknown_mac_still_clears_its_leftover_topics(client, cleared):
    # A device the Pi already forgot can still linger in the app from retained
    # topics (e.g. an earlier clear that never reached the broker). Pressing
    # delete again must remove the ghost, not just answer 404.
    r = client.delete(f'/api/nodes/{MAC}', headers=_auth())
    assert r.status_code == 404
    assert f'greenhouse/nodes/{MAC}/mesh' in cleared


def test_clear_node_retained_sends_all_topics_in_one_flushed_batch(monkeypatch):
    # Regression: publishing three messages then disconnecting immediately,
    # with no network loop running, delivered only the first one.
    batches = []
    monkeypatch.setattr(portal.mqtt_publish, 'multiple',
                        lambda msgs, **kw: batches.append((list(msgs), kw)))
    portal.clear_node_retained(MAC)
    assert len(batches) == 1
    msgs, kw = batches[0]
    assert {m[0] for m in msgs} == {f'greenhouse/nodes/{MAC}/{s}' for s in ('status', 'battery', 'mesh')}
    assert all(m[1] == '' and m[3] is True for m in msgs)     # empty + retained = delete
    assert kw['hostname'] == '127.0.0.1'
