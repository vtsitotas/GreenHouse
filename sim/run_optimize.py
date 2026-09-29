"""Run the capacity/efficiency study and save it (python run_optimize.py, from sim/)."""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from meshsim import optimize, params, runlog

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
cat = params.build()
t0 = time.time()
rows = optimize.study(cat)
out = Path(__file__).parent / "runs" / f"{datetime.now():%Y%m%d-%H%M%S}_optimize"
out.mkdir(parents=True, exist_ok=True)
(out / "rows.json").write_text(json.dumps(runlog.sanitize(rows), ensure_ascii=False, indent=1), encoding="utf-8")
print(f"done in {time.time() - t0:.0f} s → {out}")
