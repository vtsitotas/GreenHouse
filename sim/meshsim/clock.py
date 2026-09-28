"""Clock drift and guard-window statistics for one parent/child pair.

`run_policy` is a line-for-line port of docs/analysis/cart_sim.py::run_policy
(same RNG sequence, so the numbers match that script exactly — tested), with
extra outputs the calculator needs: mean guard width and the spread of the
residual timing error (used to size T2's network-wide window).
"""
import math
import random
import statistics
from collections import deque
from functools import lru_cache


def step_for(T_s, step_per_300s):
    """Per-cycle rate innovation scales with cycle length (cart_sim: 300 s → s, 900 s → 3s)."""
    return step_per_300s * T_s / 300


RHO = 0.98   # AR(1) coefficient of the temperature-driven rate term (cart_sim model)


def g_max_for(bias, T_s, factor=1.3, g_min=0.25):
    """CART v2 ceiling rule G_max = 2·|b|·T·1.3, never below G_min."""
    return max(g_min, 2 * abs(bias) * T_s * factor)


def wander_std_s(step, T_s, rho=RHO):
    """Stationary std of the offset wander T·w, w = ρ·w + N(0, step): T·step/√(1−ρ²)."""
    return T_s * step / math.sqrt(1 - rho * rho)


def g_max_rule(rule, bias, step, T_s, factor=1.3, g_min=0.25, z=3.0):
    """cart_v2: bias only (spec 2026-09-23 §3.3).
    bias_wander (proposed here): also cover ±z σ of the temperature wander, which the
    bias rule ignores — at T = 900 s with the planning step the bias rule gives 3.98 s
    against a wander σ of 1.36 s, and the pair ends up in a miss/sweep loop."""
    g = g_max_for(bias, T_s, factor, g_min)
    if rule == "bias_wander":
        g = max(g, 2 * z * wander_std_s(step, T_s))
    return g


def run_policy(T, bias, step, policy, cycles=100000, seed=7, g_min=0.25,
               g_max=8.0, k=1.5, window=16, pad=0.05):
    rnd = random.Random(seed)
    w = est = carry = 0.0
    est_known = False
    g = g_max
    hits = misses = run_len = max_run = drops = 0
    recent = deque(maxlen=window)
    listen, guards, errs = [], [], []
    for _ in range(cycles):
        w = 0.98 * w + rnd.gauss(0, step)
        offset = carry + T * (bias + w) + rnd.gauss(0, 0.020)
        err = offset - est
        guards.append(g)
        if abs(err) <= g / 2:
            listen.append(err + g / 2)
            errs.append(err)
            if carry == 0:
                est = offset if not est_known else 0.7 * est + 0.3 * offset
                est_known = True
            carry = 0.0
            recent.append(abs(err))
            run_len = 0
            if policy == "aimd":
                hits += 1
                if hits >= 8:
                    g, hits = max(g_min, g * 0.9), 0
            elif len(recent) == window:
                g = min(g_max, max(g_min, 2 * k * max(recent) + pad))
        else:
            misses += 1
            listen.append(g)
            carry = err
            hits = 0
            run_len += 1
            max_run = max(max_run, run_len)
            g = min(g_max, g * 2)
            if run_len >= 3:
                drops += 1
                run_len = 0
                carry = est = 0.0
                est_known = False
                g = g_max
    per_day = 86400 / T
    return {
        "miss": misses / cycles,
        "listen": statistics.mean(listen),
        "drops_per_year": drops / cycles * per_day * 365,
        "guard_mean": statistics.mean(guards),
        "err_std": statistics.pstdev(errs) if len(errs) > 1 else 0.0,
        "max_miss_run": max_run,
    }


@lru_cache(maxsize=256)
def pair_stats(T, bias, step, policy, g_min, g_max, cycles, seed=7):
    """Cached run_policy for the calculator (deterministic, so safe to cache)."""
    return run_policy(T, bias, step, policy, cycles=cycles, seed=seed, g_min=g_min, g_max=g_max)


def accumulated_offset_std(err_std, hops):
    """Each hop re-anchors to its own parent, so absolute offset from the root
    is a sum of `hops` independent pair errors: σ·√hops."""
    return err_std * math.sqrt(max(0, hops))
