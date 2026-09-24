"""Algorithm A - SafeFit-R2 weakest-link engineering compatibility.

    BSS = 100 (H_Q H_R H_B H_T)^(1/4)          intrinsic health, SOC excluded
    M_E = E_usable(full, above reserve) / E_mission
    M_P = SOP_30s(end-of-mission state) / P_30s,required
    C_A = 100 * G * min(S_E, S_P, S_V, S_T, S_H)

Each margin M maps to a score S through a piecewise-linear curve anchored at
(fail -> 0), (pass -> 0.6), (target -> 1.0). Anchor values are explicit and
editable, not hidden. Property: no single deficient dimension can be averaged
away, and a FAIL gate forces C_A = 0.
"""
from __future__ import annotations

import numpy as np

from ..model.ecm import PackState
from ..model.vehicle import Vehicle, mission_requirements

ANCHORS = {  # fail, pass, target (margins as ratios; health as BSS/100)
    "energy": (0.80, 1.00, 1.25),
    "power": (0.80, 1.00, 1.20),
    "voltage": (0.95, 1.00, 1.50),   # 1.0 == loaded voltage exactly at controller cut-off
    "thermal": (0.80, 1.00, 1.50),
    "health": (0.50, 0.70, 0.90),
}


def score(m, fail, pas, target):
    if m is None:
        return None
    if m <= fail:
        return 0.0
    if m < pas:
        return 0.6 * (m - fail) / (pas - fail)
    if m < target:
        return 0.6 + 0.4 * (m - pas) / (target - pas)
    return 1.0


def lin(x, bad, good):
    return float(np.clip((x - bad) / (good - bad), 0.0, 1.0))


def bss(card: dict):
    el, th, st = card["electrical"], card["thermal"], card["state"]
    H_Q = lin(st["soh_q"], 0.60, 0.95) if st["soh_q"] else None
    H_R = lin(st["soh_r"], 0.50, 1.00) if st["soh_r"] else None
    # consistency: rest-voltage spread and per-cell resistance spread
    dv = el["rest_dv_mv"] if el.get("rest_dv_mv") is not None else el["dv_mv"]
    H_B = lin(-dv, -80.0, -10.0)
    if el["cell_dcir_mohm"]:
        r = np.array(el["cell_dcir_mohm"])
        cv = float(np.std(r) / max(np.mean(r), 1e-9))
        H_B = min(H_B, lin(-cv, -0.35, -0.08))
    dT = th["dt_cells"] if th["dt_cells"] is not None else 0.0
    rate = th["dTdt_c_per_min"] or 0.0
    H_T = min(lin(-dT, -8.0, -2.0), lin(-rate, -2.0, -0.3))
    parts = {"H_Q": H_Q, "H_R": H_R, "H_B": H_B, "H_T": H_T}
    known = [v for v in parts.values() if v is not None]
    value = 100.0 * float(np.prod(known)) ** (1.0 / len(known)) if known else None
    return {"value": value, "factors": parts, "missing": [k for k, v in parts.items() if v is None],
            "note": "SOC is excluded from BSS by design"}


