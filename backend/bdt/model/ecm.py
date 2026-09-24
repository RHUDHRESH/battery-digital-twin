"""Two-RC Thevenin equivalent-circuit model, vectorised over cells (and runs).

    V_t = OCV(z,T) - I*R0 - V1 - V2
    dV_k/dt = -V_k/(R_k C_k) + I/C_k        (exact zero-order-hold discretisation)
    dz/dt   = -I / (3600 Q)                 (I > 0 = discharge, A; Q in Ah)

Thermal (lumped pack):
    C_th dT/dt = Q_gen - hA (T - T_amb),   Q_gen = I^2 R0 + I V1 + I V2

Arrays broadcast: cell parameters have shape (..., n_cells) so the same code
runs a single pack (n_cells,) or a Monte-Carlo batch (n_runs, n_cells).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np

from .chem import Chemistry, r_factor


@dataclass
class CellParams:
    Q_Ah: float = 100.0
    R0: float = 0.0008      # ohm @25C mid-SOC
    R1: float = 0.0005
    tau1: float = 15.0      # s
    R2: float = 0.0007
    tau2: float = 300.0     # s

    def to_dict(self):
        d = asdict(self)
        d["C1"] = self.tau1 / self.R1
        d["C2"] = self.tau2 / self.R2
        return d


@dataclass
class ThermalParams:
    C_th: float = 38000.0   # J/K for a ~5 kWh 16s prismatic module (assumed)
    hA: float = 4.0         # W/K natural convection, closed enclosure (assumed)


class PackState:
    """Mutable state for n series cells (optionally batched over runs)."""

    def __init__(self, z, V1=None, V2=None, T=25.0):
        self.z = np.asarray(z, dtype=float).copy()
        self.V1 = np.zeros_like(self.z) if V1 is None else np.asarray(V1, float).copy()
        self.V2 = np.zeros_like(self.z) if V2 is None else np.asarray(V2, float).copy()
        # temperature is per pack (lumped): shape = z.shape[:-1]
        self.T = np.broadcast_to(np.asarray(T, float), self.z.shape[:-1]).copy()


class ECMPack:
    def __init__(self, chem: Chemistry, Q, R0, R1, tau1, R2, tau2, thermal: ThermalParams | None = None):
        self.chem = chem
        self.Q, self.R0, self.R1, self.tau1, self.R2, self.tau2 = (
            np.asarray(x, float) for x in (Q, R0, R1, tau1, R2, tau2)
        )
        self.th = thermal or ThermalParams()

    @classmethod
    def uniform(cls, chem, n, p: CellParams, thermal=None):
        f = lambda v: np.full(n, v, float)
        return cls(chem, f(p.Q_Ah), f(p.R0), f(p.R1), f(p.tau1), f(p.R2), f(p.tau2), thermal)

    # ---- instantaneous quantities -------------------------------------------------
    def r0_eff(self, st: PackState):
        return self.R0 * r_factor(self.chem, st.T[..., None], st.z)

    def cell_voltages(self, st: PackState, I):
        I = np.asarray(I, float)[..., None]
        return self.chem.ocv(st.z) - I * self.r0_eff(st) - st.V1 - st.V2

    def current_for_power(self, st: PackState, P_w):
        """Solve P = I (Vx - I Rt) for the physically meaningful root.

        Returns (I, feasible). Infeasible => demand exceeds the pack's
        maximum-power point (voltage collapse); I is clamped to that point.
        """
        Vx = np.sum(self.chem.ocv(st.z) - st.V1 - st.V2, axis=-1)
        Rt = np.sum(self.r0_eff(st), axis=-1)
        P = np.asarray(P_w, float)
        disc = Vx * Vx - 4.0 * Rt * P
        feasible = disc >= 0.0
        I = np.where(feasible, (Vx - np.sqrt(np.maximum(disc, 0.0))) / (2.0 * Rt), Vx / (2.0 * Rt))
        return I, feasible

    # ---- time step ---------------------------------------------------------------
    def step(self, st: PackState, I, dt: float, T_amb=25.0):
        I = np.asarray(I, float)
        Ic = I[..., None]
        r0 = self.r0_eff(st)
        q_gen = np.sum(Ic * Ic * r0 + Ic * st.V1 + Ic * st.V2, axis=-1)
        a1 = np.exp(-dt / self.tau1)
        a2 = np.exp(-dt / self.tau2)
        st.V1 = a1 * st.V1 + self.R1 * (1.0 - a1) * Ic
        st.V2 = a2 * st.V2 + self.R2 * (1.0 - a2) * Ic
        st.z = st.z - Ic * dt / (3600.0 * self.Q)
        st.T = st.T + dt * (q_gen - self.th.hA * (st.T - T_amb)) / self.th.C_th
        return q_gen

    # ---- State of Power ----------------------------------------------------------
    def sop(self, st: PackState, horizon_s: float, i_max: float, z_min: float = 0.02,
            v_min_cell: float | None = None, charge: bool = False):
        """Multi-constraint SOP (HPPC-style) for a constant-current pulse of length horizon_s.

        Largest current I in [0, i_max] such that, at the END of the pulse,
        every cell satisfies V >= v_min (and SOC >= z_min). Voltage is monotone
        decreasing in I, so bisection is exact to tolerance. Returns (I, P_w, limiter).
        Pack must be unbatched (shape (n_cells,)).
        """
        v_lim = self.chem.v_cut_op if v_min_cell is None else v_min_cell
        sgn = -1.0 if charge else 1.0
        a1 = np.exp(-horizon_s / self.tau1)
        a2 = np.exp(-horizon_s / self.tau2)
        r0 = self.r0_eff(st)

        def v_end(I):
            Is = sgn * I
            z = st.z - Is * horizon_s / (3600.0 * self.Q)
            v = (self.chem.ocv(z) - Is * r0 - (a1 * st.V1 + self.R1 * (1 - a1) * Is)
                 - (a2 * st.V2 + self.R2 * (1 - a2) * Is))
            return v, z

        def ok(I):
            v, z = v_end(I)
            if charge:
                return bool(np.all(v <= self.chem.v_max) and np.all(z <= 1.0))
            return bool(np.all(v >= v_lim) and np.all(z >= z_min))

        limiter = "current limit"
        if ok(i_max):
            I = i_max
        else:
            lo, hi = 0.0, i_max
            if not ok(0.0):
                return 0.0, 0.0, "already at limit"
            for _ in range(40):
                mid = 0.5 * (lo + hi)
                lo, hi = (mid, hi) if ok(mid) else (lo, mid)
            I = lo
            v, z = v_end(I + 1e-3)
            limiter = "SOC floor" if (not charge and np.any(z < z_min)) else (
                "cell max voltage" if charge else "cell min voltage")
        v, _ = v_end(I)
        return float(I), float(I * np.sum(v)), limiter

    def continuous_thermal_current(self, st: PackState, T_amb: float):
        """Steady-state current at which T_ss = T_max_op (lumped thermal)."""
        r_tot = float(np.sum(self.r0_eff(st) + self.R1 + self.R2))
        head = max(self.chem.t_max_op - T_amb, 0.0)
        return float(np.sqrt(head * self.th.hA / r_tot))
