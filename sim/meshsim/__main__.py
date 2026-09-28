"""Command line (run from the sim/ directory):

    python -m meshsim keys                              every variable, default, meaning
    python -m meshsim presets                           ready-made scenarios
    python -m meshsim calc --preset greenhouse --set hw.climate_sensor=sht40
    python -m meshsim improve --preset stress_50x10     each improvement alone + all together
    python -m meshsim sweep --preset greenhouse --vary timing.T_s=900,1800 --vary hw.climate_sensor=dht22,sht40
    python -m meshsim des --preset greenhouse --set des.cycles=20 --set des.seeds=5
    python -m meshsim params --md ../docs/simulator/PARAMETERS.md
    python -m meshsim snapshot

Every calc / improve / sweep run is written to sim/runs/ (see runlog.py) unless --no-log.
"""
import argparse
import itertools
import sys
from pathlib import Path

from . import calculator, config, firmware_params, improvements, params, report, runlog


def _overrides(pairs):
    out = {}
    for p in pairs or []:
        if "=" not in p:
            raise SystemExit(f"--set expects key=value, got {p!r}")
        k, v = p.split("=", 1)
        out[k.strip()] = config.parse_value(v.strip())
    return out


def _print_summary(res):
    s = res["summary"]
    print(f"  technique={s['technique']}  nodes={s['nodes']}  T={s['T_s']}s")
    print(f"  worst: rank {s['worst_rank']}  {s['worst_mah_day']} mAh/day  → {s['worst_autonomy_dark_days']} days without sun")
    print(f"  leaf:  {s['leaf_mah_day']} mAh/day  → {s['leaf_autonomy_dark_days']} days without sun"
          f"   (solar {s['harvest_mah_day']} mAh/day, all neutral: {s['all_energy_neutral']})")
    print(f"  delivered {s['delivered_nodes']}/{s['nodes']}  on-time PDR {s['pdr_ontime_mean']}"
          f"  latency max {s['latency_max_s']} s  awake max {s['max_awake_s']} s")
    print(f"  checks failed: {', '.join(s['checks_failed']) or 'none'}")


def cmd_calc(args, cat, with_improvements=False):
    cfg = config.resolve(cat, args.preset, _overrides(args.set))
    res = calculator.compute(cfg, cat)
    imp_rows = None
    if with_improvements or getattr(args, "improve", False):
        _, imp_rows = improvements.evaluate(cat, cfg)
        res["improvements"] = imp_rows
    changed = runlog.changed_keys(cfg, config.defaults(cat))
    md = report.calc_report(cfg, res, changed, imp_rows)
    _print_summary(res)
    if imp_rows:
        print("\n  improvement                                   worst mAh/d   Δ%    lifetime d  delivered")
        for r in imp_rows:
            print(f"  {r['label'][:45]:45} {r['worst_mah_day']:10.2f} {r['worst_delta_pct']:6.1f} "
                  f"{r['lifetime_dark_days']:10.1f} {r['delivered_nodes']:6}")
    if not args.no_log:
        d = runlog.log_run("improve" if imp_rows else "calc", args.label or args.preset or "calc",
                           cfg, res, md, config.defaults(cat))
        print(f"\n  logged → {d}")
    return res


def cmd_des(args, cat):
    from . import des
    cfg = config.resolve(cat, args.preset, _overrides(args.set))
    res = des.simulate(cfg, cat)
    s = res["summary"]
    print(f"  DES {s['technique']}  nodes={s['nodes']}  cycles={s['cycles']}")
    print(f"  PDR settled {s['pdr_settled']}  (in horizon {s['pdr']})   duplicates {s['duplicates_at_pi']}")
    print(f"  worst rank {s['worst_rank']} {s['worst_mah_day']} mAh/day  leaf {s['leaf_mah_day']} mAh/day"
          f"  → {s['worst_autonomy_dark_days']} days without sun")
    print(f"  latency median {s['latency_median_s']} s  p95 {s['latency_p95_s']} s   "
          f"P(collision)/attempt {s['p_collision_per_attempt']}  bridge queue max {s['bridge_queue_max']}"
          f" rejects {s['bridge_rejects']}")
    if res.get("ci"):
        for k, v in res["ci"].items():
            print(f"  {k}: {v['mean']} ± {v['ci95']} (95% CI, n={v['n']})")
    if not args.no_log:
        md = report.des_report(cfg, res, runlog.changed_keys(cfg, config.defaults(cat)))
        d = runlog.log_run("des", args.label or args.preset or "des", cfg, res, md, config.defaults(cat))
        print(f"\n  logged → {d}")
    return res


