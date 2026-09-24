"""Algorithm C - probabilistic electro-thermal battery/vehicle digital twin.

For each Monte-Carlo run: sample uncertain inputs, drive the 2RC + lumped
thermal pack with the vehicle's power demand second by second, and record the
first constraint violation (time, mode, state). All runs are vectorised in
numpy over axis 0 (runs) x axis 1 (cells).

    C_C = P(success) = successes / N,  with a Wilson 95 % interval.

"Success" = the declared mission distance is completed with, at every step:
  min cell V >= chem.v_cut_op, pack V >= controller cut-off, I <= BMS limit,
  T <= chem.t_max_op, and final SOC >= reserve. The controller clips battery
  current at its own limit; demand it cannot meet (or beyond the max-power
  point) is counted as a performance deficit (seconds), not a safety failure.
The number means exactly: "fraction of declared scenarios that passed the
declared constraints" - nothing more. Sensitivity is Spearman-rank based
(approximate attribution, not a variance decomposition).
"""
from __future__ import annotations

import math

import numpy as np
from scipy.stats import spearmanr

from ..model.ecm import ECMPack, PackState
from ..model.vehicle import Vehicle, power_demand

DEFAULT_UNCERTAINTY = {
    "payload_kg": {"dist": "uniform", "lo": -100.0, "hi": 100.0, "label": "Payload"},
    "ambient_c": {"dist": "uniform", "lo": -8.0, "hi": 8.0, "label": "Ambient temperature"},
    "soc0": {"dist": "normal", "sd": 0.02, "label": "Initial SOC"},
    "r_scale": {"dist": "normal", "sd": 0.07, "label": "Resistance estimate"},
    "q_scale": {"dist": "normal", "sd": 0.03, "label": "Capacity estimate"},
    "accel": {"dist": "lognormal", "sd": 0.15, "label": "Driver aggressiveness"},
    "grade": {"dist": "normal", "sd": 0.10, "label": "Road grade"},
    "aux": {"dist": "uniform", "lo": 0.7, "hi": 1.6, "label": "Auxiliary load"},
}


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _sample(unc, N, rng):
    s = {}
    for k, u in unc.items():
        if u["dist"] == "uniform":
            s[k] = rng.uniform(u["lo"], u["hi"], N)
        elif u["dist"] == "normal":
            s[k] = rng.normal(0.0, u["sd"], N)
        elif u["dist"] == "lognormal":
            s[k] = rng.lognormal(0.0, u["sd"], N)
    return s


