"""Every run is recorded: full config, full results, provenance, and a line in
runs/index.csv so runs can be compared later.

    sim/runs/<YYYYmmdd-HHMMSS>_<label>/
        config.json    every variable of the run (defaults ← preset ← --set)
        results.json   every computed number
        report.md      human-readable report
        meta.json      git commit/dirty, firmware snapshot hash, command, time
    sim/runs/index.csv one row per run
"""
import csv
import hashlib
import json
import math
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import firmware_params

RUNS = Path(__file__).resolve().parents[1] / "runs"

INDEX_FIELDS = [
    "time", "dir", "kind", "label", "preset", "technique", "ranks", "per_rank", "T_s",
    "climate_sensor", "warmup_mode", "board", "battery", "max_ttl", "bridge_baud",
    "worst_mah_day", "worst_autonomy_dark_days", "leaf_mah_day", "latency_max_s",
    "pdr_ontime_mean", "delivered_nodes", "checks_failed", "changed_keys",
]


def sanitize(obj):
    """JSON-safe: inf/nan → strings, tuples → lists."""
    if isinstance(obj, float) and (math.isinf(obj) or math.isnan(obj)):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    return obj


def _git():
    try:
        root = firmware_params.REPO
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root,
                              capture_output=True, text=True, timeout=5).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "sim", "firmware", "pi"],
                                    cwd=root, capture_output=True, text=True, timeout=5).stdout.strip())
        return head or None, dirty
    except (OSError, subprocess.SubprocessError):
        return None, None


def _snapshot_hash():
    try:
        return hashlib.sha256(firmware_params.SNAPSHOT.read_bytes()).hexdigest()[:12]
    except OSError:
        return None


def changed_keys(cfg, defaults):
    return {k: v for k, v in cfg.items() if not k.startswith("_") and defaults.get(k) != v}


def log_run(kind, label, cfg, results, report_md, defaults, runs_dir=None, extra=None):
    runs = Path(runs_dir) if runs_dir else RUNS
    runs.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", label or kind).strip("-")[:60] or kind
    d = runs / f"{now:%Y%m%d-%H%M%S}_{slug}"
    n = 1
    while d.exists():
        n += 1
        d = runs / f"{now:%Y%m%d-%H%M%S}_{slug}-{n}"
    d.mkdir()
    head, dirty = _git()
    changed = changed_keys(cfg, defaults)
    meta = {"time": now.isoformat(timespec="seconds"), "kind": kind, "label": label,
            "preset": cfg.get("_preset"), "git_commit": head, "git_dirty": dirty,
            "firmware_snapshot_sha256": _snapshot_hash(), "python": sys.version.split()[0],
            "argv": sys.argv, "changed_keys": changed}
    dump = lambda name, obj: (d / name).write_text(
        json.dumps(sanitize(obj), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    dump("config.json", cfg)
    dump("results.json", results)
    dump("meta.json", meta)
    if extra:
        for name, obj in extra.items():
            dump(name, obj)
    (d / "report.md").write_text(report_md, encoding="utf-8")

    s = results.get("summary", {})
    row = {
        "time": meta["time"], "dir": d.name, "kind": kind, "label": label, "preset": cfg.get("_preset"),
        "technique": cfg["scheme.technique"], "ranks": cfg["net.ranks"], "per_rank": cfg["net.per_rank"],
        "T_s": cfg["timing.T_s"], "climate_sensor": cfg["hw.climate_sensor"],
        "warmup_mode": cfg["hw.warmup_mode"], "board": cfg["hw.board"], "battery": cfg["hw.battery"],
        "max_ttl": cfg["scheme.max_ttl"], "bridge_baud": cfg["bridge.baud"],
        "worst_mah_day": s.get("worst_mah_day"), "worst_autonomy_dark_days": s.get("worst_autonomy_dark_days"),
        "leaf_mah_day": s.get("leaf_mah_day"), "latency_max_s": s.get("latency_max_s"),
        "pdr_ontime_mean": s.get("pdr_ontime_mean"), "delivered_nodes": s.get("delivered_nodes"),
        "checks_failed": ";".join(s.get("checks_failed", [])),
        "changed_keys": ";".join(f"{k}={v}" for k, v in changed.items()),
    }
    index = runs / "index.csv"
    new = not index.exists()
    with index.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=INDEX_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)
    return d
