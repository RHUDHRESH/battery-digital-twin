"""Three-state qualification gate shared by all algorithms.

G_i in {PASS, FAIL, UNKNOWN}. FAIL => incompatible. UNKNOWN => test required.
Only an all-PASS (or N/A) gate yields a final verdict; with UNKNOWNs the
algorithms still run but their score is marked PROVISIONAL.
"""
from __future__ import annotations

import time

from ..model.chem import CHEMISTRIES
from ..model.vehicle import Vehicle

PASS, FAIL, UNKNOWN, NA = "PASS", "FAIL", "UNKNOWN", "N/A"


def run_gate(card: dict, veh: Vehicle, now: float | None = None, max_age_s: float = 10.0):
    idt, el, th = card["identity"], card["electrical"], card["thermal"]
    chem = CHEMISTRIES.get(idt["chemistry"])
    checks = []

    def add(key, name, status, evidence):
        checks.append({"key": key, "name": name, "status": status, "evidence": evidence})

    # chemistry
    add("chemistry", "Chemistry", PASS if idt["chemistry"] in veh.chemistry_allowed else FAIL,
        f"{idt['chemistry']} vs allowed {veh.chemistry_allowed}")
    # voltage window
    vmax, vnom, vmin = idt["v_max"], idt["nominal_V"], idt["series"] * chem.v_cut_op
    if vmax > veh.bus_v_max:
        add("voltage", "Pack voltage window", FAIL, f"pack max {vmax:.1f} V > bus max {veh.bus_v_max:.0f} V")
    elif vnom < veh.bus_v_min:
        add("voltage", "Pack voltage window", FAIL, f"pack nominal {vnom:.1f} V < bus min {veh.bus_v_min:.0f} V")
    else:
        add("voltage", "Pack voltage window", PASS,
            f"pack {vmin:.1f}-{vmax:.1f} V inside bus {veh.bus_v_min:.0f}-{veh.bus_v_max:.0f} V "
            f"(usable window above bus min: {max(0, vnom - veh.bus_v_min):.1f} V at nominal)")
    # current limit
    imax = idt.get("i_max_dis")
    if not imax:
        add("current", "BMS discharge current limit", UNKNOWN, "BMS limit not entered on Battery card")
    else:
        add("current", "BMS discharge current limit", PASS if imax >= veh.max_current_a else FAIL,
            f"BMS {imax:.0f} A vs controller peak {veh.max_current_a:.0f} A")
    # connector / protocol / mechanical (only constrained if both sides declare)
    for key, name, bval, vval in (("connector", "Connector", idt.get("connector"), veh.connector),
                                  ("protocol", "BMS / CAN protocol", idt.get("protocol"), veh.protocol)):
        if not vval:
            add(key, name, NA, "vehicle does not constrain this")
        elif not bval:
            add(key, name, UNKNOWN, f"vehicle requires '{vval}', battery unspecified")
        else:
            add(key, name, PASS if bval.lower() == vval.lower() else FAIL, f"battery '{bval}' vs vehicle '{vval}'")
    if veh.battery_mass_allow_kg:
        m = idt.get("mass_kg") or 0
        add("mechanical", "Mass allowance", UNKNOWN if not m else (PASS if m <= veh.battery_mass_allow_kg else FAIL),
            f"battery {m or '?'} kg vs allowance {veh.battery_mass_allow_kg} kg")
    else:
        add("mechanical", "Mechanical fit", NA, "vehicle does not constrain this")
    # telemetry integrity (the BAT-144 lesson: plausible-looking but inconsistent data)
    cells = el["cells"]
    age = (now or time.time()) - card["evidence"]["t"] if card["evidence"]["source"] in ("rs485",) else 0.0
    issues = []
    if age > max_age_s:
        issues.append(f"stale data ({age:.0f} s old)")
    if any(c < 1.5 or c > 4.5 for c in cells):
        issues.append("cell voltage physically implausible")
    temps = th["temps"] or []
    if any(t < -40 or t > 100 for t in temps):
        issues.append("temperature implausible")
    add("telemetry", "Telemetry integrity", FAIL if issues else PASS,
        "; ".join(issues) or f"{len(cells)} cells, {len(temps)} temp sensors, fresh, physically plausible")
    # cell faults
    bad = [i + 1 for i, c in enumerate(cells) if c < chem.v_min or c > chem.v_max]
    faults = card["evidence"]["faults"]
    if bad or faults:
        add("cells", "Cell / BMS faults", FAIL, f"out-of-window cells {bad}; BMS faults {faults}")
    else:
        add("cells", "Cell / BMS faults", PASS, f"all cells in {chem.v_min}-{chem.v_max} V, no BMS fault flags")
    # temperature
    tmax = th["t_max"]
    lo, hi = chem.t_dis
    if tmax is None:
        add("temperature", "Temperature window", UNKNOWN, "no temperature sensor data")
    else:
        add("temperature", "Temperature window", PASS if lo <= tmax <= hi else FAIL,
            f"T_max {tmax:.1f} C in discharge window {lo}..{hi} C")

    statuses = [c["status"] for c in checks]
    verdict = FAIL if FAIL in statuses else (UNKNOWN if UNKNOWN in statuses else PASS)
    return {"verdict": verdict, "checks": checks,
            "meaning": {"PASS": "eligible for scoring", "FAIL": "INCOMPATIBLE",
                        "UNKNOWN": "TEST REQUIRED (score provisional)"}[verdict]}
