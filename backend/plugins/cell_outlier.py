"""Cell outlier detector (rule-based, no training).

Robust z-score of each cell against the pack: z = 0.6745 (v - median) / MAD
(Iglewicz & Hoaglin modified z-score; |z| > 3.5 is their outlier threshold).
Combined with the twin's per-cell residual bias, which separates a cell that
merely sits at a different SOC (bias at rest) from one with higher resistance
(bias that grows under load).
"""
import numpy as np

NAME = "Cell outlier detector"
DESCRIPTION = "Modified z-score of cell voltages plus twin residual bias per cell"
KIND = "detector"


def predict(snapshot):
    el = snapshot["battery"]["electrical"]
    cells = np.asarray(el["cells"], float)
    if len(cells) < 3:
        return {"label": "needs >= 3 cells", "value": None}
    med = np.median(cells)
    mad = np.median(np.abs(cells - med))
    mad = max(mad, 0.0005)  # 0.5 mV floor: BMS resolution is ~1 mV
    z = 0.6745 * (cells - med) / mad
    flagged = [int(i) + 1 for i in np.where(np.abs(z) > 3.5)[0]]
    bias = (snapshot.get("twin") or {}).get("cell_bias_mV")
    loaded = abs(el["current"]) > 2
    return {
        "label": "no outliers" if not flagged else f"cells {flagged} deviate",
        "value": len(flagged),
        "confidence": None,
        "detail": {"z": [round(float(x), 2) for x in z], "flagged": flagged, "under_load": loaded,
                   "twin_bias_mV": [round(b, 2) for b in bias] if bias else None,
                   "hint": "deviation under load -> resistance; at rest -> SOC/capacity imbalance"},
    }
