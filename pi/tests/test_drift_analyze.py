import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
import drift_analyze as da


def test_constant_fast_clock_gives_its_bias_and_zero_step():
    times = [i * 300.0 * (1 - 0.006) for i in range(50)]      # runs 0.6 % fast
    rates = da.per_node_rates(times, 300.0)
    s = da.summarize(rates)
    assert s['bias'] == pytest.approx(-0.006, abs=1e-9)
    assert s['step'] == pytest.approx(0.0, abs=1e-9)
    assert s['n'] == 49


def test_a_missed_report_is_not_counted_as_a_long_cycle():
    times = [0.0, 300.0, 900.0, 1200.0]          # one report missing
    rates = da.per_node_rates(times, 300.0)
    assert len(rates) == 2


def test_pair_relative_bias_is_the_difference():
    a = da.per_node_rates([i * 300.0 * 1.001 for i in range(20)], 300.0)
    b = da.per_node_rates([i * 300.0 * 1.003 for i in range(20)], 300.0)
    rel = da.pair_relative(a, b, 300.0)
    assert rel['bias'] == pytest.approx(0.002, abs=1e-6)


def test_a_single_clean_cycle_still_reports_its_own_rate_as_bias():
    times = [0.0, 300.0 * 1.01]          # exactly one clean cycle, 1 % fast
    rates = da.per_node_rates(times, 300.0)
    s = da.summarize(rates)
    assert s['bias'] == pytest.approx(0.01, abs=1e-9)
    assert s['step'] == pytest.approx(0.0, abs=1e-9)
    assert s['n'] == 1