def cmd_sweep(args, cat):
    base = _overrides(args.set)
    axes = []
    for v in args.vary:
        k, vals = v.split("=", 1)
        axes.append((k.strip(), [config.parse_value(x.strip()) for x in vals.split(",")]))
    rows = []
    for combo in itertools.product(*[vals for _, vals in axes]):
        over = dict(base)
        over.update({k: val for (k, _), val in zip(axes, combo)})
        cfg = config.resolve(cat, args.preset, over)
        res = calculator.compute(cfg, cat)
        label = "_".join(f"{k.split('.')[-1]}={val}" for (k, _), val in zip(axes, combo))
        if not args.no_log:
            md = report.calc_report(cfg, res, runlog.changed_keys(cfg, config.defaults(cat)))
            runlog.log_run("sweep", f"{args.label or args.preset or 'sweep'}_{label}", cfg, res, md,
                           config.defaults(cat))
        s = res["summary"]
        rows.append({"combo": label, **{k: s[k] for k in ("worst_mah_day", "worst_autonomy_dark_days",
                                                          "leaf_mah_day", "latency_max_s", "pdr_ontime_mean",
                                                          "delivered_nodes")},
                     "failed": len(s["checks_failed"])})
    print(f"  {'combo':55} worst mAh/d  life d   leaf mAh/d  latency s   PDR     deliv  ❌")
    for r in rows:
        print(f"  {r['combo'][:55]:55} {r['worst_mah_day']:10.2f} {r['worst_autonomy_dark_days']:7.1f} "
              f"{r['leaf_mah_day']:10.2f} {r['latency_max_s']:10.2f} {r['pdr_ontime_mean']:7.4f} "
              f"{r['delivered_nodes']:6} {r['failed']:3}")
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(prog="meshsim")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("params", help="print / export the parameter catalogue")
    p.add_argument("--md", type=Path)
    p.add_argument("--json", type=Path)
    sub.add_parser("snapshot", help="re-parse firmware sources into firmware_snapshot.json")
    sub.add_parser("keys", help="list every run variable")
    sub.add_parser("presets", help="list presets")
    for name in ("calc", "improve", "sweep", "des"):
        q = sub.add_parser(name)
        q.add_argument("--preset", choices=sorted(config.PRESETS))
        q.add_argument("--set", action="append", metavar="KEY=VALUE")
        q.add_argument("--label")
        q.add_argument("--no-log", action="store_true")
        if name == "calc":
            q.add_argument("--improve", action="store_true", help="also evaluate every improvement")
        if name == "sweep":
            q.add_argument("--vary", action="append", required=True, metavar="KEY=V1,V2,...")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.cmd == "snapshot":
        firmware_params.write_snapshot()
        print(f"wrote {firmware_params.SNAPSHOT}")
        return
    cat = params.build()
    if args.cmd == "params":
        if args.md:
            args.md.parent.mkdir(parents=True, exist_ok=True)
            args.md.write_text(params.to_markdown(cat) + "\n", encoding="utf-8")
            print(f"wrote {args.md.resolve()} ({len(cat.params)} parameters)")
        if args.json:
            args.json.write_text(params.to_json(cat) + "\n", encoding="utf-8")
            print(f"wrote {args.json.resolve()}")
        if not (args.md or args.json):
            for prm in cat.params.values():
                print(f"{prm.group:3} {prm.key:40} {params._fmt(prm.value):>28} {prm.unit:10} {prm.source}")
    elif args.cmd == "keys":
        for row in config.describe(cat):
            ch = f"  [{' | '.join(map(str, row['choices']))}]" if row["choices"] else ""
            print(f"{row['key']:32} = {row['default']!s:22} {row['desc']}{ch}")
    elif args.cmd == "presets":
        for name, p in config.PRESETS.items():
            sets = ", ".join(f"{k}={v}" for k, v in p.items() if not k.startswith("_"))
            print(f"{name:16} {p['_doc']}\n{'':16} {sets}")
    elif args.cmd == "calc":
        cmd_calc(args, cat)
    elif args.cmd == "improve":
        cmd_calc(args, cat, with_improvements=True)
    elif args.cmd == "sweep":
        cmd_sweep(args, cat)
    elif args.cmd == "des":
        cmd_des(args, cat)


if __name__ == "__main__":
    main()
