"""Figures for the thesis / presentation, straight from the simulator.
Pure stdlib: writes SVG (+ a CSV table per figure) to docs/simulator/figures/.
Run from sim/:  python make_figures.py
Palette: the validated categorical order (dataviz reference palette, light mode);
three slots are below 3:1 contrast on the surface, so every figure carries
direct value labels and a CSV table (the relief rule)."""
import csv
import html
import json
import sys
from pathlib import Path

from meshsim import calculator, config, params

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
OUT = Path(__file__).resolve().parent.parent / "docs" / "simulator" / "figures"
OUT.mkdir(parents=True, exist_ok=True)
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SURFACE, INK, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0"
FONT = "font-family='Segoe UI, Helvetica, Arial, sans-serif'"
W, H = 760, 440
ML, MR, MT, MB = 70, 170, 84, 64                 # plot margins (right: direct labels)

cat = params.build()
BEST = {"scheme.technique": "T1-ladder", "scheme.t1_hop_ack": "l2", "net.ranks": 100, "net.per_rank": 50,
        "scheme.max_ttl": 128, "scheme.relay_buffer": 121, "sync.g_max_rule": "bias_wander",
        "bridge.baud": 921600, "bridge.framing": "binary", "pi.process_s": 0.005, "hw.sun": "athens_winter",
        "hw.solar": "6v_2w", "hw.climate_sensor": "sht40", "hw.divider": "switched",
        "scheme.t1_slot_s": 0.8, "radio.jitter_s": 0.05, "sync.margin_k": 2.5, "timing.T_s": 900}
DHT = {"hw.climate_sensor": "dht22", "hw.divider": "220k_x2", "scheme.t1_slot_s": 2.3, "radio.jitter_s": 0.1}


def calc(**over):
    o = dict(BEST)
    for k, v in over.items():
        o[k.replace("__", ".")] = v
    return calculator.compute(config.resolve(cat, None, o), cat)


def fmt(v, nd=1):
    s = f"{v:.{nd}f}"
    return s.replace(".", ",")


