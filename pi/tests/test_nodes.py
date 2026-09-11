# pi/tests/test_nodes.py
import json
import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))

import nodes


@pytest.fixture
def store(tmp_path):
    return str(tmp_path / 'nodes.json')


def _node(mac='20:6e:f1:6c:9d:b0', zone='zone2'):
    return nodes.Node(mac=nodes.normalise_mac(mac), app_key=bytes(range(16)),
                      zone=zone, name='Tomato bed', sleepy=True)


def test_normalise_mac_strips_separators_and_uppercases():
    assert nodes.normalise_mac('20:6e:f1:6c:9d:b0') == '206EF16C9DB0'
    assert nodes.normalise_mac('206ef16c9db0') == '206EF16C9DB0'


def test_normalise_mac_rejects_rubbish():
    for bad in ('', 'zz', '206EF16C9DB', '206EF16C9DB0FF'):
        with pytest.raises(ValueError):
            nodes.normalise_mac(bad)


def test_missing_file_loads_as_empty_not_an_error(store):
    assert nodes.load(store) == {}


def test_an_empty_but_existing_file_loads_as_empty_not_an_error(store):
    # install.sh/first_boot.sh touch this file empty on a fresh Pi that has
    # never had a sensor enrolled -- that state must load fine, not crash
    # every /api/nodes call. Bench-hit for real: DELETE /api/nodes/<mac>
    # 500'd on a freshly deployed Pi because of exactly this.
    with open(store, 'w'):
        pass
    assert nodes.load(store) == {}


def test_whitespace_only_file_loads_as_empty_not_an_error(store):
    with open(store, 'w') as fh:
        fh.write('\n')
    assert nodes.load(store) == {}


def test_add_then_load_round_trips_including_the_key(store):
    nodes.add(_node(), store)
    loaded = nodes.load(store)
    assert set(loaded) == {'206EF16C9DB0'}
    assert loaded['206EF16C9DB0'].app_key == bytes(range(16))
    assert loaded['206EF16C9DB0'].zone == 'zone2'
    assert loaded['206EF16C9DB0'].sleepy is True


def test_adding_the_same_mac_twice_replaces_rather_than_duplicates(store):
    nodes.add(_node(zone='zone2'), store)
    nodes.add(_node(zone='zone9'), store)
    loaded = nodes.load(store)
    assert len(loaded) == 1
    assert loaded['206EF16C9DB0'].zone == 'zone9'


def test_remove_reports_whether_it_removed_anything(store):
    nodes.add(_node(), store)
    assert nodes.remove('206EF16C9DB0', store) is True
    assert nodes.remove('206EF16C9DB0', store) is False
    assert nodes.load(store) == {}


def test_store_is_written_0600_because_it_holds_every_app_key(store):
    nodes.add(_node(), store)
    assert stat.S_IMODE(os.stat(store).st_mode) == 0o600


def test_corrupt_store_raises_loudly_rather_than_silently_losing_sensors(store):
    with open(store, 'w') as fh:
        fh.write('{not json')
    with pytest.raises(nodes.NodeStoreError):
        nodes.load(store)


def test_entry_with_a_bad_key_length_is_rejected(store):
    with open(store, 'w') as fh:
        json.dump({'version': 1, 'nodes': [
            {'mac': '206EF16C9DB0', 'app_key': 'aabb',
             'zone': 'zone2', 'name': 'x', 'sleepy': False}]}, fh)
    with pytest.raises(nodes.NodeStoreError):
        nodes.load(store)
