#!/usr/bin/env python3
"""Bench-only: turn drift_logger CSVs into CART simulator inputs (Gate 0)."""
import argparse
import csv
import itertools
import json
import re
import statistics
import sys
from collections import defaultdict


def per_node_rates(times, period_s):
    times = sorted(times)
    out = []
    for prev, cur in zip(times, times[1:]):
        delta = cur - prev
        if round(delta / period_s) == 1:
            out.append((cur, delta / period_s - 1.0))
    return out


def _stats(values):
    if len(values) == 0:
        return {'bias': 0.0, 'step': 0.0, 'n': 0}
    if len(values) == 1:
        return {'bias': values[0], 'step': 0.0, 'n': 1}
    diffs = [b - a for a, b in zip(values, values[1:])]
    return {'bias': statistics.mean(values),
            'step': statistics.pstdev(diffs) if len(diffs) > 1 else 0.0,
            'n': len(values)}


def summarize(rates):
    return _stats([r for _t, r in rates])


def pair_relative(rates_a, rates_b, period_s):
    rel = []
    for t_b, r_b in rates_b:
        best = min(rates_a, key=lambda x: abs(x[0] - t_b), default=None)
        if best is not None and abs(best[0] - t_b) <= period_s / 2:
            rel.append(r_b - best[1])
    return _stats(rel)


_MAC = re.compile(r'^[0-9A-F]{12}$')


def load_times(lines):
    """Arrival times per node from logger rows ("MAC,epoch"). Rows that are
    not exactly that -- e.g. NUL bytes / a half-written row left by an abrupt
    power loss -- are skipped rather than becoming a bogus extra node."""
    times = defaultdict(list)
    for row in csv.reader(line.replace(chr(0), '') for line in lines):
        if len(row) != 2 or not _MAC.match(row[0].strip()):
            continue
        try:
            t = float(row[1])
        except ValueError:
            continue
        times[row[0].strip()].append(t)
    return times


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('csv')
    ap.add_argument('--period', type=float, required=True, help='nominal T in seconds')
    args = ap.parse_args(argv)
    with open(args.csv, errors='replace') as fh:
        times = load_times(fh)
    rates = {mac: per_node_rates(ts, args.period) for mac, ts in times.items()}
    pairs = {f'{a}->{b}': pair_relative(rates[a], rates[b], args.period)
             for a, b in itertools.permutations(sorted(rates), 2)}
    worst_bias = max((abs(p['bias']) for p in pairs.values()), default=0.0)
    worst_step = max((p['step'] for p in pairs.values()), default=0.0)
    print(json.dumps({'period_s': args.period,
                      'per_node': {m: summarize(r) for m, r in rates.items()},
                      'pairs': pairs,
                      'worst': {'bias': worst_bias, 'step': worst_step}}, indent=2))


if __name__ == '__main__':
    sys.exit(main())