def run_safefit(card: dict, veh: Vehicle, cyc: dict, gate: dict, pack, st: PackState, i_max: float):
    chem = pack.chem
    n = card["identity"]["series"]
    req = mission_requirements(veh, cyc, card["identity"].get("mass_kg") or 0.0)
    # ---- energy: full-charge usable energy above reserve (compatibility), and "ready now"
    q = float(np.mean(pack.Q))
    e_usable_full = n * q * chem.energy_Wh_per_Ah(veh.reserve_soc, 1.0)
    e_now = n * q * chem.energy_Wh_per_Ah(veh.reserve_soc, max(card["state"]["soc"], veh.reserve_soc))
    M_E = e_usable_full / req["E_mission_wh"]
    # ---- power: SOP at the END-of-mission state (reserve SOC + 10 %, vehicle ambient) - worst case
    z_eom = min(veh.reserve_soc + 0.10, 1.0)
    st_eom = PackState(np.full(len(pack.Q), z_eom), T=veh.ambient_c)
    I30, P30, lim30 = pack.sop(st_eom, 30, min(i_max, veh.max_current_a * 1.5))
    I10, P10, lim10 = pack.sop(st_eom, 10, min(i_max, veh.max_current_a * 1.5))
    M_P = min(P30 / max(req["P_30s_w"], 1.0), P10 / max(req["P_10s_w"], 1.0))
    # ---- voltage: loaded pack voltage at P_30s,req at end-of-mission vs controller cut-off
    I_req, feas = pack.current_for_power(st_eom, req["P_30s_w"])
    v_loaded = float(np.sum(pack.cell_voltages(st_eom, I_req))) if feas else 0.0
    head_need = max(n * chem.v_nom - veh.bus_v_min, 1e-6)
    M_V = max((v_loaded - veh.bus_v_min) / head_need + 1.0, 0.0) if feas else 0.0
    # ---- thermal: steady rise at mission RMS current vs allowed rise
    v_nom = n * chem.v_nom
    I_rms = req["P_rms_w"] / v_nom
    r_tot = float(np.sum(pack.r0_eff(st_eom) + pack.R1 + pack.R2))
    dT_ss = I_rms ** 2 * r_tot / pack.th.hA
    tau_th = pack.th.C_th / pack.th.hA
    t_mission = req["E_mission_wh"] * 3600.0 / max(req["P_mean_w"], 1.0)
    dT_mission = dT_ss * (1.0 - np.exp(-t_mission / tau_th))   # first-order rise over the mission
    allowed = chem.t_max_op - veh.ambient_c
    M_T = allowed / max(dT_mission, 1e-6)
    # ---- health
    h = bss(card)
    M_H = h["value"] / 100.0 if h["value"] is not None else None

    margins = {"energy": M_E, "power": M_P, "voltage": M_V, "thermal": M_T, "health": M_H}
    dims = {}
    for k, m in margins.items():
        s = score(m, *ANCHORS[k])
        status = None if s is None else ("FAIL" if s == 0 else ("MARGINAL" if s < 0.6 else "PASS"))
        dims[k] = {"margin": m, "score": s, "status": status, "anchors": ANCHORS[k]}
    known = {k: v["score"] for k, v in dims.items() if v["score"] is not None}
    limiting = min(known, key=known.get) if known else None
    G = 0.0 if gate["verdict"] == "FAIL" else 1.0
    C = 100.0 * G * (min(known.values()) if known else 0.0)
    details = {
        "energy": {"E_mission_wh": req["E_mission_wh"], "E_usable_full_wh": e_usable_full, "E_ready_now_wh": e_now,
                   "ready_now": e_now >= req["E_mission_wh"], "wh_per_km": req["wh_per_km"],
                   "range_full_km": e_usable_full / req["wh_per_km"], "range_now_km": e_now / req["wh_per_km"]},
        "power": {"P_30s_req_w": req["P_30s_w"], "P_10s_req_w": req["P_10s_w"], "P_peak_req_w": req["P_peak_w"],
                  "SOP_30s_eom_w": P30, "SOP_10s_eom_w": P10, "limiter": lim30, "eom_soc": z_eom},
        "voltage": {"v_loaded_eom": v_loaded, "bus_v_min": veh.bus_v_min, "feasible": bool(feas)},
        "thermal": {"I_rms": I_rms, "dT_steady": dT_ss, "dT_mission": dT_mission, "t_mission_s": t_mission,
                    "tau_th_s": tau_th, "allowed_rise": allowed},
        "health": h, "requirements": req,
    }
    lim_txt = None
    if limiting:
        human = {"energy": "mission energy", "power": "30-second discharge power", "voltage": "loaded voltage vs controller cut-off",
                 "thermal": "thermal rise", "health": "intrinsic battery health"}[limiting]
        lim_txt = human
    return {"algorithm": "A", "name": "SafeFit-R2 weakest-link", "score": C,
            "provisional": gate["verdict"] == "UNKNOWN", "gate_multiplier": G,
            "dimensions": dims, "limiting": limiting, "limiting_text": lim_txt, "details": details,
            "meaning": "Engineering Compatibility Index: the worst normalized margin (0-100)."}