# ── tiny SVG kit ──────────────────────────────────────────────────────────────
class Fig:
    def __init__(self, title, subtitle, xlabel, ylabel, w=W, h=H):
        self.w, self.h = w, h
        self.px0, self.px1, self.py0, self.py1 = ML, w - MR, h - MB, MT
        self.parts = [f"<rect width='{w}' height='{h}' fill='{SURFACE}'/>",
                      f"<text x='{ML}' y='26' {FONT} font-size='17' font-weight='600' fill='{INK}'>{html.escape(title)}</text>",
                      f"<text x='{ML}' y='46' {FONT} font-size='12.5' fill='{INK2}'>{html.escape(subtitle)}</text>"]
        self.xlabel, self.ylabel = xlabel, ylabel
        self.end_labels = []                     # (y, x, text): de-collided at save

    def scales(self, x0, x1, y0, y1):
        self.x0, self.x1, self.y0, self.y1 = x0, x1, y0, y1

    def X(self, v):
        return self.px0 + (v - self.x0) / (self.x1 - self.x0) * (self.px1 - self.px0)

    def Y(self, v):
        return self.py0 - (v - self.y0) / (self.y1 - self.y0) * (self.py0 - self.py1)

    def axes(self, yticks, xticks=None, xtick_labels=None, ytick_fmt=lambda v: fmt(v, 0)):
        p = self.parts
        for t in yticks:
            y = self.Y(t)
            p.append(f"<line x1='{self.px0}' x2='{self.px1}' y1='{y:.1f}' y2='{y:.1f}' stroke='{GRID}' stroke-width='1'/>")
            p.append(f"<text x='{self.px0 - 8}' y='{y + 4:.1f}' text-anchor='end' {FONT} font-size='11.5' fill='{MUTED}'>{ytick_fmt(t)}</text>")
        p.append(f"<line x1='{self.px0}' x2='{self.px1}' y1='{self.py0}' y2='{self.py0}' stroke='{MUTED}' stroke-width='1'/>")
        if xticks is not None:
            for i, t in enumerate(xticks):
                lab = xtick_labels[i] if xtick_labels else fmt(t, 0)
                p.append(f"<text x='{self.X(t):.1f}' y='{self.py0 + 18}' text-anchor='middle' {FONT} font-size='11.5' fill='{MUTED}'>{html.escape(str(lab))}</text>")
        p.append(f"<text x='{(self.px0 + self.px1) / 2}' y='{self.h - 18}' text-anchor='middle' {FONT} font-size='12' fill='{INK2}'>{html.escape(self.xlabel)}</text>")
        p.append(f"<text transform='translate(18,{(self.py0 + self.py1) / 2}) rotate(-90)' text-anchor='middle' {FONT} font-size='12' fill='{INK2}'>{html.escape(self.ylabel)}</text>")

    def line(self, pts, color, label, label_last=True, value_fmt=None):
        d = " ".join(f"{'M' if i == 0 else 'L'}{self.X(x):.1f},{self.Y(y):.1f}" for i, (x, y) in enumerate(pts))
        p = self.parts
        p.append(f"<path d='{d}' fill='none' stroke='{color}' stroke-width='2' stroke-linejoin='round' stroke-linecap='round'/>")
        for x, y in pts:
            p.append(f"<circle cx='{self.X(x):.1f}' cy='{self.Y(y):.1f}' r='4' fill='{color}' stroke='{SURFACE}' stroke-width='2'/>")
        if label_last:
            x, y = pts[-1]
            txt = label + (f" · {value_fmt(y)}" if value_fmt else "")
            self.end_labels.append([self.Y(y) + 4, self.X(x) + 10, txt])

    def note(self, x, y, text, anchor="start", color=INK2, size=11.5):
        self.parts.append(f"<text x='{x:.1f}' y='{y:.1f}' text-anchor='{anchor}' {FONT} font-size='{size}' fill='{color}'>{html.escape(text)}</text>")

    def legend(self, items, y=None):
        x, y = self.px0, (y or 68)
        for label, color in items:
            if x + 22 + 7.2 * len(label) > self.w - 20:      # wrap onto a second row
                x, y = self.px0, y + 17
            self.parts.append(f"<rect x='{x}' y='{y - 9}' width='10' height='10' rx='2' fill='{color}'/>")
            self.parts.append(f"<text x='{x + 15}' y='{y}' {FONT} font-size='11.5' fill='{INK2}'>{html.escape(label)}</text>")
            x += 22 + 7.2 * len(label)

    def save(self, name, rows, header):
        labs = sorted(self.end_labels)                   # nudge direct labels ≥ 15 px apart
        for i in range(1, len(labs)):
            labs[i][0] = max(labs[i][0], labs[i - 1][0] + 15)
        for y, x, txt in labs:
            self.parts.append(f"<text x='{x:.1f}' y='{y:.1f}' {FONT} font-size='12' fill='{INK}'>{html.escape(txt)}</text>")
        svg = (f"<svg xmlns='http://www.w3.org/2000/svg' width='{self.w}' height='{self.h}' "
               f"viewBox='0 0 {self.w} {self.h}' role='img'>" + "".join(self.parts) + "</svg>")
        (OUT / f"{name}.svg").write_text(svg, encoding="utf-8")
        with open(OUT / f"{name}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)
        print("wrote", name)


def bar(fig, x, w, y0, y1, color):
    """Vertical bar with a 4-px rounded data end, anchored on the baseline."""
    top, base = fig.Y(y1), fig.Y(y0)
    h = max(0.0, base - top)
    r = min(4.0, h / 2, w / 2)
    fig.parts.append(f"<path d='M{x:.1f},{base:.1f} V{top + r:.1f} Q{x:.1f},{top:.1f} {x + r:.1f},{top:.1f} "
                     f"H{x + w - r:.1f} Q{x + w:.1f},{top:.1f} {x + w:.1f},{top + r:.1f} V{base:.1f} Z' fill='{color}'/>")


# ── F1 energy vs cycle T ──────────────────────────────────────────────────────
def fig_energy_vs_T():
    Ts = [300, 600, 900, 1200, 1800, 2700]
    rows = []
    for T in Ts:
        r = calc(timing__T_s=T, req__latency_s=T)
        rows.append((T // 60, r["summary"]["worst_mah_day"], r["summary"]["leaf_mah_day"],
                     round(r["sync"]["guard_mean"], 2), r["summary"]["pdr_ontime_mean"]))
    f = Fig("Ενέργεια ανά ημέρα ως προς τον κύκλο αφύπνισης T",
            "100 ranks × 50, SHT40, slot 0,8 s, k 2,5 · ελάχιστο στα 10–15′· πάνω από 30′ το guard χτυπά το όριο 20 s",
            "Κύκλος T (λεπτά)", "mAh / ημέρα")
    f.scales(0, 48, 0, 35)
    f.axes([0, 5, 10, 15, 20, 25, 30, 35], [5, 10, 15, 20, 30, 45], ["5", "10", "15", "20", "30", "45"])
    f.line([(t, w) for t, w, *_ in rows], SERIES[0], "χειρότερος κόμβος", value_fmt=lambda v: fmt(v))
    f.line([(t, l) for t, _, l, *_ in rows], SERIES[1], "φύλλο", value_fmt=lambda v: fmt(v))
    f.note(f.X(15), f.Y(7.25) - 12, "15′: 7,25", "middle", INK)
    f.legend([("χειρότερος κόμβος (rank 2)", SERIES[0]), ("φύλλο (rank 100)", SERIES[1])])
    f.save("f1_energy_vs_T", rows, ["T_min", "worst_mah_day", "leaf_mah_day", "guard_mean_s", "pdr_ontime_mean"])


# ── F2 on-time PDR vs depth for the guard margin k ────────────────────────────
def fig_pdr_vs_depth():
    ks = [1.5, 2.0, 2.5, 3.0]
    per_k = {}
    for k in ks:
        r = calc(sync__margin_k=k)
        per_k[k] = [(x["rank"], x["p_ontime"]) for x in r["per_rank"]]
    f = Fig("Παράδοση στην ώρα της ως προς το βάθος, για το περιθώριο k",
            "Οι αστοχίες συγχρονισμού αθροίζονται ανά hop: με k 1,5 στο rank 100 φτάνει στην ώρα του το 27 %",
            "Rank (hops από τη γέφυρα)", "Παράδοση στην ώρα (%)")
    f.scales(1, 100, 0, 100)
    f.axes([0, 20, 40, 60, 80, 100], [1, 20, 40, 60, 80, 100])
    for i, k in enumerate(ks):
        pts = [(rk, 100 * p) for rk, p in per_k[k] if rk in (1, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)]
        f.line(pts, SERIES[i], f"k {fmt(k)}", value_fmt=lambda v: fmt(v) + " %")
    f.legend([(f"k = {fmt(k)}", SERIES[i]) for i, k in enumerate(ks)])
    rows = [(rk, *[round(100 * dict(per_k[k])[rk], 3) for k in ks]) for rk in range(1, 101)]
    f.save("f2_pdr_vs_depth_k", rows, ["rank"] + [f"pdr_k{k}" for k in ks])


# ── F3 15 vs 30 minutes ───────────────────────────────────────────────────────
def fig_15_vs_30():
    cases = [("SHT40 · χειρότερος", {}, "worst_mah_day"), ("SHT40 · φύλλο", {}, "leaf_mah_day"),
             ("DHT22 · χειρότερος", DHT, "worst_mah_day"), ("DHT22 · φύλλο", DHT, "leaf_mah_day")]
    rows = []
    for name, over, key in cases:
        o = {k.replace(".", "__"): v for k, v in over.items()}
        v15 = calc(**o)["summary"][key]
        o30 = dict(o, timing__T_s=1800, req__latency_s=1800, sync__margin_k=3.0)
        if not over:
            o30.update(scheme__t1_slot_s=0.6, scheme__rank1_flush_s=3.0)
        v30 = calc(**o30)["summary"][key]
        rows.append((name, v15, v30))
    f = Fig("15′ έναντι 30′: ενέργεια ανά ημέρα", "100 × 50 · στα 30′ χρειάζεται k 3 (guard στο όριο των 20 s)",
            "", "mAh / ημέρα")
    f.scales(0, len(rows), 0, 14)
    f.axes([0, 2, 4, 6, 8, 10, 12, 14])
    gw = (f.px1 - f.px0) / len(rows)
    bw = min(34, gw / 3)
    for i, (name, a, b) in enumerate(rows):
        cx = f.px0 + gw * (i + 0.5)
        for j, (v, col) in enumerate(((a, SERIES[0]), (b, SERIES[1]))):
            x = cx - bw - 1 + j * (bw + 2)                       # 2-px surface gap between the pair
            bar(f, x, bw, 0, v, col)
            f.note(x + bw / 2, f.Y(v) - 6, fmt(v), "middle", INK, 11)
        f.note(cx, f.py0 + 18, name, "middle", MUTED, 11.5)
    f.legend([("15′", SERIES[0]), ("30′", SERIES[1])])
    f.save("f3_15_vs_30", rows, ["case", "T15_mah_day", "T30_mah_day"])


# ── F4 T1 ladder vs T2 end-to-end ─────────────────────────────────────────────
def fig_t1_vs_t2():
    Rs = [1, 2, 5, 10, 20]
    rows = []
    for R in Rs:
        a = calc(net__ranks=R, net__per_rank=10)["summary"]
        b = calc(net__ranks=R, net__per_rank=10, scheme__technique="T2-window", scheme__t2_ack="unicast")["summary"]
        rows.append((R, a["worst_mah_day"], b["worst_mah_day"], "|".join(b["checks_failed"])))
    f = Fig("Τεχνική 1 (hop-by-hop, σκάλα) έναντι Τεχνικής 2 (end-to-end)",
            "10 κόμβοι/rank, 15′, SHT40 · η T2 κρατά όλους ξύπνιους ως το ACK· από βάθος 10: > 10 s ξύπνιοι",
            "Βάθος (ranks)", "mAh / ημέρα, χειρότερος κόμβος")
    f.scales(0, 21, 0, 40)
    f.axes([0, 10, 20, 30, 40], Rs)
    f.line([(R, a) for R, a, *_ in rows], SERIES[0], "T1 σκάλα", value_fmt=lambda v: fmt(v))
    f.line([(R, b) for R, _, b, _ in rows], SERIES[1], "T2 end-to-end", value_fmt=lambda v: fmt(v))
    f.legend([("T1: hop-by-hop, custody L2", SERIES[0]), ("T2: end-to-end, reverse unicast ACK", SERIES[1])])
    f.save("f4_t1_vs_t2", rows, ["ranks", "T1_worst_mah_day", "T2_worst_mah_day", "T2_checks_failed"])


# ── F5 energy by state ────────────────────────────────────────────────────────
def fig_energy_by_state():
    r = calc()
    groups = [("boot", ["boot"]), ("αισθητήρες + CPU", ["sensor", "cpu"]), ("παράθυρο λήψης", ["rx_window"]),
              ("συγχρονισμός", ["sync_listen"]), ("αναμονή σειράς", ["radio_other"]), ("εκπομπή", ["tx_air"]),
              ("ύπνος", ["sleep"])]
    who = [("rank 1", r["per_rank"][0]), ("rank 2 (χειρότερος)", r["per_rank"][1]), ("rank 100 (φύλλο)", r["per_rank"][-1])]
    per_day = 86400 / 900 / 3600
    rows = []
    for name, x in who:
        m = x["mas_by_state"]
        rows.append([name] + [round(sum(m.get(s, 0.0) for s in states) * per_day, 3) for _, states in groups])
    f = Fig("Πού πάει η ενέργεια: ανά κατάσταση", "100 × 50, 15′, SHT40 · mAh/ημέρα ανά κόμβο",
            "mAh / ημέρα", "", h=380)
    f.px0 = 170
    f.scales(0, 8, 0, 1)
    p = f.parts
    for t in range(0, 9):
        x = f.X(t)
        p.append(f"<line x1='{x:.1f}' x2='{x:.1f}' y1='{f.py1}' y2='{f.py0}' stroke='{GRID}'/>")
        f.note(x, f.py0 + 18, str(t), "middle", MUTED)
    bh = 34
    for i, row in enumerate(rows):
        y = f.py1 + 40 + i * (bh + 28)
        f.note(f.px0 - 10, y + bh / 2 + 4, row[0], "end", INK, 12)
        acc = 0.0
        for j, v in enumerate(row[1:]):
            if v <= 0:
                continue
            x0, x1 = f.X(acc), f.X(acc + v)
            p.append(f"<rect x='{x0 + 1:.1f}' y='{y}' width='{max(0.0, x1 - x0 - 2):.1f}' height='{bh}' rx='2' fill='{SERIES[j]}'/>")
            acc += v
        f.note(f.X(acc) + 8, y + bh / 2 + 4, fmt(acc, 2), "start", INK, 12)
    f.note((f.px0 + f.px1) / 2, f.h - 18, f.xlabel, "middle", INK2, 12)
    f.legend([(g, SERIES[j]) for j, (g, _) in enumerate(groups)], y=68)
    f.save("f5_energy_by_state", rows, ["node"] + [g for g, _ in groups])


# ── F6 one sensor per plant: channel load ─────────────────────────────────────
def fig_per_plant():
    runs = sorted(Path(__file__).parent.joinpath("runs").glob("*_world_geometry/rows.json"))
    data = {}
    for p in runs:
        for row in json.loads(p.read_text(encoding="utf-8")):
            data[row["case"]] = row
    pick = [("1 ha\n1/φυτό", "A 1ha 1/plant"), ("5 ha, 4 γέφυρες", "B 5ha 1/plant 4br"),
            ("5 ha, 4 γέφ.\n3 κανάλια", "B 5ha 1/plant 4br 3ch"), ("5 ha, 4 γέφ.\nbeacon 250 ms", "D 5ha 1/plant 4br beacon250"),
            ("5 ha, 4 γέφ.\nDHT22", "D 5ha 1/plant 4br DHT22")]
    rows = [(lab.replace("\n", " "), 100 * data[k]["channel_util_max"]) for lab, k in pick if k in data]
    if not rows:
        print("skip f6 (run run_world_geometry.py first)")
        return
    f = Fig("«Ένας αισθητήρας ανά φυτό»: φόρτος καναλιού", "2,5 φυτά/m², SHT40 εκτός αν σημειώνεται · όριο CSMA 30 %",
            "", "Χρήση καναλιού (%)")
    f.scales(0, len(rows), 0, 90)
    f.axes([0, 15, 30, 45, 60, 75, 90])
    ylim = f.Y(30)
    f.parts.append(f"<line x1='{f.px0}' x2='{f.px1}' y1='{ylim:.1f}' y2='{ylim:.1f}' stroke='{INK2}' stroke-width='1.5' stroke-dasharray='5 4'/>")
    f.note(f.px1 + 6, ylim + 4, "όριο 30 %", "start", INK2)
    gw = (f.px1 - f.px0) / len(rows)
    bw = min(56, gw * 0.55)
    for i, (lab, v) in enumerate(rows):
        cx = f.px0 + gw * (i + 0.5)
        bar(f, cx - bw / 2, bw, 0, v, SERIES[0] if v <= 30 else SERIES[1])
        f.note(cx, f.Y(v) - 6, fmt(v) + " %", "middle", INK, 11.5)
        for li, part in enumerate(dict(pick)[lab] if False else [s for s in [p for p, _ in pick][i].split("\n")]):
            f.note(cx, f.py0 + 16 + 14 * li, part, "middle", MUTED, 11)
    f.legend([("≤ 30 %: περνά", SERIES[0]), ("> 30 %: κορεσμένο κανάλι", SERIES[1])])
    f.save("f6_per_plant_channel", rows, ["scenario", "channel_util_max_pct"])


if __name__ == "__main__":
    fig_energy_vs_T()
    fig_pdr_vs_depth()
    fig_15_vs_30()
    fig_t1_vs_t2()
    fig_energy_by_state()
    fig_per_plant()
    print("→", OUT)
