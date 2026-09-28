"""Human-readable Markdown report of one calculator run (Greek, like the docs)."""
import math

STATE_GR = {"boot": "εκκίνηση", "sensor": "αισθητήρες (warm-up)", "cpu": "CPU/ανάγνωση",
            "radio_init": "init radio", "sync_listen": "συγχρονισμός (guard)",
            "rx_window": "παράθυρο λήψης", "tx_air": "εκπομπή (time on air)",
            "radio_other": "radio: αναμονή/ACK/backoff", "sample_wake": "αφυπνίσεις μόνο μέτρησης",
            "sleep": "deep sleep"}


def _f(v, nd=3):
    if isinstance(v, float):
        return "∞" if math.isinf(v) else f"{v:.{nd}f}".rstrip("0").rstrip(".")
    return str(v)


def _pick_ranks(n):
    if n <= 20:
        return list(range(1, n + 1))
    return sorted({1, 2, 3, 5, 10, 15, 16, 17, 18, 20, 25, 30, 40, 50, n} & set(range(1, n + 1)))


def calc_report(cfg, res, changed=None, improvements=None):
    s = res["summary"]
    L = []
    P = L.append
    P(f"# Run — {s['technique']} · {s['ranks']}×{s['nodes'] // s['ranks']} κόμβοι · T = {s['T_s']} s\n")
    if cfg.get("_preset"):
        P(f"Preset: `{cfg['_preset']}`  ")
    if changed:
        P("Αλλαγμένες μεταβλητές: " + ", ".join(f"`{k}={v}`" for k, v in changed.items()) + "\n")
    P("## Σύνοψη\n")
    P("| Μέγεθος | Τιμή |\n|---|---|")
    rows = [
        ("Χειρότερος κόμβος (rank)", s["worst_rank"]),
        ("Κατανάλωση χειρότερου", f"{_f(s['worst_mah_day'])} mAh/ημέρα"),
        ("Αυτονομία χειρότερου χωρίς ήλιο (= ζωή δικτύου)", f"{_f(s['worst_autonomy_dark_days'], 1)} ημέρες"),
        ("Κατανάλωση φύλλου (rank R)", f"{_f(s['leaf_mah_day'])} mAh/ημέρα"),
        ("Αυτονομία φύλλου χωρίς ήλιο", f"{_f(s['leaf_autonomy_dark_days'], 1)} ημέρες"),
        ("Ηλιακή συγκομιδή", f"{_f(s['harvest_mah_day'], 1)} mAh/ημέρα · όλοι αυτάρκεις: {'ναι' if s['all_energy_neutral'] else 'ΟΧΙ'}"),
        ("Κόμβοι που παραδίδουν", f"{s['delivered_nodes']} / {s['nodes']}"),
        ("Μέσο on-time PDR ανά κύκλο", _f(s["pdr_ontime_mean"], 4)),
        ("Μέγιστη καθυστέρηση", f"{_f(s['latency_max_s'], 2)} s"),
        ("Μέγιστος ξύπνιος χρόνος", f"{_f(s['max_awake_s'], 2)} s"),
        ("Ταβάνι βάθους (TTL)", f"rank {res['ttl_depth_ceiling']}"),
    ]
    for k, v in rows:
        P(f"| {k} | {v} |")
    P("")
    P("## Έλεγχοι εφικτότητας\n")
    P("| | Έλεγχος | Λεπτομέρεια |\n|---|---|---|")
    for c in res["checks"]:
        P(f"| {'✅' if c['ok'] else '❌'} | `{c['name']}` | {c['detail']} |")
    P("")
    P("## Ανά rank\n")
    P("| rank | subtree | awake s | duty % | on-air ms | mAh/ημ. | µA μέσο | αυτονομία ημ. | ηλιακό × | latency s | on-time PDR | buffer | util |")
    P("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    by_rank = {x["rank"]: x for x in res["per_rank"]}
    for r in _pick_ranks(len(by_rank)):
        x = by_rank[r]
        P(f"| {r} | {x['subtree']} | {_f(x['awake_s'], 2)} | {_f(x['duty_pct'], 3)} | {_f(x['time_on_air_ms'], 2)} | "
          f"{_f(x['mah_day'], 2)} | {_f(x['avg_ua'], 1)} | {_f(x['autonomy_dark_days'], 1)} | "
          f"{_f(x['solar_margin_x'], 1)} | {_f(x['latency_s'], 2)} | {_f(x['p_ontime'], 4)} | "
          f"{x['buffer_peak']} | {_f(x['buffer_util'], 2)} |")
    P("")
    worst = by_rank[s["worst_rank"]]
    P(f"## Πού πάει η ενέργεια (rank {s['worst_rank']}, ανά κύκλο)\n")
    total = sum(worst["mas_by_state"].values()) or 1
    P("| Κατάσταση | mAs/κύκλο | % |\n|---|---|---|")
    for st, v in sorted(worst["mas_by_state"].items(), key=lambda kv: -kv[1]):
        P(f"| {STATE_GR.get(st, st)} | {_f(v, 2)} | {_f(100 * v / total, 1)} |")
    if worst["sweep_mah_day"]:
        P(f"| orphan sweeps (εκτός κύκλου) | {_f(worst['sweep_mah_day'], 3)} mAh/ημέρα | — |")
    P("")
    if res["sync"]:
        y = res["sync"]
        P("## Συγχρονισμός ρολογιών (ζεύγος κόμβου–parent)\n")
        P(f"bias {_f(y['bias'] * 100, 3)} % · step {_f(y['step'] * 100, 4)} %/κύκλο · G_max {_f(y['g_max'], 2)} s → "
          f"μέσο listen **{_f(y['listen'], 3)} s**, χαμένα wakes **{_f(y['miss'] * 100, 2)} %**, "
          f"orphan sweeps **{_f(y['drops_per_year'], 1)}/έτος**, σ υπολοίπου {_f(y['err_std'], 3)} s.\n")
    P("## Σχήμα\n")
    for k, v in res["scheme"].items():
        if k != "slots_s":
            P(f"- `{k}`: {_f(v, 4) if isinstance(v, float) else v}")
    P("")
    g = res["gateway"]
    P(f"## Γέφυρα / Pi\n\nγραμμή UART {g['line_b']} B → {_f(g['t_uart'] * 1000, 2)} ms · ACK {g['ack_line_b']} B · "
      f"χρόνος εξυπηρέτησης {_f(g['t_gw'] * 1000, 2)} ms/frame → **{_f(g['max_frames_s'], 1)} frames/s**.\n")
    c = res["collisions"]
    P(f"## Συγκρούσεις (εκτίμηση)\n\n{c['contenders']} παιδιά στον πιο φορτωμένο parent: CSMA first-slot "
      f"{_f(c['p_csma_first_slot'] * 100, 2)} % (λύνεται με MAC retry) · hidden ανά προσπάθεια "
      f"{_f(c['p_hidden_per_attempt'] * 100, 3)} % · αποτυχία μετά από όλες τις προσπάθειες "
      f"{c['p_fail_after_attempts']:.2e}.\n")
    lim = res["limits"]
    P("## Θεωρητικά όρια\n")
    P(f"- Ιδανικό φύλλο (μόνο chip 5 µA, SHT40, light sleep, 1 frame): **{_f(lim['ideal_leaf_mah_day'], 3)} mAh/ημέρα**, "
      f"αυτονομία **{_f(lim['ideal_leaf_autonomy_dark_days'], 0)} ημέρες** (ξύπνιο {_f(lim['ideal_leaf_awake_s'], 3)} s)")
    P(f"- Όριο αυτοεκφόρτισης μπαταρίας: {_f(lim['self_discharge_floor_days'], 0)} ημέρες")
    P(f"- Ενέργεια ενός frame (TX + L2 ACK): {_f(lim['one_frame_energy_mas'], 4)} mAs")
    P(f"- Γέφυρα: {_f(lim['gateway_max_frames_s'], 1)} frames/s → ≤ {lim['max_nodes_per_window']} κόμβοι ανά παράθυρο")
    P("")
    if improvements:
        P("## Βελτιώσεις (μία-μία, και όλες μαζί)\n")
        P("| Βελτίωση | Κατηγορία | χειρότερος mAh/ημ. | Δ% | φύλλο mAh/ημ. | ζωή δικτύου ημ. | latency s | PDR | παραδίδουν | ❌ | Σημείωση |")
        P("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in improvements:
            P(f"| {r['label']} | {r['category']} | {_f(r['worst_mah_day'], 2)} | {_f(r['worst_delta_pct'], 1)} | "
              f"{_f(r['leaf_mah_day'], 2)} | {_f(r['lifetime_dark_days'], 1)} | {_f(r['latency_max_s'], 1)} | "
              f"{_f(r['pdr_ontime_mean'], 3)} | {r['delivered_nodes']} | {r['checks_failed']} | {r['caveat']} |")
        P("")
    return "\n".join(L) + "\n"
