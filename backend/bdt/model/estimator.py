"""Online state / parameter estimation from BMS-only telemetry (no test gear).

What can honestly be identified from a BMS stream, and how:
  * DCIR (pack + per cell): natural current steps, R = -dV/dI across one sample.
    At ~1 s sampling this is "DCIR_1s": R0 plus part of the fast RC branch.
  * 2RC parameters: relaxation after a load->rest transition, fitted to
        V(t) = V_inf - A1 exp(-t/tau1) - A2 exp(-t/tau2)
    with R_k = A_k / (I_load (1 - exp(-T_load/tau_k)))  (partial-charge correction).
  * Capacity: BMS residual/SOC ratio (low confidence) or coulomb counting between
    two OCV-anchored rests in the steep part of the LFP curve (higher confidence).
  * dT/dt: least-squares slope of pack max temperature over the last 5 min.
Every output carries its source and confidence so the UI can show evidence.
"""
from __future__ import annotations

import math
import time
from collections import deque

import numpy as np
from scipy.optimize import curve_fit

from .chem import Chemistry, LFP
from .ecm import CellParams, ECMPack, PackState


class Estimator:
    def __init__(self, chem: Chemistry = LFP, nominal_Ah: float = 100.0, i_max_dis: float | None = 150.0,
                 i_max_chg: float | None = 50.0):
        self.chem = chem
        self.nominal_Ah = nominal_Ah
        self.i_max_dis, self.i_max_chg = i_max_dis, i_max_chg
        self.buf: deque = deque(maxlen=4000)
        self.dcir: deque = deque(maxlen=120)          # pack ohm
        self.cell_dcir: deque = deque(maxlen=120)     # arrays (ohm per cell)
        self.fits: deque = deque(maxlen=20)
        self.anchors: list[dict] = []
        self.ah_counter = 0.0
        self.q_estimates: deque = deque(maxlen=20)
        self._load_start: float | None = None
        self._load_I = 0.0
        self._rest_start_t: float | None = None
        self._rest_meta = None
        self.last_t = None
        self.rest_dv_mv: float | None = None   # cell spread measured at rest (>= 60 s, |I| < 2 A)
        self._rest_since: float | None = None

    # ------------------------------------------------------------------ ingest
    def update(self, t: float, I: float, cells: list[float], temps: list[float], soc_bms=None,
               residual_Ah=None, full_Ah=None):
        cells = np.asarray(cells, float)
        vpack = float(cells.sum())  # mV-resolution pack voltage
        T = max(temps) if temps else float("nan")
        if self.last_t is not None:
            dt = t - self.last_t
            if 0 < dt < 30:
                self.ah_counter += I * dt / 3600.0
        if abs(I) < 2.0:
            self._rest_since = t if self._rest_since is None else self._rest_since
            if t - self._rest_since >= 60:
                self.rest_dv_mv = float((cells.max() - cells.min()) * 1000)
        else:
            self._rest_since = None
        prev = self.buf[-1] if self.buf else None
        self.buf.append((t, I, vpack, cells, T, soc_bms, residual_Ah, full_Ah))
        self.last_t = t
        if prev is None:
            return
        pt, pI, pV, pc, *_ = prev
        dI = I - pI
        if t - pt <= 3.5 and abs(dI) >= max(5.0, 0.08 * self.nominal_Ah):
            R = -(vpack - pV) / dI
            Rc = -(cells - pc) / dI
            if 0 < R < 1.0 and np.all(Rc > -0.002):
                self.dcir.append((t, R, dI))
                self.cell_dcir.append(Rc)
        self._track_relaxation(t, I, pI)

    def _track_relaxation(self, t, I, pI):
        thr = 2.0
        if abs(I) >= thr:
            if self._load_start is None or abs(I - self._load_I) > max(5.0, 0.2 * abs(self._load_I)):
                self._load_start, self._load_I = t, I
            self._maybe_fit(exclude_last=True)
            self._rest_start_t = None
        else:
            if abs(pI) >= thr and self._load_start is not None:
                self._rest_start_t = t
                self._rest_meta = {"I": self._load_I, "T_load": t - self._load_start, "t0": t}
            self._load_start = None
            if self._rest_start_t is not None:
                rest_T = t - self._rest_start_t
                if rest_T >= 900:
                    self._maybe_fit()
                    self._rest_start_t = None
                self._check_anchor(t, rest_T)

    def _maybe_fit(self, exclude_last=False):
        if self._rest_start_t is None or self._rest_meta is None:
            return
        seg = [s for s in (list(self.buf)[:-1] if exclude_last else self.buf) if s[0] >= self._rest_start_t]
        if len(seg) < 25 or seg[-1][0] - seg[0][0] < 90:
            self._rest_meta = None
            return
        t0 = seg[0][0]
        ts = np.array([s[0] - t0 for s in seg])
        vs = np.array([s[2] for s in seg])
        meta = self._rest_meta
        self._rest_meta = None
        fit = fit_relaxation(ts, vs, meta["I"], meta["T_load"])
        if fit:
            fit["t"] = time.time()
            fit["I_load"] = meta["I"]
            fit["T_load"] = meta["T_load"]
            self.fits.append(fit)

    def _check_anchor(self, t, rest_T):
        """OCV anchor: >=10 min rest with cells in a steep (identifiable) OCV region."""
        if rest_T < 600 or (self.anchors and t - self.anchors[-1]["t"] < 900):
            return
        cells = self.buf[-1][3]
        v = float(np.mean(cells))
        if v < 3.22 or v > 3.35:
            z = float(self.chem.soc_from_ocv(v))
            self.anchors.append({"t": t, "z": z, "ah": self.ah_counter, "v": v})
            if len(self.anchors) >= 2:
                a, b = self.anchors[-2], self.anchors[-1]
                dz = a["z"] - b["z"]
                if abs(dz) > 0.3:
                    self.q_estimates.append((b["ah"] - a["ah"]) / dz)

    # ------------------------------------------------------------------ outputs
    def dcir_pack(self):
        if not self.dcir:
            return None
        r = np.array([d[1] for d in self.dcir])
        return {"value": float(np.median(r)), "p10": float(np.percentile(r, 10)),
                "p90": float(np.percentile(r, 90)), "n": len(r), "source": "natural load steps (DCIR_1s)"}

    def dcir_cells(self):
        if not self.cell_dcir:
            return None
        return np.median(np.array(self.cell_dcir), axis=0)

    def capacity(self):
        if self.q_estimates:
            q = float(np.median(self.q_estimates))
            return {"value": q, "source": "coulomb count between OCV anchors", "confidence": "medium",
                    "n": len(self.q_estimates)}
        last = self.buf[-1] if self.buf else None
        if last and last[7]:
            return {"value": float(last[7]), "source": "BMS full-capacity register", "confidence": "low"}
        if last and last[6] and last[5] and last[5] > 0.1:
            return {"value": float(last[6] / last[5]), "source": "BMS residual/SOC ratio", "confidence": "low"}
        return {"value": self.nominal_Ah, "source": "nameplate (no measurement)", "confidence": "none"}

    def dTdt(self):
        pts = [(s[0], s[4]) for s in self.buf if not math.isnan(s[4])]
        if len(pts) < 10:
            return None
        t_end = pts[-1][0]
        pts = [p for p in pts if p[0] >= t_end - 300]
        if len(pts) < 10:
            return None
        t, T = np.array(pts).T
        slope = np.polyfit(t - t[0], T, 1)[0] * 60.0
        return float(slope)

    def ecm_params(self):
        """Best current 2RC parameter set (per cell) with provenance."""
        n = len(self.buf[-1][3]) if self.buf else 16
        cap = self.capacity()
        p = CellParams(Q_Ah=cap["value"])
        src = {"R0": "default", "RC": "default (tau1=15 s, tau2=300 s)"}
        d = self.dcir_pack()
        if self.fits:
            # robust: median across fits; R_k only trusted when the preceding load lasted
            # >= 0.3 tau_k (else the partial-charge correction amplifies noise)
            fs = list(self.fits)
            med = lambda key, ok: float(np.median([f[key] for f in fs if ok(f)])) if any(ok(f) for f in fs) else None
            tau1 = med("tau1", lambda f: True)
            tau2 = med("tau2", lambda f: True)
            R1 = med("R1", lambda f: f["T_load"] >= 0.3 * f["tau1"])
            R2 = med("R2", lambda f: f["T_load"] >= 0.3 * f["tau2"])
            p.tau1, p.tau2 = tau1, tau2
            p.R1 = R1 / n if R1 else p.R1
            p.R2 = R2 / n if R2 else p.R2
            src["RC"] = (f"median of {len(fs)} relaxation fits (last rmse {fs[-1]['rmse_mV']:.2f} mV)"
                         + ("" if R2 else "; R2 default - no load >= 0.3 tau2 yet"))
        if d:
            # DCIR_1s ~ R0 + R1(1-exp(-1/tau1)); remove the RC share
            r0_pack = d["value"] - n * p.R1 * (1 - math.exp(-1.0 / p.tau1))
            p.R0 = max(r0_pack / n, 1e-5)
            src["R0"] = "DCIR_1s minus fast-RC share"
        return p, src

    def build_pack(self) -> tuple[ECMPack, PackState, dict]:
        last = self.buf[-1]
        cells = last[3]
        n = len(cells)
        p, src = self.ecm_params()
        rc = self.dcir_cells()
        if rc is not None and np.all(rc > 0):
            r0_cells = rc / rc.mean() * p.R0
        else:
            r0_cells = np.full(n, p.R0)
        pack = ECMPack(self.chem, np.full(n, p.Q_Ah), r0_cells, np.full(n, p.R1), np.full(n, p.tau1),
                       np.full(n, p.R2), np.full(n, p.tau2))
        # SOC from BMS if present; LFP voltage-based SOC is unreliable on the plateau
        z_b = last[5] if last[5] is not None else float(self.chem.soc_from_ocv(np.mean(cells)))
        st = PackState(np.full(n, z_b), T=last[4] if not math.isnan(last[4]) else 25.0)
        return pack, st, {"cell": p.to_dict(), "provenance": src}


