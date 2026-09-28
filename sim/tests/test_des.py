"""DES: deterministic, agrees with closed forms where they are exact, and with
the calculator where both model the same thing."""
import pytest

from meshsim import analytic as an
from meshsim import config, des, params


@pytest.fixture(scope="module")
def cat():
    return params.build()


def sim(cat, preset, **over):
    cfg = config.resolve(cat, preset, {k.replace("__", "."): v for k, v in over.items()})
    return des.simulate(cfg, cat)


def test_deterministic(cat):
    a = sim(cat, "greenhouse", des__cycles=3)
    b = sim(cat, "greenhouse", des__cycles=3)
    assert a["summary"] == b["summary"]


def test_single_hop_without_contention_never_collides(cat):
    res = sim(cat, "greenhouse", net__ranks=1, net__per_rank=1, des__cycles=20)
    assert res["summary"]["p_collision_per_attempt"] == 0
    assert res["summary"]["pdr_settled"] == 1.0


def test_chain_hidden_terminals_collide(cat):
    # r+1 → r and r−1 → r−2 cannot hear each other but r hears both: a real chain effect
    res = sim(cat, "greenhouse", net__ranks=8, net__per_rank=1, net__hidden_frac=0, des__cycles=20,
              sync__step_per_300s=1e-7)
    assert res["summary"]["p_collision_per_attempt"] > 0


def test_ttl_ceiling_in_simulation(cat):
    # drift ~0 so that only the TTL rule decides who gets through
    kw = dict(net__ranks=20, net__per_rank=1, net__hidden_frac=0, des__cycles=3,
              scheme__technique="T2-window", sync__step_per_300s=1e-7, des__window_s=8.0)
    res = sim(cat, "greenhouse", **kw)
    deep = {x["rank"]: x["delivered"] for x in res["per_rank"]}
    assert all(deep[r] == 0 for r in range(18, 21))
    res64 = sim(cat, "greenhouse", scheme__max_ttl=64, **kw)
    assert sum(x["delivered"] for x in res64["per_rank"] if x["rank"] >= 18) > 0


def test_mac_retries_match_closed_form(cat):
    # one node straight to the bridge; pick a link margin that gives PER ≈ 0.4 per attempt
    lo, hi = -10.0, 10.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if an.frame_error_rate(-98.4 + mid, 61) > 0.4:
            lo = mid
        else:
            hi = mid
    p = an.frame_error_rate(-98.4 + hi, 61)
    cfg = config.resolve(cat, "firmware_today", {
        "net.ranks": 1, "net.per_rank": 1, "timing.T_s": 60, "des.cycles": 1500,
        "radio.link_margin_db": hi, "des.shadow_sigma_db": 0.0, "radio.mac_retry": 1})
    d = des.DES(cfg, cat)
    d.run()
    st = d.nodes[0].st
    success = st["tx_attempts"] - st["tx_per"] - st["tx_coll"]
    fail_rate = st["tx_final_fail"] / (st["tx_final_fail"] + success)
    assert fail_rate == pytest.approx(p ** 2, abs=0.05)


@pytest.mark.parametrize("tech", ["T1-ladder", "T2-window"])
def test_energy_matches_calculator(cat, tech):
    res = sim(cat, "greenhouse", net__per_rank=1, net__hidden_frac=0, des__cycles=10,
              scheme__technique=tech)
    calc = {x["rank"]: x["mah_day"] for x in res["plan"]["per_rank"]}
    for x in res["per_rank"]:
        assert x["mah_day"] == pytest.approx(calc[x["rank"]], rel=0.10)


def test_results_carry_trace_and_series(cat):
    res = sim(cat, "greenhouse", des__cycles=2)
    assert res["trace"] and {"node", "t0", "t1", "state"} <= set(res["trace"][0])
    assert "bridge_queue" in res["series"]
