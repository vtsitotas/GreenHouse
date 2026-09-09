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
