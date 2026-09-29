"""Parameter catalogue: parsed values match the firmware, derived values match
hand calculations and the numbers already published in the repo."""
import json
import math

import pytest

from meshsim import analytic as an
from meshsim import firmware_params, params


@pytest.fixture(scope="module")
def cat():
    return params.build()


def test_snapshot_matches_live_sources():
    live = firmware_params.parse_live()
    snap = json.loads(firmware_params.SNAPSHOT.read_text(encoding="utf-8"))
    for section in ("defines", "structs", "literals", "python", "planned"):
        assert live[section] == snap[section], f"re-run `python -m meshsim snapshot` ({section})"


@pytest.mark.parametrize("key,value", [
    ("MESH_PACKET_LEN", 61), ("sizeof(MeshBeacon)", 27), ("sizeof(MeshAck)", 20),
    ("sizeof(MeshJoinBeacon)", 8), ("MESH_PROVISION_LEN", 33), ("MESH_INFLIGHT_MAX", 11),
    ("MESH_DATA_BUFFER_SIZE", 10), ("MESH_DEDUP_CACHE_SIZE", 32), ("MESH_MAX_TTL", 64),
    ("sizeof(MeshRtcState)", 632), ("TX_FAIL_DROP_COUNT", 3), ("BATT_ADC_SAMPLES", 8),
])
def test_firmware_values(cat, key, value):
    assert cat[key] == value


def test_every_param_has_a_source(cat):
    for p in cat.params.values():
        assert p.source, p.key
        assert p.kind in params.KINDS, p.key


@pytest.mark.parametrize("length,us", [(8, 600), (20, 696), (27, 752), (29, 768),
                                       (33, 800), (61, 1024)])
def test_airtime(length, us):
    assert an.airtime_us(length) == us


def test_exchange_and_ack_airtime():
    assert an.ack_airtime_us() == 304
    assert an.unicast_exchange_us(61) == 50 + 1024 + 10 + 304


def test_energy_reproduces_cart_sim_results(cat):
    # docs/analysis/cart_sim_results.txt lines 81 and 91
    assert cat["MAH_DAY_PHASE1_LEAF_T300"] == 18.79
    assert cat["MAH_DAY_PHASE1_LEAF_T900"] == 7.26


def test_uart_line(cat):
    assert cat["UART_FRAME_LINE_B"] == 150
    assert cat["BRIDGE_MAX_FRAMES_S"] == pytest.approx(115200 / 1500, abs=0.01)


def test_ttl_ceiling_rank17():
    args = (2, 16, 2, 16, 16)
    assert an.data_delivers(17, 2, 16) and not an.data_delivers(18, 2, 16)
    assert an.ack_reaches(17, 2, 16, 16) and not an.ack_reaches(18, 2, 16, 16)
    assert an.depth_ceiling(*args) == 17


def test_flood_count_closed_form():
    # Σ_{r=1..17} 10·10·min(16, r+2) and Σ_{r=1..50} 10·10·min(50, r+2)
    assert an.flood_rebroadcasts(50, 10, 2, 16, 2, 16, 16) == \
        100 * sum(min(16, r + 2) for r in range(1, 18)) == 18100
    assert an.flood_rebroadcasts(50, 10, 2, 255, 2, 255, 255) == \
        100 * sum(min(50, r + 2) for r in range(1, 51)) == 137200


def test_sensitivity_anchor():
    ber = an.ber_at_sensitivity()
    assert ber == pytest.approx(1.0178e-5, rel=1e-3)
    assert an.dbpsk_ber(an.dbpsk_ebn0_db_for_ber(ber)) == pytest.approx(ber, rel=1e-9)
    # a 1024-byte PSDU at exactly the sensitivity must fail 8 % of the time
    psdu_payload = 1024 - an.ESPNOW_OVERHEAD_BYTES
    assert an.frame_error_rate(-98.4, psdu_payload) == pytest.approx(0.08, rel=1e-6)


def test_csma_matches_cart_sim_b1():
    assert an.csma_first_slot_collision(2) == pytest.approx(0.0625)
    assert an.csma_first_slot_collision(4) == pytest.approx(0.1211, abs=5e-4)
    assert an.csma_first_slot_collision(6) == pytest.approx(0.1777, abs=5e-4)


def test_md1k_blocking_k1():
    for rho in (0.3, 1.0, 2.5):
        assert an.md1k_blocking(rho, 1) == pytest.approx(rho / (1 + rho))


def test_every_parameter_is_explained(cat):
    from meshsim import config, explain
    missing = [k for k in cat.params if explain.for_param(k) is None]
    missing += [k for k, *_ in config.SPEC if k not in explain.CONFIG]
    assert not missing, f"add a plain-language explanation to meshsim/explain.py for: {missing}"
    for what, why in list(explain.CATALOGUE.values()) + list(explain.CONFIG.values()):
        assert what and why and "|" not in what + why


def test_markdown_renders(cat):
    md = params.to_markdown(cat)
    assert "Ταβάνι βάθους" in md and "Flood ACK" in md
    assert md.count("\n## ") >= len(params.GROUPS)
