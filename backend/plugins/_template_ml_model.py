"""TEMPLATE (not loaded: the leading underscore disables it). Copy to e.g. soh_model.py.

Train offline on sessions recorded from the Data tab (backend/data/sessions/*.csv +
their .json sidecars), save the model, and load it here once at import time.
Keep the feature code identical between training and inference; import it from
one shared module if you can, so the two cannot drift apart.
"""
from pathlib import Path

import numpy as np

NAME = "My SOH model"
DESCRIPTION = "Predicts capacity SOH from live features"
KIND = "regressor"

MODEL_PATH = Path(__file__).with_suffix(".joblib")
# import joblib; MODEL = joblib.load(MODEL_PATH)     # sklearn / xgboost
# import onnxruntime as ort; SESSION = ort.InferenceSession(str(MODEL_PATH.with_suffix('.onnx')))
MODEL = None


def features(snapshot):
    b = snapshot["battery"]
    el, th = b["electrical"], b["thermal"]
    dcir = el["dcir"]["value"] if el["dcir"] else np.nan
    return np.array([[dcir, el["dv_mv"], th["t_max"] or np.nan, b["state"]["soc"]]], float)


def predict(snapshot):
    x = features(snapshot)
    if MODEL is None:
        return {"label": "template: no model loaded", "value": None}
    y = float(MODEL.predict(x)[0])
    return {"label": f"SOH {y * 100:.1f} %", "value": y, "confidence": None}
