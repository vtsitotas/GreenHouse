"""CART (synced-wake sleepy relay) design-validation simulation.

Analysis only, not firmware -- stdlib Python, deterministic seeds. Produces
the numbers quoted in docs/superpowers/specs/2026-09-23-cart-v2-revision.md.
Re-run with the Phase 0 bench measurements substituted into MEASURED below
to turn the provisional constants into final ones (plan Task 3.2).

    python docs/analysis/cart_sim.py            # writes cart_sim_results.txt

Sections
  A1  analytic guard window vs residual drift (no learning)
  A2  guard-window POLICY Monte Carlo: CART spec AIMD vs dispersion-based
      "margin" policy, with correct miss compounding and sweep re-anchoring
  B1  CSMA/CA first-slot collision, children released by the same beacon
  B2  hidden-node collision in a parent's receptive window, with jitter J
      and application-level retries
  C   energy per node per day
"""
import math
import random
import statistics
from collections import deque
from pathlib import Path

I_ACTIVE_MA = 86.5          # report §18.2.2 active current (RX/CPU)
I_SLEEP_MA = 0.0625         # report §18.2.2 sleep current (62.5 uA)
PKT_AIR_S = 0.0012          # 61 B data @ 1 Mbps (~0.9 ms) + L2 ACK (~0.3 ms)

# Research-derived provisional inputs (spec §2). Replace with Phase 0 bench
# results: bias = worst |mean relative rate error| between a parent/child
# pair, step = per-cycle innovation of the temperature-driven rate term.
MEASURED = {"bias": 0.006, "step_300": 0.0001, "step_900": 0.0003}


# ---------------------------------------------------------------- A1
def z_for(p_two_sided):
    lo, hi = 0.0, 10.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if math.erf(mid / math.sqrt(2)) < p_two_sided:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def analytic_guard(T, sigma_rel, p=0.999, latency_jitter=0.020):
    sigma_t = math.sqrt((T * sigma_rel) ** 2 + latency_jitter ** 2)
    return 2 * z_for(p) * sigma_t


# ---------------------------------------------------------------- A2
def run_policy(T, bias, step, policy, cycles=100000, seed=7, g_min=0.25,
               g_max=8.0, k=1.5, window=16, pad=0.05):
    """One child/parent pair. Relative rate error = bias + OU(temperature).
    Child re-anchors on every catch (CART core). First catch after a
    (re)anchor measures the offset outright; later catches EWMA it. A miss
    leaves the next wake predicted from a stale anchor (error carries).
    3 consecutive misses = parent dropped = full-cycle orphan sweep, which
    re-anchors timing but leaves the offset unknown again."""
    rnd = random.Random(seed)
    w = est = carry = 0.0
    est_known = False
    g = g_max
    hits = misses = run_len = max_run = drops = 0
    recent = deque(maxlen=window)
    listen = []
    for _ in range(cycles):
        w = 0.98 * w + rnd.gauss(0, step)
        offset = carry + T * (bias + w) + rnd.gauss(0, 0.020)
        err = offset - est
        if abs(err) <= g / 2:
            listen.append(err + g / 2)
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
        "listen_mah_day": per_day * statistics.mean(listen) * I_ACTIVE_MA / 3600,
        "drop_mah_day": drops / cycles * per_day * (T * I_ACTIVE_MA / 3600),
    }


# ---------------------------------------------------------------- B
def csma_first_slot_collision(k, cw=16):
    p_unique = sum(k * (1 / cw) * ((cw - 1 - m) / cw) ** (k - 1) for m in range(cw))
    return 1 - p_unique


def mc_hidden_collision(k, J, hidden_frac, attempts, trials=40000, seed=3):
    rnd = random.Random(seed)
    fails = 0
    for _ in range(trials):
        ok = False
        for _a in range(attempts):
            me = rnd.uniform(0, J)
            hit = any(abs(rnd.uniform(0, J) - me) < PKT_AIR_S and rnd.random() < hidden_frac
                      for _o in range(k - 1))
            if not hit:
                ok = True
                break
        fails += not ok
    return fails / trials


# ---------------------------------------------------------------- C
def mah_per_day(T, awake_s):
    return (86400 / T) * (awake_s * I_ACTIVE_MA + (T - awake_s) * I_SLEEP_MA) / 3600


def main():
    out = []
    P = out.append
    P("== A1. Analytic guard window (99.9% catch, centred, NO offset learning) ==")
    P("sigma_rel    T=300s    T=900s")
    for s in (0.0001, 0.0005, 0.001, 0.005, 0.01):
        P(f"{s*100:6.2f}%  {analytic_guard(300, s):7.2f}s  {analytic_guard(900, s):7.2f}s")

    bias = MEASURED["bias"]
    P("")
    P(f"== A2. Guard policy Monte Carlo, relative bias {bias*100:.1f}% ==")
    for T in (300, 900):
        for label, g_max in (("G_max=8s fixed", 8.0), ("G_max=2|b|T*1.3", 2 * bias * T * 1.3)):
            P(f"-- T={T}s, {label} = {g_max:.1f}s")
            P("step%     policy  miss%    listen   drops/yr  listen mAh/d  drop mAh/d")
            for step in (0.00002, 0.0001, 0.0003, 0.001):
                for pol in ("aimd", "margin"):
                    r = run_policy(T, bias, step, pol, g_max=g_max)
                    P(f"{step*100:7.4f}  {pol:6}  {r['miss']*100:6.2f}  {r['listen']:6.3f}s  "
                      f"{r['drops_per_year']:8.2f}  {r['listen_mah_day']:10.3f}  {r['drop_mah_day']:9.3f}")

    P("")
    P("== B1. CSMA/CA first-slot collision (k non-hidden children, one trigger) ==")
    for k in (2, 3, 4, 6, 8):
        P(f"k={k}: {csma_first_slot_collision(k)*100:5.2f}%")

    P("")
    P("== B2. Hidden-node: P(child fails ALL attempts in one window) ==")
    P("k  hidden  J(ms)  attempts=1   attempts=3")
    for k in (2, 4, 6):
        for h in (0.3, 1.0):
            for J in (0.02, 0.1, 0.3):
                P(f"{k}  {h:4.1f}   {J*1000:5.0f}   {mc_hidden_collision(k, J, h, 1)*100:8.3f}%   "
                  f"{mc_hidden_collision(k, J, h, 3)*100:8.4f}%")

    P("")
    P("== C. Energy per node, mAh/day ==")
    for T in (300, 900):
        P(f"-- T={T}s")
        P(f"Phase-1 leaf (awake 2.5 s):            {mah_per_day(T, 2.5):6.2f}")
        for listen in (0.2, 0.5, 1.5):
            P(f"CART leaf (listen {listen:3.1f}s + ack 0.3s):   {mah_per_day(T, 2.5 + listen + 0.3):6.2f}")
        for S, n in ((1.5, 1), (1.5, 4), (3.0, 4), (7.0, 4)):
            awake = 2.5 + 0.5 + S + n * 0.15 + 0.3
            P(f"CART relay (S={S:3.1f}s, {n} child, awake {awake:4.1f}s): {mah_per_day(T, awake):6.2f}")
        P(f"Orphan full-cycle sweep, one-off:      {T * I_ACTIVE_MA / 3600:6.2f} mAh")
    P("Always-on relay (~100 mA):             2400.00")

    Path(__file__).with_name("cart_sim_results.txt").write_text("\n".join(out), encoding="utf-8")


if __name__ == "__main__":
    main()
