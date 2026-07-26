# pi/tests/test_simulator_mesh.py
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
import simulator


def test_build_mesh_payload_round_trips_json():
    raw = simulator.build_mesh_payload(
        node="node1", parent="A0B1C2D3E4F5", rank=1, rssi=-61,
        sleepy=False, battery_mv=3312, zone="zone1")
    data = json.loads(raw)
    assert data["parent"] == "A0B1C2D3E4F5"
    assert data["rank"] == 1
    assert data["rssi"] == -61
    assert data["sleepy"] is False
    assert data["battery_mv"] == 3312
    assert data["zone"] == "zone1"


def test_build_mesh_payload_key_set_matches_contract():
    raw = simulator.build_mesh_payload(
        node="node1", parent="A0B1C2D3E4F5", rank=1, rssi=-61,
        sleepy=False, battery_mv=3312, zone="zone1")
    data = json.loads(raw)
    assert set(data.keys()) == {"parent", "rank", "rssi", "sleepy", "battery_mv", "zone"}


def test_build_mesh_payload_null_parent_for_bridge():
    raw = simulator.build_mesh_payload(
        node="A0B1C2D3E4F5", parent=None, rank=0, rssi=None,
        sleepy=False, battery_mv=None, zone=None)
    data = json.loads(raw)
    assert data["parent"] is None
    assert data["rank"] == 0
    assert data["rssi"] is None
    assert data["battery_mv"] is None
    assert data["zone"] is None


def test_build_mesh_payload_null_rssi_handling():
    raw = simulator.build_mesh_payload(
        node="node1", parent="A0B1C2D3E4F5", rank=1, rssi=None,
        sleepy=False, battery_mv=3312, zone="zone1")
    data = json.loads(raw)
    assert data["rssi"] is None


def test_build_mesh_payload_null_battery_mv_handling():
    raw = simulator.build_mesh_payload(
        node="node1", parent="A0B1C2D3E4F5", rank=1, rssi=-61,
        sleepy=False, battery_mv=None, zone="zone1")
    data = json.loads(raw)
    assert data["battery_mv"] is None


def test_build_mesh_payload_sleepy_true():
    raw = simulator.build_mesh_payload(
        node="node2", parent="node1", rank=2, rssi=-70,
        sleepy=True, battery_mv=3200, zone="zone2")
    data = json.loads(raw)
    assert data["sleepy"] is True


def test_build_mesh_payload_bridge_record_shape():
    raw = simulator.build_mesh_payload(
        node=simulator.BRIDGE, parent=None, rank=0, rssi=None,
        sleepy=False, battery_mv=None, zone=None)
    data = json.loads(raw)
    assert data["parent"] is None
    assert data["rank"] == 0
    assert data["sleepy"] is False


def test_bridge_constant_matches_contract_mac():
    assert simulator.BRIDGE == "A0B1C2D3E4F5"


def test_base_topology_ranks_and_parents():
    assert simulator.NODE_PARENT["node1"] == simulator.BRIDGE
    assert simulator.NODE_RANK["node1"] == 1
    assert simulator.NODE_PARENT["node2"] == "node1"
    assert simulator.NODE_RANK["node2"] == 2
    assert simulator.NODE_SLEEPY["node2"] is True
    assert simulator.NODE_PARENT["node3"] == simulator.BRIDGE
    assert simulator.NODE_RANK["node3"] == 1


def test_node_zone_mapping_matches_zones_list():
    assert simulator.NODE_ZONE["node1"] == "zone1"
    assert simulator.NODE_ZONE["node2"] == "zone2"
    assert simulator.NODE_ZONE["node3"] == "zone3"


def test_walk_rssi_stays_within_bounds():
    rssi = -65
    for _ in range(1000):
        rssi = simulator.walk_rssi(rssi)
        assert -85 <= rssi <= -45


def test_battery_mv_from_pct():
    assert simulator.battery_mv_from_pct(100.0) == 2800 + 100 * 6
    assert simulator.battery_mv_from_pct(0.0) == 2800
