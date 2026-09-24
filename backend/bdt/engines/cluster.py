"""Algorithm B - application-aware population sorting.

Stage 1  reject abnormal batteries: physics plausibility + Local Outlier Factor
         (density-based, in the spirit of OPTICS/DBSCAN-style screening).
Stage 2  fuzzy C-means on robust-scaled features x_b = [Q, DCIR, dT/dt, dV]
         (c chosen by the Xie-Beni index), membership
             u_ik = 1 / sum_j (||x_i-c_k|| / ||x_i-c_j||)^(2/(m-1))
Stage 3  application match: each vehicle induces minimum requirements
         (capacity, max resistance, max heating rate). For battery b:
             C_B = 100 * sum_k u_bk * pass_rate_k(vehicle)
         meaning "batteries that behave like this one meet this vehicle's
         requirement C_B % of the time". Needs a population (>= 12).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from sklearn.neighbors import LocalOutlierFactor

from ..model.chem import LFP
from ..model.vehicle import Vehicle, adapt_cycle, mission_requirements

FEATURES = [("capacity_Ah", "Capacity", "Ah"), ("dcir_mohm", "DCIR", "mΩ"),
            ("dTdt", "Heating rate @1C (model)", "°C/min"), ("dv_mv", "Cell ΔV", "mV")]
FLEET_FILE = Path(__file__).resolve().parents[2] / "data" / "fleet.json"


def heating_rate_1c(q_ah, dcir_ohm, c_th=38000.0):
    """Adiabatic initial heating rate at a 1C discharge (C/min): I^2 R / C_th. Load-normalised."""
    return q_ah * q_ah * dcir_ohm / c_th * 60.0


def load_fleet():
    if FLEET_FILE.exists():
        return json.loads(FLEET_FILE.read_text(encoding="utf-8"))
    return []


def save_fleet(rows):
    FLEET_FILE.write_text(json.dumps(rows, indent=1), encoding="utf-8")


def synthetic_population(N=120, seed=11, n_series=16, nominal_Ah=100.0):
    """SYNTHETIC modules for demoing Algorithm B. Clearly tagged; not evidence."""
    rng = np.random.default_rng(seed)
    rows = []
    groups = [  # (share, soh mean, R growth mean, label)
        (0.35, 0.95, 1.10, "light use"), (0.35, 0.86, 1.45, "moderate"), (0.22, 0.76, 1.95, "heavy"),
        (0.08, 0.84, 1.30, "mixed-lot")]
    for g_i, (share, soh, rg, lab) in enumerate(groups):
        for _ in range(int(round(N * share))):
            s = float(np.clip(rng.normal(soh, 0.025), 0.55, 1.0))
            r = float(np.clip(rng.normal(rg, 0.12), 0.9, 3.0))
            dv = float(abs(rng.normal(12 + 25 * (r - 1), 6)))
            dcir = LFP.r0_ref_ohm_Ah / nominal_Ah * n_series * r
            rows.append({"id": f"SYN-{len(rows) + 1:03d}", "synthetic": True, "lot": lab,
                         "capacity_Ah": s * nominal_Ah, "dcir_mohm": dcir * 1000,
                         "dTdt": heating_rate_1c(nominal_Ah, dcir) * float(rng.normal(1, 0.08)),
                         "dv_mv": dv, "series": n_series})
    # a few pathological units (BAT-144 style: inconsistent data)
    for k in range(3):
        rows.append({"id": f"SYN-X{k + 1}", "synthetic": True, "lot": "anomalous",
                     "capacity_Ah": float(rng.uniform(95, 104)), "dcir_mohm": float(rng.uniform(2, 6)),
                     "dTdt": float(rng.uniform(0.9, 1.6)), "dv_mv": float(rng.uniform(120, 260)), "series": n_series})
    return rows


def _fcm(X, c, m=2.0, iters=300, seed=0):
    rng = np.random.default_rng(seed)
    U = rng.dirichlet(np.ones(c), size=len(X))
    for _ in range(iters):
        Um = U ** m
        C = (Um.T @ X) / Um.sum(axis=0)[:, None]
        D = np.linalg.norm(X[:, None, :] - C[None, :, :], axis=2) + 1e-12
        U_new = 1.0 / np.sum((D[:, :, None] / D[:, None, :]) ** (2 / (m - 1)), axis=2)
        if np.max(np.abs(U_new - U)) < 1e-6:
            U = U_new
            break
        U = U_new
    D = np.linalg.norm(X[:, None, :] - C[None, :, :], axis=2) + 1e-12
    xb = float(np.sum((U ** m) * D ** 2) / (len(X) * min(
        np.linalg.norm(C[a] - C[b]) ** 2 for a in range(c) for b in range(c) if a != b)))
    return U, C, xb


def membership(x, C, m=2.0):
    D = np.linalg.norm(C - x[None, :], axis=1) + 1e-12
    return 1.0 / np.sum((D[:, None] / D[None, :]) ** (2 / (m - 1)), axis=1)


def vehicle_requirements(veh: Vehicle, cyc: dict, n_series=16):
    """Translate a vehicle mission into battery feature limits."""
    req = mission_requirements(veh, adapt_cycle(cyc, veh))
    chem = LFP
    e_per_ah = n_series * chem.energy_Wh_per_Ah(veh.reserve_soc, 1.0)
    q_min = req["E_mission_wh"] / e_per_ah
    # resistance ceiling: at P_30s and 30 % SOC, loaded pack voltage must stay above controller cut-off
    v_oc = n_series * float(chem.ocv(0.30))
    P = req["P_30s_w"]
    # V(V_oc - V)/R = P with V = bus_v_min  ->  R = V (V_oc - V) / P
    V = max(veh.bus_v_min, n_series * chem.v_cut_op)
    r_max = V * max(v_oc - V, 0) / max(P, 1.0) / 1.25  # /1.25: DCIR_1s understates 30 s resistance
    return {"capacity_Ah_min": q_min, "dcir_mohm_max": r_max * 1000, "dTdt_max": 1.0, "dv_mv_max": 60.0,
            "requirements": req}


def _passes(row, lim):
    return (row["capacity_Ah"] >= lim["capacity_Ah_min"] and row["dcir_mohm"] <= lim["dcir_mohm_max"]
            and row["dTdt"] <= lim["dTdt_max"] and row["dv_mv"] <= lim["dv_mv_max"])


def run_clustering(rows, current: dict | None, vehicles: list[Vehicle], cycles: dict, target_vehicle: Vehicle,
                   gate: dict | None = None):
    if len(rows) < 12:
        return {"algorithm": "B", "name": "Application-aware sorting", "score": None,
                "error": f"population too small ({len(rows)}); need >= 12 batteries. "
                         "Snapshot more batteries, import a dataset, or seed the synthetic demo fleet."}
    keys = [f[0] for f in FEATURES]
    X_raw = np.array([[r[k] for k in keys] for r in rows], float)
    # stage 1: physics plausibility + LOF
    phys_bad = (X_raw[:, 0] <= 0) | (X_raw[:, 1] <= 0) | (X_raw[:, 3] > 400)
    med = np.median(X_raw, axis=0)
    iqr = np.subtract(*np.percentile(X_raw, [75, 25], axis=0))
    iqr[iqr == 0] = 1.0
    X = (X_raw - med) / iqr
    lof = LocalOutlierFactor(n_neighbors=min(20, len(rows) - 1))
    lof_lab = lof.fit_predict(X)
    lof_score = -lof.negative_outlier_factor_
    outlier = phys_bad | (lof_lab == -1)
    inl = ~outlier
    # stage 2: fuzzy c-means with c by Xie-Beni
    best = None
    for c in range(2, 6):
        if inl.sum() < 3 * c:
            break
        U, C, xb = _fcm(X[inl], c)
        if best is None or xb < best[2]:
            best = (U, C, xb, c)
    U, C, xb, c = best
    centers_raw = C * iqr + med
    # name clusters by their centroid: energy (capacity rank) / power (resistance rank)
    q_rank = np.argsort(np.argsort(-centers_raw[:, 0]))
    r_rank = np.argsort(np.argsort(centers_raw[:, 1]))
    names = []
    for k in range(c):
        e = "high-energy" if q_rank[k] == 0 else ("low-energy" if q_rank[k] == c - 1 else "mid-energy")
        p = "high-power" if r_rank[k] == 0 else ("low-power" if r_rank[k] == c - 1 else "mid-power")
        names.append(f"{e} / {p}")
    hard = np.argmax(U, axis=1)
    inl_rows = [r for r, ok in zip(rows, inl) if ok]
    # stage 3: pass-rate per cluster per vehicle
    matrix = []
    for v in vehicles:
        lim = vehicle_requirements(v, cycles[v.cycle])
        rates = []
        for k in range(c):
            members = [r for r, h in zip(inl_rows, hard) if h == k]
            w = U[:, k]
            ok = np.array([_passes(r, lim) for r in inl_rows], float)
            rates.append(float(np.sum(w * ok) / max(np.sum(w), 1e-9)))
        matrix.append({"vehicle": v.id, "name": v.name, "limits": {k: lim[k] for k in lim if k != "requirements"},
                       "pass_rate": rates})
    points = []
    for i, r in enumerate(rows):
        points.append({"id": r["id"], "x": [float(v) for v in X_raw[i]], "outlier": bool(outlier[i]),
                       "lof": float(lof_score[i]), "synthetic": r.get("synthetic", False), "lot": r.get("lot"),
                       "cluster": int(hard[int(np.sum(inl[:i]))]) if inl[i] else None})
    out = {"algorithm": "B", "name": "Application-aware sorting", "n": len(rows), "n_outliers": int(outlier.sum()),
           "c": c, "xie_beni": xb, "features": FEATURES, "clusters": [
               {"k": k, "name": names[k], "center": centers_raw[k].tolist(), "size": int(np.sum(hard == k))}
               for k in range(c)], "matrix": matrix, "points": points,
           "synthetic_share": float(np.mean([r.get("synthetic", False) for r in rows]))}
    if current:
        x = np.array([current[k] for k in keys], float)
        xs = (x - med) / iqr
        u = membership(xs, C)
        # novelty: LOF score of the current battery relative to the population
        lof_n = LocalOutlierFactor(n_neighbors=min(20, len(rows) - 1), novelty=True).fit(X)
        is_out = bool(lof_n.predict(xs[None, :])[0] == -1)
        tv = next((m for m in matrix if m["vehicle"] == target_vehicle.id), None)
        lim = vehicle_requirements(target_vehicle, cycles[target_vehicle.cycle])
        rates = tv["pass_rate"] if tv else [0.0] * c
        G = 0.0 if (gate and gate["verdict"] == "FAIL") or is_out else 1.0
        score = 100.0 * G * float(np.dot(u, rates))
        out.update({"score": score, "provisional": bool(gate and gate["verdict"] == "UNKNOWN"),
                    "current": {"x": x.tolist(), "membership": u.tolist(), "outlier": is_out,
                                "direct_pass": _passes(dict(zip(keys, x)), lim), "limits": lim},
                    "meaning": "Membership-weighted share of similar batteries that meet this vehicle's "
                               "derived feature limits."})
    else:
        out["score"] = None
    return out
