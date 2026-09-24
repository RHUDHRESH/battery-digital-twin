"""Vehicle profiles, drive cycles, and longitudinal power demand.

    F(t) = m_eff a + m g Crr cos(th) + 0.5 rho CdA v^2 + m g sin(th)
    P_bat = F v / eta_drive + P_aux        (traction)
    P_bat = max(F v eta_regen, -P_regen) + P_aux   (braking)

Preset vehicle numbers are ILLUSTRATIVE engineering defaults for vehicles of
that class, not manufacturer data. Edit them to your real vehicle.
Drive cycles here are SYNTHETIC (generated, deterministic). Load a measured
cycle (CSV: t, speed_kmh[, grade_pct]) for real work.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field

import numpy as np

G, RHO = 9.81, 1.2


@dataclass
class Vehicle:
    id: str
    name: str
    kind: str = "custom"           # rickshaw | cart | scooter | car | custom (drives the 3D model)
    mass_kg: float = 600.0          # kerb mass WITHOUT battery
    payload_kg: float = 250.0
    CdA: float = 0.9               # m^2
    Crr: float = 0.015
    eta_drive: float = 0.82
    eta_regen: float = 0.55
    regen_max_kw: float = 2.0
    aux_w: float = 80.0
    bus_v_min: float = 42.0         # controller under-voltage cut-off (V)
    bus_v_max: float = 60.0         # controller over-voltage limit (V)
    peak_power_kw: float = 4.0      # motor/controller peak (electrical input)
    cont_power_kw: float = 2.0
    max_current_a: float = 100.0    # controller peak battery current
    target_range_km: float = 60.0
    reserve_soc: float = 0.10
    max_grade_pct: float = 8.0
    top_speed_kmh: float = 50.0
    accel_max: float = 1.2          # m/s^2 the vehicle can actually deliver
    ambient_c: float = 32.0
    chemistry_allowed: list = field(default_factory=lambda: ["LFP", "NMC"])
    connector: str = ""             # optional; empty = not constrained
    protocol: str = ""              # optional BMS/CAN protocol requirement
    battery_mass_allow_kg: float = 0.0   # 0 = not constrained
    cycle: str = "urban"
    color: str = "#1f6feb"
    notes: str = ""

    def to_dict(self):
        return asdict(self)


PRESETS = [
    Vehicle("erick-48", "E-Rickshaw 48 V", "rickshaw", mass_kg=330, payload_kg=320, CdA=1.1, Crr=0.018,
            eta_drive=0.78, eta_regen=0.0, regen_max_kw=0.0, aux_w=40, bus_v_min=40, bus_v_max=60,
            peak_power_kw=2.2, cont_power_kw=1.2, max_current_a=60, target_range_km=70, cycle="urban",
            top_speed_kmh=25, accel_max=0.5,
            color="#16a34a", notes="Typical 48 V hub/BLDC rickshaw class; values illustrative."),
    Vehicle("golf-48", "Golf / Campus Cart 48 V", "cart", mass_kg=420, payload_kg=240, CdA=1.3, Crr=0.02,
            eta_drive=0.80, eta_regen=0.5, regen_max_kw=2.5, aux_w=60, bus_v_min=42, bus_v_max=60,
            peak_power_kw=7.5, cont_power_kw=4.0, max_current_a=150, target_range_km=50, cycle="hilly",
            top_speed_kmh=25, accel_max=1.0,
            color="#1f6feb", notes="Hilly campus duty stresses the power margin."),
    Vehicle("scoot-48", "E-Scooter 48 V", "scooter", mass_kg=95, payload_kg=90, CdA=0.55, Crr=0.012,
            eta_drive=0.84, eta_regen=0.5, regen_max_kw=1.0, aux_w=25, bus_v_min=40, bus_v_max=60,
            peak_power_kw=3.0, cont_power_kw=1.5, max_current_a=70, target_range_km=90, cycle="urban",
            top_speed_kmh=45, accel_max=1.5,
            color="#e11d48"),
    Vehicle("trolley-12", "Utility trolley 12 V", "cart", mass_kg=45, payload_kg=120, CdA=0.5, Crr=0.02,
            eta_drive=0.75, eta_regen=0.0, regen_max_kw=0.0, aux_w=5, bus_v_min=10.5, bus_v_max=15.0,
            peak_power_kw=0.6, cont_power_kw=0.3, max_current_a=60, target_range_km=20, cycle="urban",
            top_speed_kmh=8, accel_max=0.4, color="#0ea5a4",
            notes="Illustrative 12 V application for a 4s LFP battery."),
    Vehicle("mobility-24", "Mobility scooter 24 V", "scooter", mass_kg=70, payload_kg=100, CdA=0.6, Crr=0.015,
            eta_drive=0.78, eta_regen=0.3, regen_max_kw=0.2, aux_w=10, bus_v_min=21.0, bus_v_max=30.0,
            peak_power_kw=1.0, cont_power_kw=0.5, max_current_a=50, target_range_km=35, cycle="urban",
            top_speed_kmh=12, accel_max=0.6, color="#e08a00",
            notes="Illustrative 24 V application for an 8s LFP battery."),
    Vehicle("ev-350", "Compact EV 350 V (gate demo)", "car", mass_kg=1200, payload_kg=220, CdA=0.68, Crr=0.010,
            eta_drive=0.88, eta_regen=0.65, regen_max_kw=40, aux_w=600, bus_v_min=250, bus_v_max=420,
            peak_power_kw=92, cont_power_kw=45, max_current_a=320, target_range_km=180, cycle="highway",
            top_speed_kmh=130, accel_max=2.5,
            color="#7c3aed", notes="Deliberately incompatible with a 48 V module: exercises the gate."),
]


# ------------------------------------------------------------------ drive cycles
def _trip(v_kmh, accel, cruise_s, decel, stop_s, grade=0.0):
    """One micro-trip: accel (m/s^2) up to v, cruise, brake, stop."""
    v = v_kmh / 3.6
    seg = []
    t_acc = max(1, int(round(v / accel)))
    seg += [(accel * (k + 1), grade) for k in range(t_acc)]
    seg += [(v, grade)] * int(cruise_s)
    t_dec = max(1, int(round(v / decel)))
    seg += [(max(v - decel * (k + 1), 0.0), grade) for k in range(t_dec)]
    seg += [(0.0, grade)] * int(stop_s)
    return seg


def make_cycle(name: str, v_scale: float = 1.0):
    rng = np.random.default_rng({"urban": 1, "hilly": 2, "highway": 3}.get(name, 9))
    seg = []
    if name == "urban":
        for _ in range(14):
            seg += _trip(rng.uniform(18, 32) * v_scale, rng.uniform(0.9, 1.4), rng.uniform(15, 45),
                         rng.uniform(1.0, 1.6), rng.uniform(8, 25), rng.uniform(-1.0, 1.5))
    elif name == "hilly":
        for k in range(10):
            g = [8.0, -6.0, 3.0, 0.0, 10.0][k % 5]
            seg += _trip(rng.uniform(18, 26) * v_scale, rng.uniform(0.9, 1.3), rng.uniform(40, 70),
                         rng.uniform(1.0, 1.5), rng.uniform(5, 15), g)
    elif name == "highway":
        seg += _trip(90 * v_scale, 1.6, 600, 1.2, 20, 0.0)
        seg += _trip(110 * v_scale, 1.4, 400, 1.2, 30, 2.0)
    else:
        raise KeyError(name)
    v = np.array([s[0] for s in seg])
    grade = np.array([s[1] for s in seg])
    # a repeated duty cycle must be a closed loop: zero net elevation (distance-weighted mean grade = 0)
    grade = grade - np.sum(grade * v) / max(np.sum(v), 1e-9)
    return {"name": f"{name} (synthetic)", "dt": 1.0, "v": v, "grade_pct": grade}


CYCLES = ("urban", "hilly", "highway")


def adapt_cycle(cyc: dict, veh: "Vehicle") -> dict:
    """Clip the reference cycle to what this vehicle can physically follow:
    speed <= top speed, forward-pass slew limit on acceleration (braking untouched)."""
    v = np.minimum(cyc["v"], veh.top_speed_kmh / 3.6).copy()
    dt = cyc["dt"]
    for k in range(1, len(v)):
        v[k] = min(v[k], v[k - 1] + veh.accel_max * dt)
    return {**cyc, "v": v, "name": cyc["name"] + f" @ {veh.name}"}


def cycle_from_csv(t, v_kmh, grade_pct=None, name="uploaded"):
    t = np.asarray(t, float)
    tt = np.arange(0, t[-1], 1.0)
    v = np.interp(tt, t, np.asarray(v_kmh, float)) / 3.6
    g = np.interp(tt, t, np.asarray(grade_pct, float)) if grade_pct is not None else np.zeros_like(tt)
    return {"name": name, "dt": 1.0, "v": v, "grade_pct": g}


def power_demand(veh: Vehicle, cyc: dict, battery_mass_kg: float = 0.0, payload_kg=None, accel_scale=1.0,
                 grade_scale=1.0, aux_scale=1.0):
    """Battery-terminal power (W, + = discharge) per second of the cycle. Broadcasts over run arrays."""
    v = cyc["v"]
    a = np.gradient(v, cyc["dt"])
    th = np.arctan(np.asarray(grade_scale)[..., None] * cyc["grade_pct"] / 100.0) if np.ndim(grade_scale) else \
        np.arctan(grade_scale * cyc["grade_pct"] / 100.0)
    pay = veh.payload_kg if payload_kg is None else payload_kg
    m = veh.mass_kg + battery_mass_kg + np.asarray(pay, float)
    m = m[..., None] if np.ndim(m) else m
    acc = np.asarray(accel_scale, float)
    acc = acc[..., None] if np.ndim(acc) else acc
    F = 1.05 * m * a * acc + m * G * veh.Crr * np.cos(th) + 0.5 * RHO * veh.CdA * v ** 2 + m * G * np.sin(th)
    Pw = F * v
    peak = veh.peak_power_kw * 1000.0
    trac = np.minimum(Pw / veh.eta_drive, peak)
    regen = np.maximum(Pw * veh.eta_regen, -veh.regen_max_kw * 1000.0)
    aux = veh.aux_w * (np.asarray(aux_scale, float)[..., None] if np.ndim(aux_scale) else aux_scale)
    return np.where(Pw >= 0, trac, regen) + aux


def rolling_max_mean(p, window):
    if len(p) < window:
        return float(np.max(p))
    c = np.cumsum(np.insert(p, 0, 0.0))
    return float(np.max((c[window:] - c[:-window]) / window))


def mission_requirements(veh: Vehicle, cyc: dict, battery_mass_kg=0.0):
    P = power_demand(veh, cyc, battery_mass_kg)
    dist_km = float(np.sum(cyc["v"]) * cyc["dt"] / 1000.0)
    e_cycle_wh = float(np.sum(P) * cyc["dt"] / 3600.0)
    reps = veh.target_range_km / max(dist_km, 1e-6)
    return {
        "cycle": cyc["name"], "cycle_km": dist_km, "cycle_s": len(P) * cyc["dt"],
        "wh_per_km": e_cycle_wh / max(dist_km, 1e-6),
        "E_mission_wh": e_cycle_wh * reps,
        "P_peak_w": float(np.max(P)), "P_10s_w": rolling_max_mean(P, 10), "P_30s_w": rolling_max_mean(P, 30),
        "P_rms_w": float(np.sqrt(np.mean(P ** 2))), "P_mean_w": float(np.mean(P)),
        "P_regen_w": float(-np.min(P)),
    }