def fit_relaxation(ts, vs, I_load, T_load):
    """Fit V(t) = Vinf - A1 e^{-t/tau1} - A2 e^{-t/tau2}; returns pack-level 2RC params."""
    if abs(I_load) < 1e-6:
        return None
    sgn = 1.0 if I_load > 0 else -1.0
    y = sgn * (vs[-1] - vs)  # positive, decaying toward 0
    if y[0] <= 0:
        return None

    def model(t, vinf, a1, tau1, a2, tau2):
        return vinf - a1 * np.exp(-t / tau1) - a2 * np.exp(-t / tau2)

    v = sgn * vs
    try:
        p0 = [v[-1], y[0] * 0.5, 12.0, y[0] * 0.5, 250.0]
        lb = [v[-1] - 1.0, 0, 1.0, 0, 60.0]
        ub = [v[-1] + 1.0, 5.0, 60.0, 5.0, 3000.0]
        popt, pcov = curve_fit(model, ts, v, p0=p0, bounds=(lb, ub), maxfev=20000)
    except Exception:
        return None
    vinf, a1, tau1, a2, tau2 = popt
    resid = v - model(ts, *popt)
    perr = np.sqrt(np.clip(np.diag(pcov), 0, None))
    I = abs(I_load)
    R1 = a1 / (I * (1 - math.exp(-T_load / tau1)))
    R2 = a2 / (I * (1 - math.exp(-T_load / tau2)))
    # instantaneous jump at the step: first rest sample vs model at t=0 gives R0-ish
    return {"R1": float(R1), "tau1": float(tau1), "R2": float(R2), "tau2": float(tau2),
            "sd_tau1": float(perr[2]), "sd_tau2": float(perr[4]),
            "rmse_mV": float(np.sqrt(np.mean(resid ** 2)) * 1000), "n": int(len(ts)),
            "curve": {"t": ts[:: max(1, len(ts) // 120)].tolist(),
                      "v": vs[:: max(1, len(ts) // 120)].tolist(),
                      "fit": (sgn * model(ts, *popt))[:: max(1, len(ts) // 120)].tolist()}}