def simulate(pack: ECMPack, soc0, veh: Vehicle, cyc: dict, N=400, seed=0, i_max=150.0, uncertainty=None,
             nominal=False, battery_mass_kg=0.0, trace_every=5):
    unc = uncertainty or DEFAULT_UNCERTAINTY
    rng = np.random.default_rng(seed)
    if nominal:
        N = 1
        s = {k: (np.ones(1) if (u["dist"] == "lognormal" or k == "aux") else np.zeros(1)) for k, u in unc.items()}
    else:
        s = _sample(unc, N, rng)
    chem = pack.chem
    n = len(pack.Q)
    r_sc = (1.0 + s["r_scale"])[:, None]
    Q = pack.Q[None, :] * (1.0 + s["q_scale"])[:, None]
    R0, R1, R2 = pack.R0[None, :] * r_sc, pack.R1[None, :] * r_sc, pack.R2[None, :] * r_sc
    dt = cyc["dt"]
    a1, a2 = np.exp(-dt / pack.tau1)[None, :], np.exp(-dt / pack.tau2)[None, :]
    b1, b2 = R1 * (1 - a1), R2 * (1 - a2)
    soc_pts, ocv_pts = np.asarray(chem.soc_pts), np.asarray(chem.ocv_pts)
    B, C_th, hA = chem.arrhenius_B, pack.th.C_th, pack.th.hA
    T_amb = veh.ambient_c + s["ambient_c"]
    z = np.clip(np.asarray(soc0, float)[None, :] + s["soc0"][:, None], 0.02, 1.0)
    V1 = np.zeros((N, n))
    V2 = np.zeros((N, n))
    T = T_amb.copy()
    demand = power_demand(veh, cyc, battery_mass_kg, payload_kg=veh.payload_kg + s["payload_kg"],
                          accel_scale=s["accel"], grade_scale=1.0 + s["grade"], aux_scale=s["aux"])  # (N, Tc)
    v = cyc["v"]
    cyc_km = float(np.sum(v) * dt / 1000.0)
    reps = int(math.ceil(veh.target_range_km / max(cyc_km, 1e-6)))
    steps_needed = int(np.searchsorted(np.cumsum(np.tile(v, reps)) * dt / 1000.0, veh.target_range_km)) + 1
    Tc = demand.shape[1]
    I_ctrl = veh.max_current_a  # controller limits battery current; excess demand => performance deficit

    alive = np.ones(N, bool)
    fail_t = np.full(N, np.nan)
    fail_mode = np.array([""] * N, dtype=object)
    fail_state = [None] * N
    min_margin = np.full(N, np.inf)
    deficit_s = np.zeros(N)
    peak_T = T.copy()
    v_cut = chem.v_cut_op
    trace = {k: [] for k in ("t", "x_km", "v_kmh", "elev_m", "P_w", "I", "V", "Vcell_min", "soc", "T", "deficit")}
    x = elev = 0.0
    for k in range(steps_needed):
        j = k % Tc
        Pd = demand[:, j]
        ocvz = np.interp(z, soc_pts, ocv_pts)
        rf = np.exp(B * (1.0 / (T + 273.15) - 1.0 / 298.15))[:, None] * (1.0 + 0.8 * np.clip((0.15 - z) / 0.15, 0, 1))
        r0 = R0 * rf
        Vx = (ocvz - V1 - V2).sum(axis=1)
        Rt = r0.sum(axis=1)
        disc = Vx * Vx - 4.0 * Rt * Pd
        feas = disc >= 0.0
        I = np.where(feas, (Vx - np.sqrt(np.maximum(disc, 0.0))) / (2.0 * Rt), Vx / (2.0 * Rt))
        short = (~feas) | (I > I_ctrl)
        I = np.minimum(I, I_ctrl)
        Vc = ocvz - I[:, None] * r0 - V1 - V2
        vmin_cell = Vc.min(axis=1)
        vpack = Vc.sum(axis=1)
        deficit_s += short & alive
        checks = (
            ("cell under-voltage", vmin_cell < v_cut),
            ("controller cut-off (pack V)", vpack < veh.bus_v_min),
            ("BMS over-current", I > i_max),
            ("over-temperature", T > chem.t_max_op),
            ("SOC depleted", z.min(axis=1) < 0.0),
        )
        margin = np.minimum.reduce([(vmin_cell - v_cut) / 0.5, (vpack - veh.bus_v_min) / 8.0,
                                    (i_max - I) / i_max, (chem.t_max_op - T) / 20.0])
        min_margin = np.minimum(min_margin, np.where(alive, margin, np.inf))
        for mode, bad in checks:
            newly = alive & bad
            if newly.any():
                idx = np.where(newly)[0]
                fail_t[idx] = k * dt
                fail_mode[idx] = mode
                for i in idx[:50]:
                    fail_state[i] = {"t_s": k * dt, "x_km": x, "P_w": float(Pd[i]), "I": float(I[i]),
                                     "V_pack": float(vpack[i]), "Vcell_min": float(vmin_cell[i]),
                                     "weakest_cell": int(np.argmin(Vc[i])) + 1,
                                     "soc": float(z[i].mean()), "T": float(T[i]),
                                     "grade_pct": float(cyc["grade_pct"][j]), "v_kmh": float(v[j] * 3.6)}
                alive &= ~bad
        if nominal and k % trace_every == 0:
            for key, val in (("t", k * dt), ("x_km", x), ("v_kmh", float(v[j] * 3.6)), ("elev_m", elev),
                             ("P_w", float(Pd[0])), ("I", float(I[0])), ("V", float(vpack[0])),
                             ("Vcell_min", float(vmin_cell[0])), ("soc", float(z[0].mean())), ("T", float(T[0])),
                             ("deficit", bool(short[0]))):
                trace[key].append(val)
        if not alive.any() and not (nominal and k * dt <= np.nanmin(fail_t) + 30):
            break
        Ic = np.where(alive, I, 0.0)[:, None]
        q_gen = (Ic * Ic * r0 + Ic * V1 + Ic * V2).sum(axis=1)
        V1 = a1 * V1 + b1 * Ic
        V2 = a2 * V2 + b2 * Ic
        z = z - Ic * dt / (3600.0 * Q)
        T = T + dt * (q_gen - hA * (T - T_amb)) / C_th
        peak_T = np.maximum(peak_T, T)
        x += v[j] * dt / 1000.0
        elev += v[j] * dt * math.sin(math.atan(cyc["grade_pct"][j] / 100.0))
    low = alive & (z.mean(axis=1) < veh.reserve_soc)
    fail_mode[low] = "reserve SOC not met"
    fail_t[low] = steps_needed * dt
    alive &= ~low

    k_ok = int(alive.sum())
    out = {"N": N, "successes": k_ok, "p_success": k_ok / N, "ci95": wilson(k_ok, N),
           "mission_km": veh.target_range_km, "mission_s": steps_needed * dt,
           "deficit_s_median": float(np.median(deficit_s)), "peak_T_p95": float(np.percentile(peak_T, 95)),
           "final_soc_median": float(np.median(z.mean(axis=1)))}
    modes = {}
    for m in fail_mode:
        if m:
            modes[m] = modes.get(m, 0) + 1
    out["failure_modes"] = dict(sorted(modes.items(), key=lambda kv: -kv[1]))
    if not nominal and N > 10:
        sens = {}
        mm = np.where(np.isfinite(min_margin), min_margin, 5.0)
        for key, vals in s.items():
            if np.std(vals) > 0 and np.std(mm) > 0:
                rho = spearmanr(vals, mm).statistic
                sens[key] = 0.0 if np.isnan(rho) else float(rho)
        tot = sum(r * r for r in sens.values()) or 1.0
        out["sensitivity"] = sorted(
            [{"param": k, "label": unc[k]["label"], "rho": r, "share": r * r / tot} for k, r in sens.items()],
            key=lambda d: -d["share"])
        worst = int(np.argmin(mm))
        out["worst_run"] = {"min_margin": float(mm[worst]), "fail_mode": fail_mode[worst] or None,
                            "state": fail_state[worst], "inputs": {k: float(vv[worst]) for k, vv in s.items()}}
        out["first_failures"] = [fs for fs in fail_state if fs][:20]
    if nominal:
        out["trace"] = trace
        out["failure"] = fail_state[0] if fail_mode[0] else None
        out["fail_mode"] = fail_mode[0] or None
    return out


