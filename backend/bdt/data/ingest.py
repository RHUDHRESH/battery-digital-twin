"""Battery test-file ingestion (CSV / XLSX exports from cyclers and loggers).

Header-alias matching in the spirit of TRI's BEEP structuring step (which
normalises Arbin/Maccor/Neware/BioLogic exports to one schema). This is NOT
BEEP; for native binary formats install `beep` and convert first.

Recognised columns (case-insensitive, units in brackets ignored):
  time  : test_time(s), test time, time, time(s), totaltime, elapsed
  current: current(a), current, amps, i, i(a)       (sign auto-detected: see below)
  voltage: voltage(v), voltage, volts, v, pack_v
  temp  : aux_temperature_1(c), temperature, temp, t_cell
  cells : cell_1..cell_N / v1..vN / cell1..
Sign: cyclers usually report discharge current as NEGATIVE. If the voltage
falls when current is negative, current is inverted to our convention (+ = discharge).
"""
from __future__ import annotations

import io
import re

import numpy as np
import pandas as pd

ALIASES = {
    "t": ["test_time", "test time", "testtime", "time", "totaltime", "total time", "elapsed", "t", "time_s"],
    "I": ["current", "amps", "amp", "i", "current_a"],
    "V": ["voltage", "volts", "volt", "v", "pack_v", "pack voltage", "ecell"],
    "T": ["aux_temperature_1", "temperature", "temp", "t_cell", "cell temperature", "aux_temp"],
}


def _norm(h: str) -> str:
    return re.sub(r"\s*[\(\[].*?[\)\]]", "", str(h)).strip().lower().replace("-", "_")


def parse_table(raw: bytes, filename: str) -> dict:
    if filename.lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(io.BytesIO(raw))
    else:
        df = pd.read_csv(io.BytesIO(raw), sep=None, engine="python")
    cols = {_norm(c): c for c in df.columns}
    found = {}
    for key, names in ALIASES.items():
        for n in names:
            if n in cols:
                found[key] = cols[n]
                break
    cell_cols = sorted([c for c in df.columns if re.fullmatch(r"(cell|v)_?\d+", _norm(c))],
                       key=lambda c: int(re.findall(r"\d+", str(c))[-1]))
    if "t" not in found or "I" not in found or ("V" not in found and not cell_cols):
        raise ValueError(f"need time, current and voltage (or cell_N) columns; got {list(df.columns)}")
    t = pd.to_numeric(df[found["t"]], errors="coerce").to_numpy(float)
    I = pd.to_numeric(df[found["I"]], errors="coerce").to_numpy(float)
    if cell_cols:
        cells = df[cell_cols].apply(pd.to_numeric, errors="coerce").to_numpy(float)
        if np.nanmedian(cells) > 100:  # mV
            cells = cells / 1000.0
    else:
        V = pd.to_numeric(df[found["V"]], errors="coerce").to_numpy(float)
        cells = V[:, None]
    T = pd.to_numeric(df[found["T"]], errors="coerce").to_numpy(float) if "T" in found else np.full(len(t), 25.0)
    ok = np.isfinite(t) & np.isfinite(I) & np.all(np.isfinite(cells), axis=1)
    t, I, cells, T = t[ok], I[ok], cells[ok], T[ok]
    # sign detection: correlation of dV with dI (discharge-positive => dV/dI < 0)
    dv, di = np.diff(cells.sum(axis=1)), np.diff(I)
    m = np.abs(di) > max(0.05 * np.nanmax(np.abs(I)), 1e-6)
    flipped = False
    if m.sum() >= 3 and np.median(dv[m] / di[m]) > 0:
        I = -I
        flipped = True
    return {"t": t, "I": I, "cells": cells, "T": T, "columns": found, "cell_columns": cell_cols,
            "rows": int(len(t)), "current_flipped": flipped}
