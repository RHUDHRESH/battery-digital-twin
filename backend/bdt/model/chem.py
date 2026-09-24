"""Chemistry profiles.

Every threshold lives here as a *profile*, not as a hard-coded universal number
(the ASME lesson: limits differ per chemistry / cell design / OEM).

The LFP OCV table below is a GENERIC shape for a graphite/LFP cell at 25 C.
It is an assumption, not a measurement: replace it with an OCV curve measured
on your own cells (C/20 or GITT) as soon as you have one. Its falsification
test: rest a cell >2 h at several SOC points and compare terminal voltage.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np


@dataclass
class Chemistry:
    name: str
    soc_pts: list[float]
    ocv_pts: list[float]
    v_min: float            # absolute cell minimum (V)
    v_max: float            # absolute cell maximum (V)
    v_nom: float
    v_cut_op: float         # operational discharge cut-off used for SOP / twin (V)
    t_dis: tuple[float, float] = (-20.0, 60.0)   # discharge temperature window (C)
    t_chg: tuple[float, float] = (0.0, 45.0)     # charge temperature window (C)
    t_max_op: float = 55.0  # temperature ceiling used by the twin (C)
    arrhenius_B: float = 2500.0  # K; resistance temperature sensitivity (assumed)
    # reference (beginning-of-life) per-cell DCIR_1s per Ah of capacity:
    # R_ref_cell ~= r0_ref_ohm_Ah / Q_Ah   (assumed: 0.8 mOhm for a 100 Ah prismatic; calibrate per cell model)
    r0_ref_ohm_Ah: float = 0.08
    notes: str = ""

    def ocv(self, z):
        return np.interp(np.clip(z, 0.0, 1.0), self.soc_pts, self.ocv_pts)

    def soc_from_ocv(self, v):
        # OCV is monotone; invert by interpolation. Flat LFP plateau => poor
        # resolution in 20-80 %, which is exactly why LFP SOC-from-voltage is weak.
        return np.interp(v, self.ocv_pts, self.soc_pts)

    def energy_Wh_per_Ah(self, z_from: float, z_to: float) -> float:
        """Integral of OCV dz between two SOCs => Wh per Ah of capacity (per cell)."""
        lo, hi = sorted((z_from, z_to))
        zs = np.linspace(lo, hi, 200)
        return float(np.trapezoid(self.ocv(zs), zs))

    def to_dict(self):
        return asdict(self)


LFP = Chemistry(
    name="LFP",
    soc_pts=[0.00, 0.02, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95, 0.98, 1.00],
    ocv_pts=[2.50, 2.90, 3.10, 3.20, 3.23, 3.25, 3.275, 3.29, 3.30, 3.31, 3.325, 3.335, 3.345, 3.36, 3.40, 3.45],
    v_min=2.50,
    v_max=3.65,
    v_nom=3.20,
    v_cut_op=2.70,
    notes="Generic LFP OCV shape (assumption). Replace with measured OCV-SOC.",
)

CHEMISTRIES = {"LFP": LFP}


def r_factor(chem: Chemistry, T_c, z):
    """Multiplicative resistance correction for temperature (Arrhenius) and low SOC.

    Both functional forms are standard modelling choices; the coefficients are
    assumptions until fitted from HPPC at several temperatures / SOCs.
    """
    T_k = np.asarray(T_c, dtype=float) + 273.15
    arr = np.exp(chem.arrhenius_B * (1.0 / T_k - 1.0 / 298.15))
    low_soc = 1.0 + 0.8 * np.clip((0.15 - np.asarray(z, dtype=float)) / 0.15, 0.0, 1.0)
    return arr * low_soc
