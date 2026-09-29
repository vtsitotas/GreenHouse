import math

import pytest

from meshsim import config, geometry, params


@pytest.fixture(scope="module")
def cat():
    return params.build()


def _cfg(cat, **over):
    base = {"scheme.technique": "T1-ladder", "scheme.t1_hop_ack": "l2", "scheme.t1_slot_s": 0.8,
            "radio.jitter_s": 0.05, "hw.climate_sensor": "sht40", "sync.g_max_rule": "bias_wander",
            "sync.margin_k": 2.5, "geo.length_m": 100, "geo.width_m": 40, "geo.plants_per_sensor": 20}
    base.update(over)
    return config.resolve(cat, None, base)


def test_two_ray_is_free_space_before_the_breakpoint_and_40log_after():
    f = 2.412e9
    lam = geometry.C_LIGHT / f
    d_bp = 4 * 0.5 * 0.5 / lam                                   # ≈ 8 m
    assert geometry.two_ray_db(5, f, 0.5, 0.5) == pytest.approx(geometry.fspl_db(5, f))
    assert geometry.two_ray_db(2 * d_bp, f, 0.5, 0.5) - geometry.two_ray_db(d_bp, f, 0.5, 0.5) \
        == pytest.approx(40 * math.log10(2), abs=1e-9)


def test_weissberger_matches_the_published_formula():
    f = 2.412
    assert geometry.weissberger_db(10, f) == pytest.approx(0.45 * f ** 0.284 * 10)   # 5.8 dB
    assert geometry.weissberger_db(20, f) == pytest.approx(1.33 * f ** 0.284 * 20 ** 0.588)
    assert geometry.weissberger_db(0, f) == 0.0


def test_fading_costs_margin_and_per_falls_with_rssi(cat):
    ch = geometry.Channel(_cfg(cat), cat)
    sens = ch.sens
    assert ch.per_avg(sens + 20) < ch.per_avg(sens + 10) < ch.per_avg(sens)
    rayleigh = geometry.Channel(_cfg(cat, **{"geo.rician_k_db": None}), cat)
    assert rayleigh.per_avg(sens + 10) > ch.per_avg(sens + 10)   # no LOS → deeper fades


def test_foliage_and_wet_leaves_shorten_the_hop(cat):
    dry = geometry.Channel(_cfg(cat), cat).range_m(0.1, 0.5, 0.5)
    wet = geometry.Channel(_cfg(cat, **{"geo.wet_factor": 1.3}), cat).range_m(0.1, 0.5, 0.5)
    open_ = geometry.Channel(_cfg(cat, **{"geo.foliage_frac": 0.0}), cat).range_m(0.1, 0.5, 0.5)
    assert wet < dry < open_


def test_pair_shadowing_is_symmetric_and_standard_normal():
    assert geometry._gauss(3, 7, 1) == geometry._gauss(7, 3, 1)
    xs = [geometry._gauss(i, i + 1, 5) for i in range(4000)]
    mean = sum(xs) / len(xs)
    sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / len(xs))
    assert abs(mean) < 0.06 and abs(sd - 1) < 0.06


def test_tree_covers_every_sensor_and_parents_are_one_rank_closer(cat):
    cfg = _cfg(cat)
    ch = geometry.Channel(cfg, cat)
    pts, bridges = geometry.layout(cfg)
    t = geometry.build_tree(cfg, cat, ch, pts, bridges)
    assert all(r is not None for r in t["rank"])
    for n, p in enumerate(t["parent"]):
        assert (t["rank"][n] == 1) == (p < 0)
        if p >= 0:
            assert t["rank"][p] == t["rank"][n] - 1
    assert sum(t["subtree"][n] for n in range(len(pts)) if t["rank"][n] == 1) == len(pts)


def test_balanced_parents_never_load_worse_than_rssi(cat):
    over = {"geo.length_m": 400, "geo.width_m": 60, "geo.plants_per_sensor": 25}
    rssi = geometry.evaluate(_cfg(cat, **over), cat)
    bal = geometry.evaluate(_cfg(cat, **over, **{"geo.parent_policy": "balanced"}), cat)
    assert bal["max_relay_in_frames"] <= rssi["max_relay_in_frames"]


def test_more_channels_cut_the_channel_load(cat):
    over = {"geo.length_m": 300, "geo.width_m": 60, "geo.plants_per_sensor": 10, "geo.bridges": 3,
            "geo.bridge_layout": "center"}
    one = geometry.evaluate(_cfg(cat, **over), cat)
    three = geometry.evaluate(_cfg(cat, **over, **{"geo.channels": 3}), cat)
    assert three["channel_util_max"] < one["channel_util_max"]
