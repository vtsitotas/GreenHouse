"""Calculator: reproduces the repo's published numbers, conserves time and
energy, and applies the firmware rules exactly."""
import csv
import importlib.util

import pytest

from meshsim import calculator, clock, config, firmware_params, improvements, params, report, runlog


@pytest.fixture(scope="module")
def cat():
    return params.build()


def run(cat, preset=None, **over):
    cfg = config.resolve(cat, preset, {k.replace("__", "."): v for k, v in over.items()})
    return cfg, calculator.compute(cfg, cat)


def test_clock_port_matches_cart_sim_exactly():
    path = firmware_params.REPO / "docs/analysis/cart_sim.py"
    spec = importlib.util.spec_from_file_location("cart_sim", path)
    cart_sim = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cart_sim)
    for T, step, pol, gmax in ((300, 0.0001, "margin", 4.68), (900, 0.0003, "aimd", 8.0)):
        ours = clock.run_policy(T, 0.006, step, pol, cycles=4000, g_max=gmax)
        ref = cart_sim.run_policy(T, 0.006, step, pol, cycles=4000, g_max=gmax)
        for key in ("miss", "listen", "drops_per_year"):
            assert ours[key] == ref[key]


@pytest.mark.parametrize("T,expected", [(900, 7.2625), (300, 18.7875)])   # exact; repo rounds to 7.26 / 18.79
def test_lumped_phase1_reproduces_repo(cat, T, expected):
    _, res = run(cat, "firmware_today", timing__T_s=T, energy__model="lumped", net__per_rank=1)
    assert res["summary"]["worst_mah_day"] == pytest.approx(expected, abs=1e-3)


@pytest.mark.parametrize("tech", ["phase1", "T1-ladder", "T2-window"])
def test_time_and_energy_conservation(cat, tech):
    cfg, res = run(cat, "greenhouse", scheme__technique=tech)
    Tc = cfg["timing.T_s"] * cfg["timing.report_every"]
    sleep_i = res["currents_ma"]["sleep"]
    for x in res["per_rank"]:
        sleep_s = x["mas_by_state"]["sleep"] / sleep_i
        assert x["awake_s"] + sleep_s == pytest.approx(Tc, rel=1e-4)
        mah = sum(x["mas_by_state"].values()) * 86400 / Tc / 3600 + x["sweep_mah_day"]
        assert mah == pytest.approx(x["mah_day"], abs=0.01)


def test_ttl_ceiling_and_fix(cat):
    _, res = run(cat, "stress_50x10", scheme__max_ttl=16)      # the old firmware limit
    assert res["ttl_depth_ceiling"] == 17
    assert res["summary"]["delivered_nodes"] == 170
    _, res64 = run(cat, "stress_50x10", scheme__max_ttl=64)
    assert res64["summary"]["delivered_nodes"] == 500


def test_t2_flood_counts_match_closed_form(cat):
    _, res = run(cat, "stress_50x10", scheme__technique="T2-window", scheme__t2_ack="flood",
                 scheme__max_ttl=16)
    assert res["scheme"]["ack_transmissions"] == 18100


def test_phase1_cannot_multihop(cat):
    _, res = run(cat, "firmware_today", net__ranks=3, net__per_rank=2)
    assert [x["delivered"] for x in res["per_rank"]] == [True, False, False]
    assert "multihop_possible" in res["summary"]["checks_failed"]


def test_sensor_swap_cuts_leaf_energy(cat):
    _, dht = run(cat, "greenhouse")
    _, sht = run(cat, "greenhouse", hw__climate_sensor="sht40")
    assert sht["summary"]["leaf_mah_day"] < dht["summary"]["leaf_mah_day"]


def test_g_max_rules():
    assert clock.g_max_rule("bias_wander", 0.0017, 0.0003, 900) >= clock.g_max_rule("cart_v2", 0.0017, 0.0003, 900)
    assert clock.wander_std_s(0.0003, 900) == pytest.approx(900 * 0.0003 / (1 - 0.98 ** 2) ** 0.5)


def test_config_validation(cat):
    with pytest.raises(KeyError):
        config.resolve(cat, None, {"no.such_key": 1})
    with pytest.raises(ValueError):
        config.resolve(cat, None, {"hw.climate_sensor": "dht11"})
    assert config.resolve(cat, "field")["timing.T_s"] == 1800
    assert config.parse_value("0.5") == 0.5 and config.parse_value("sht40") == "sht40"


def test_improvements_bundle_helps(cat):
    cfg = config.resolve(cat, "stress_50x10")
    base, rows = improvements.evaluate(cat, cfg)
    bundle = next(r for r in rows if r["id"] == "free_bundle")
    assert bundle["worst_mah_day"] < base["summary"]["worst_mah_day"]
    assert bundle["delivered_nodes"] == 500


def test_runlog_records_everything(cat, tmp_path):
    cfg, res = run(cat, "greenhouse", hw__climate_sensor="sht40")
    md = report.calc_report(cfg, res)
    d = runlog.log_run("calc", "unit test", cfg, res, md, config.defaults(cat), runs_dir=tmp_path)
    for name in ("config.json", "results.json", "report.md", "meta.json"):
        assert (d / name).exists()
    rows = list(csv.DictReader((tmp_path / "index.csv").open(encoding="utf-8")))
    assert rows[0]["climate_sensor"] == "sht40" and "hw.climate_sensor=sht40" in rows[0]["changed_keys"]
