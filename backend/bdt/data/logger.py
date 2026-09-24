"""Research dataset logger.

One session = <stamp>-<label>.csv (one row per BMS snapshot) + <same>.json sidecar
holding everything needed to reproduce or train on it later: battery identity,
link settings, chemistry profile, the twin's parameters at start and at stop,
and free-text notes. Columns are stable so sessions concatenate cleanly.
"""
from __future__ import annotations

import csv
import json
import re
import time
from pathlib import Path

SESS_DIR = Path(__file__).resolve().parents[2] / "data" / "sessions"
SESS_DIR.mkdir(parents=True, exist_ok=True)


class SessionLogger:
    def __init__(self, label: str, notes: str, meta: dict):
        safe = re.sub(r"[^A-Za-z0-9_-]+", "-", label).strip("-")[:40]
        self.name = time.strftime("%Y%m%d-%H%M%S") + (f"-{safe}" if safe else "")
        self.csv_path = SESS_DIR / f"{self.name}.csv"
        self.meta_path = SESS_DIR / f"{self.name}.json"
        self.meta = {"label": label, "notes": notes, "started": time.time(), **meta}
        self.f = open(self.csv_path, "w", newline="", encoding="utf-8")
        self.w = None
        self.rows = 0
        self.t0 = None
        self._write_meta()

    def _write_meta(self):
        self.meta_path.write_text(json.dumps(self.meta, indent=1, default=float), encoding="utf-8")

    def write(self, t, I, cells, temps, soc_bms, twin_row, extra: dict | None = None):
        if self.w is None:
            cols = ["t_unix", "t_s", "current_A", "pack_V", "soc_bms", "T_max_C"]
            cols += [f"cell_{i + 1}_V" for i in range(len(cells))] + [f"temp_{i + 1}_C" for i in range(len(temps))]
            cols += ["twin_V", "twin_residual_mV", "twin_soc"] + sorted((extra or {}).keys())
            self.cols = cols
            self.w = csv.writer(self.f)
            self.w.writerow(cols)
            self.t0 = t
        row = [f"{t:.3f}", f"{t - self.t0:.3f}", f"{I:.3f}", f"{sum(cells):.4f}",
               "" if soc_bms is None else f"{soc_bms:.4f}", f"{max(temps):.2f}" if temps else ""]
        row += [f"{c:.4f}" for c in cells] + [f"{x:.2f}" for x in temps]
        row += ([f"{twin_row['V_pred']:.4f}", f"{twin_row['res_mV']:.2f}", f"{twin_row['soc_twin']:.4f}"]
                if twin_row else ["", "", ""])
        row += [extra[k] for k in sorted((extra or {}).keys())]
        if len(row) == len(self.cols):  # cell count changed mid-session => skip rather than corrupt
            self.w.writerow(row)
            self.rows += 1
            if self.rows % 20 == 0:
                self.f.flush()

    def close(self, end_meta: dict):
        self.f.close()
        self.meta.update({"stopped": time.time(), "rows": self.rows, **end_meta})
        self._write_meta()
        return self.name


def list_sessions():
    out = []
    for p in sorted(SESS_DIR.glob("*.csv"), key=lambda p: -p.stat().st_mtime):
        m = {}
        j = p.with_suffix(".json")
        if j.exists():
            try:
                m = json.loads(j.read_text(encoding="utf-8"))
            except Exception:
                pass
        out.append({"name": p.stem, "bytes": p.stat().st_size, "rows": m.get("rows"), "label": m.get("label"),
                    "notes": m.get("notes"), "started": m.get("started"), "stopped": m.get("stopped"),
                    "battery": m.get("battery", {}).get("label")})
    return out
