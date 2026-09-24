"""BMS SOC drift check for LFP from rested OCV.

LFP's OCV curve is flat between ~20 % and ~85 % SOC, so voltage says little
about SOC there. It is only identifiable after a long rest AND in the steep
regions (< 3.22 V or > 3.35 V per cell). When both hold, compare the OCV-derived
SOC with the BMS's coulomb-counted SOC. Otherwise say it is not identifiable,
rather than guessing. Uses the workbench's generic LFP OCV table (an assumption
until you measure your own cells' OCV).
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bdt.model.chem import LFP  # noqa: E402

NAME = "LFP rest-SOC check"
DESCRIPTION = "OCV-based SOC after >= 20 min rest, compared with the BMS SOC"
KIND = "regressor"
REST_S = 1200


def predict(snapshot):
    hist = snapshot.get("history") or []
    if not hist:
        return {"label": "no history yet", "value": None}
    t_end = hist[-1]["t"]
    rest_since = t_end
    for h in reversed(hist):
        if abs(h["I"]) > 0.5:
            break
        rest_since = h["t"]
    rested = t_end - rest_since
    bat = snapshot["battery"]
    v = float(np.mean(bat["electrical"]["cells"]))
    soc_bms = bat["state"]["soc"]
    if rested < REST_S:
        return {"label": f"resting {rested / 60:.0f} of {REST_S / 60:.0f} min", "value": None,
                "detail": {"rested_s": rested, "cell_mean_V": v}}
    if 3.22 <= v <= 3.35:
        return {"label": "on the LFP plateau: SOC not identifiable from voltage", "value": None,
                "detail": {"rested_s": rested, "cell_mean_V": v}}
    z = float(LFP.soc_from_ocv(v))
    drift = soc_bms - z
    return {"label": f"OCV SOC {z * 100:.0f} % vs BMS {soc_bms * 100:.0f} % (drift {drift * 100:+.0f} pts)",
            "value": z, "confidence": 0.6,
            "detail": {"rested_s": rested, "cell_mean_V": v, "soc_ocv": z, "soc_bms": soc_bms, "drift": drift,
                       "caveat": "generic LFP OCV table; measure your own for real use"}}
