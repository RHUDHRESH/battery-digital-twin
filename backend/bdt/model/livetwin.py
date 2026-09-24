"""Live digital twin: a per-cell 2RC model that runs alongside the real battery.

Each telemetry sample:
  1. advance the model over the elapsed interval with the previously measured
     current (zero-order hold) -> exact 2RC discretisation (ECMPack.step)
  2. predict every cell's terminal voltage at the newly measured current
  3. residual = measured - predicted  (per cell and pack)
The model uses measured temperature for its resistance correction and is
nudged toward the BMS SOC with a slow complementary filter (gain k per sample),
so drift in coulomb counting cannot run away while fast dynamics stay
model-driven. Parameters are re-pulled from the estimator every 60 s.

OCV self-calibration: the chemistry's OCV table is generic. After >= 5 min of
rest (polarisation mostly decayed) the mean per-cell residual is attributed to
OCV error at the current SOC and learned into a 5 %-SOC-bin correction table
(pack-wide, NOT per cell, so real cell-to-cell SOC differences stay visible).
The table is exported with sessions so the correction is reproducible.

Fidelity = RMS pack residual over a rolling window. A cell whose residual grows
under load is behaving differently from the model: an R&D signal.
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

from .ecm import ECMPack, PackState
from .estimator import Estimator


class LiveTwin:
    SOC_GAIN = 0.01          # complementary-filter gain toward BMS SOC per sample
    REFIT_S = 60.0
    REST_LEARN_S = 300.0     # rest needed before a residual is attributed to OCV error
    OCV_GAIN = 0.05          # learning rate of the OCV correction per rested sample
    BINS = 20                # 5 % SOC bins

    def __init__(self):
        self.pack: ECMPack | None = None
        self.st: PackState | None = None
        self.last_t = None
        self.last_I = 0.0
        self.last_param_t = -1e9
        self.hist: deque = deque(maxlen=36000)   # ~10 h at 1 Hz
        self.cell_resid: deque = deque(maxlen=600)
        self.latest: dict | None = None
        self.n = None
        self.ocv_corr = np.zeros(self.BINS)      # V per cell, by SOC bin
        self.ocv_n = np.zeros(self.BINS, int)    # rested samples learned per bin
        self.rest_since: float | None = None

    def _corr(self, z):
        zb = np.clip((np.asarray(z) * self.BINS).astype(int), 0, self.BINS - 1)
        return self.ocv_corr[zb]

    def ocv_table(self):
        return [{"soc_lo": k / self.BINS, "soc_hi": (k + 1) / self.BINS, "offset_mV": float(self.ocv_corr[k] * 1000),
                 "n": int(self.ocv_n[k])} for k in range(self.BINS) if self.ocv_n[k] > 0]

    def _rebuild(self, est: Estimator, t: float):
        pack, st, _ = est.build_pack()
        if self.st is not None and self.n == len(pack.Q):
            pack_state = self.st          # keep dynamic state, swap parameters only
        else:
            pack_state = st
        self.pack, self.st, self.n = pack, pack_state, len(pack.Q)
        self.last_param_t = t

    def update(self, est: Estimator, t: float, I: float, cells, temps, soc_bms):
        cells = np.asarray(cells, float)
        if self.pack is None or len(cells) != self.n or t - self.last_param_t > self.REFIT_S:
            self._rebuild(est, t)
        T = float(max(temps)) if temps else 25.0
        if self.last_t is not None:
            dt = t - self.last_t
            if 0 < dt < 30:
                self.st.T = np.asarray(T, float)
                self.pack.step(self.st, self.last_I, dt, T_amb=T)
                if soc_bms is not None:
                    self.st.z = self.st.z + self.SOC_GAIN * (soc_bms - float(np.mean(self.st.z)))
            elif dt >= 30:  # gap in data: resynchronise instead of integrating across it
                self._rebuild(est, t)
                self.st.V1[:] = 0
                self.st.V2[:] = 0
        self.st.T = np.asarray(T, float)
        pred = self.pack.cell_voltages(self.st, I) + self._corr(self.st.z)
        resid = cells - pred
        # rest tracking + OCV self-calibration
        if abs(I) < 0.5:
            self.rest_since = t if self.rest_since is None else self.rest_since
            if t - self.rest_since >= self.REST_LEARN_S:
                k = int(np.clip(np.mean(self.st.z) * self.BINS, 0, self.BINS - 1))
                self.ocv_corr[k] += self.OCV_GAIN * float(np.mean(resid))
                self.ocv_n[k] += 1
        else:
            self.rest_since = None
        self.cell_resid.append(resid)
        self.last_t, self.last_I = t, I
        row = {"t": t, "I": float(I), "V": float(cells.sum()), "V_pred": float(pred.sum()),
               "res_mV": float(resid.sum() * 1000), "soc_bms": soc_bms, "soc_twin": float(np.mean(self.st.z)),
               "T": T, "cmin": float(cells.min()), "cmax": float(cells.max()), "dv_mV": float((cells.max() - cells.min()) * 1000)}
        self.hist.append(row)
        self.latest = row
        return row

    def fidelity(self, window_s: float = 600.0):
        if not self.hist:
            return None
        t_end = self.hist[-1]["t"]
        r = np.array([h["res_mV"] for h in self.hist if h["t"] >= t_end - window_s])
        cr = np.array(self.cell_resid)[-min(len(self.cell_resid), 600):]
        loaded = [h for h in self.hist if h["t"] >= t_end - window_s and abs(h["I"]) > 2]
        return {
            "rmse_mV": float(np.sqrt(np.mean(r ** 2))), "max_abs_mV": float(np.max(np.abs(r))), "n": int(len(r)),
            "rmse_loaded_mV": float(np.sqrt(np.mean([h["res_mV"] ** 2 for h in loaded]))) if loaded else None,
            "cell_bias_mV": (cr.mean(axis=0) * 1000).tolist() if len(cr) else None,
            "cell_rms_mV": (np.sqrt((cr ** 2).mean(axis=0)) * 1000).tolist() if len(cr) else None,
            "soc_twin": float(np.mean(self.st.z)) if self.st is not None else None,
            "window_s": window_s,
            "resting_s": (t_end - self.rest_since) if self.rest_since is not None else 0.0,
            "ocv_calibration": self.ocv_table(),
        }

    def history(self, window_s: float = 600.0, max_points: int = 600):
        if not self.hist:
            return []
        t_end = self.hist[-1]["t"]
        rows = [h for h in self.hist if h["t"] >= t_end - window_s]
        step = max(1, len(rows) // max_points)
        return rows[::step]


def forecast(pack: ECMPack, st: PackState, profile: list[dict], T_amb: float, i_max: float | None,
             v_cut: float | None = None, dt: float = 1.0, max_s: float = 6 * 3600, ocv_corr=None):
    """Run the twin forward from the current state through a load profile.

    profile: [{"mode": "current"|"power"|"rest", "value": A or W (+ = discharge), "duration_s": s}]
    Returns a trace and the first constraint violation. Charge is negative current/power.
    """
    chem = pack.chem
    v_cut = v_cut or chem.v_cut_op
    s = PackState(st.z.copy(), st.V1.copy(), st.V2.copy(), T=float(st.T))
    tr = {k: [] for k in ("t", "I", "V", "Vcell_min", "Vcell_max", "soc", "T", "P")}
    t = 0.0
    first = None
    e_wh = 0.0
    seg_idx = 0
    total = sum(float(p.get("duration_s", 0)) for p in profile)
    stride = max(1, int(total / dt / 900))   # keep traces <= ~900 points
    k = 0
    for seg in profile:
        dur = float(seg.get("duration_s", 0))
        mode = seg.get("mode", "current")
        val = float(seg.get("value", 0.0))
        steps = int(max(1, math.ceil(dur / dt)))
        for _ in range(steps):
            if t >= max_s or (first is not None and t > first["t_s"] + 30):
                break  # a real BMS disconnects at the first violation; stop shortly after it
            if mode == "power":
                I, feas = pack.current_for_power(s, val)
                I = float(np.asarray(I))
                if not bool(np.asarray(feas)) and first is None:
                    first = {"t_s": t, "segment": seg_idx, "mode": "power not deliverable (voltage collapse)"}
            elif mode == "rest":
                I = 0.0
            else:
                I = val
            vc = pack.cell_voltages(s, I) + (ocv_corr(s.z) if ocv_corr else 0.0)
            vmin, vmax = float(vc.min()), float(vc.max())
            V = float(vc.sum())
            checks = [("cell under-voltage", vmin < v_cut), ("cell over-voltage", vmax > chem.v_max),
                      ("over-current", i_max is not None and abs(I) > i_max),
                      ("over-temperature", float(s.T) > chem.t_max_op), ("SOC depleted", float(s.z.min()) <= 0.0),
                      ("SOC full", float(s.z.max()) >= 1.0 and I < 0)]
            for name, bad in checks:
                if bad and first is None:
                    first = {"t_s": t, "segment": seg_idx, "mode": name, "I": I, "V": V, "Vcell_min": vmin,
                             "Vcell_max": vmax, "soc": float(s.z.mean()), "T": float(s.T),
                             "cell": int(np.argmin(vc) if "under" in name else np.argmax(vc)) + 1}
            if k % stride == 0:
                for key, v in (("t", t), ("I", I), ("V", V), ("Vcell_min", vmin), ("Vcell_max", vmax),
                             ("soc", float(s.z.mean())), ("T", float(s.T)), ("P", V * I)):
                    tr[key].append(v)
            e_wh += V * I * dt / 3600.0
            pack.step(s, I, dt, T_amb=T_amb)
            t += dt
            k += 1
        seg_idx += 1
        if first is not None and t > first["t_s"] + 30:
            break
    return {"trace": tr, "first_violation": first, "duration_s": t, "energy_wh": e_wh,
            "end_soc": float(s.z.mean()), "min_cell": float(min(tr["Vcell_min"])) if tr["Vcell_min"] else None,
            "peak_T": float(max(tr["T"])) if tr["T"] else None, "v_cut": v_cut, "v_max": chem.v_max,
            "t_max": chem.t_max_op}