def run_twin(pack: ECMPack, st: PackState, veh: Vehicle, cyc: dict, gate: dict, i_max: float, N=400,
             start_soc: float | None = 1.0, uncertainty=None, battery_mass_kg=0.0, seed=0):
    """start_soc=1.0 -> compatibility question (full charge); None -> 'ready now' from current SOC."""
    n = len(pack.Q)
    soc0 = np.full(n, start_soc) if start_soc is not None else st.z
    # long missions: 2 s step (exact ZOH for the RC branches keeps this accurate for tau >= 10 s)
    if veh.target_range_km / max(float(np.mean(cyc["v"])) * 3.6, 1e-3) * 3600 > 8000:
        m = len(cyc["v"]) // 2 * 2
        cyc = {**cyc, "dt": 2.0, "v": cyc["v"][:m].reshape(-1, 2).mean(1),
               "grade_pct": cyc["grade_pct"][:m].reshape(-1, 2).mean(1)}
    # per-cell SOC offsets are preserved when starting from full (top-balanced assumption)
    nom = simulate(pack, soc0, veh, cyc, nominal=True, i_max=i_max, uncertainty=uncertainty,
                   battery_mass_kg=battery_mass_kg)
    mc = simulate(pack, soc0, veh, cyc, N=N, seed=seed, i_max=i_max, uncertainty=uncertainty,
                  battery_mass_kg=battery_mass_kg)
    G = 0.0 if gate["verdict"] == "FAIL" else 1.0
    return {"algorithm": "C", "name": "Probabilistic electro-thermal digital twin",
            "score": 100.0 * G * mc["p_success"], "provisional": gate["verdict"] == "UNKNOWN",
            "gate_multiplier": G, "monte_carlo": mc, "nominal": nom,
            "start": "full charge" if start_soc is not None else "current SOC",
            "uncertainty": uncertainty or DEFAULT_UNCERTAINTY,
            "meaning": f"{mc['successes']} of {mc['N']} declared scenarios completed the mission without "
                       f"violating any declared constraint."}
