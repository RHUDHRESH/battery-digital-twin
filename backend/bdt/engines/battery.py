"""Canonical battery card: everything the right-hand panel and the engines consume.

SOC is one line among ~twenty here, deliberately.
"""
from __future__ import annotations

import math

import numpy as np

from ..model.chem import LFP
from ..model.estimator import Estimator


def build_battery_card(est: Estimator, tel, meta: dict) -> dict | None:
    if not est.buf:
        return None
    chem = est.chem
    t, I, vpack, cells, Tmax, soc_bms, *_ = est.buf[-1]
    n = len(cells)
    pack, st, ecm_info = est.build_pack()
    cap = est.capacity()
    nominal_Ah = meta.get("nominal_Ah", est.nominal_Ah)
    soh_q = cap["value"] / nominal_Ah if nominal_Ah else None
    r0_ref_cell = chem.r0_ref_ohm_Ah / nominal_Ah
    dcir = est.dcir_pack()
    r_ratio = (dcir["value"] / (n * r0_ref_cell)) if dcir else None  # measured DCIR_1s / BOL reference
    z = float(np.mean(st.z))
    soe_wh = n * cap["value"] * chem.energy_Wh_per_Ah(0.0, z)
    e_full_wh = n * cap["value"] * chem.energy_Wh_per_Ah(0.0, 1.0)
    i_max = meta.get("i_max_dis") or est.i_max_dis or 100.0
    sop = {}
    for label, h in (("1s", 1), ("10s", 10), ("30s", 30)):
        Ii, P, lim = pack.sop(st, h, i_max)
        sop[label] = {"I": Ii, "P_w": P, "limiter": lim}
    I_th = pack.continuous_thermal_current(st, meta.get("ambient_c", 25.0))
    Ic, Pc, limc = pack.sop(st, 600, min(i_max, I_th))
    sop["continuous"] = {"I": Ic, "P_w": Pc, "limiter": "thermal" if I_th < i_max and Ic >= min(i_max, I_th) - 1e-6 else limc}
    Ich, Pch, limch = pack.sop(st, 10, est.i_max_chg or 50.0, charge=True)
    sop["charge_10s"] = {"I": Ich, "P_w": Pch, "limiter": limch}
    temps = list(getattr(tel, "temps", []) or [])
    rc = est.dcir_cells()
    rest = abs(I) < 2.0
    card = {
        "identity": {"chemistry": chem.name, "series": n, "nominal_Ah": nominal_Ah,
                     "nominal_V": n * chem.v_nom, "v_min": n * chem.v_min, "v_max": n * chem.v_max,
                     "nominal_kWh": n * chem.v_nom * nominal_Ah / 1000, "label": meta.get("label", "Battery"),
                     "connector": meta.get("connector", ""), "protocol": meta.get("protocol", ""),
                     "mass_kg": meta.get("mass_kg", 0.0), "i_max_dis": meta.get("i_max_dis"),
                     "i_max_chg": meta.get("i_max_chg")},
        "state": {"soc": z, "soc_source": "BMS" if soc_bms is not None else "OCV (weak on LFP plateau)",
                  "soe_wh": soe_wh, "e_full_wh": e_full_wh, "soh_q": soh_q, "soh_r": (1 / r_ratio) if r_ratio else None,
                  "sop": sop, "rul": None, "rul_note": "needs >= 20 equivalent full cycles of history"},
        "electrical": {"pack_v": vpack, "current": I, "power_w": vpack * I, "dcir": dcir,
                       "ecm": ecm_info, "cells": list(map(float, cells)),
                       "cell_min": float(np.min(cells)), "cell_max": float(np.max(cells)),
                       "dv_mv": float((np.max(cells) - np.min(cells)) * 1000), "dv_at_rest": rest,
                       "rest_dv_mv": est.rest_dv_mv,
                       "cell_dcir_mohm": (rc * 1000).tolist() if rc is not None else None,
                       "weakest_cell": int(np.argmax(rc)) if rc is not None else int(np.argmin(cells)),
                       "charge_mos": getattr(tel, "charge_mos", None), "discharge_mos": getattr(tel, "discharge_mos", None)},
        "thermal": {"t_max": Tmax if not math.isnan(Tmax) else None, "temps": temps,
                    "dt_cells": (max(temps) - min(temps)) if temps else None, "dTdt_c_per_min": est.dTdt(),
                    "c_th": pack.th.C_th, "hA": pack.th.hA, "tau_th_min": pack.th.C_th / pack.th.hA / 60},
        "degradation": {"cycles": getattr(tel, "cycles", None), "capacity": cap,
                        "capacity_fade_pct": (1 - soh_q) * 100 if soh_q else None,
                        "resistance_growth_pct": (r_ratio - 1) * 100 if r_ratio else None},
        "evidence": {"source": getattr(tel, "source", ""), "t": t, "faults": list(getattr(tel, "faults", []) or []),
                     "n_dcir_events": dcir["n"] if dcir else 0, "n_rc_fits": len(est.fits),
                     "last_fit": est.fits[-1] if est.fits else None, "anchors": est.anchors[-5:],
                     "missing": [k for k, v in (("capacity measurement", cap["confidence"] in ("none", "low")),
                                                ("DCIR", dcir is None), ("2RC relaxation fit", not est.fits),
                                                ("BMS current limit", not meta.get("i_max_dis"))) if v]},
    }
    truth = getattr(tel, "truth", None)
    if truth:
        card["evidence"]["truth"] = {
            "Q_Ah_mean": float(np.mean(truth["Q_Ah"])), "R0_pack_mohm": float(np.sum(truth["R0"]) * 1000),
            "R1_pack_mohm": float(np.sum(truth["R1"]) * 1000), "tau1": truth["tau1"][0],
            "R2_pack_mohm": float(np.sum(truth["R2"]) * 1000), "tau2": truth["tau2"][0],
            "weak_cell": truth["weak_cell"], "soc_mean": float(np.mean(truth["soc_cells"])),
        }
    return card
