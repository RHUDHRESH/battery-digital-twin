"""Inference plugin host.

Drop a .py file into backend/plugins/. It is loaded on start or on "Reload" and
called about once a second with a snapshot of the live battery. Contract:

    NAME = "Human readable name"
    DESCRIPTION = "What it infers and from what"
    KIND = "classifier" | "regressor" | "detector" | "rule"   (informational)

    def predict(snapshot: dict) -> dict:
        # snapshot = {"battery": <battery card>, "twin": <fidelity>, "history": [<rows, last 10 min>]}
        return {"value": ..., "label": "...", "confidence": 0..1, "detail": {...}}

A plugin may load its own trained model (joblib / torch / onnx) at import time.
Exceptions are caught and shown in the UI; a failing plugin never stops the stream.
"""
from __future__ import annotations

import importlib.util
import time
import traceback
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins"
PLUGIN_DIR.mkdir(exist_ok=True)


class PluginHost:
    def __init__(self):
        self.plugins: dict[str, dict] = {}
        self.results: dict[str, dict] = {}
        self.enabled: dict[str, bool] = {}
        self.load()

    def load(self):
        self.plugins.clear()
        for p in sorted(PLUGIN_DIR.glob("*.py")):
            if p.name.startswith("_"):
                continue
            entry = {"file": p.name, "name": p.stem, "description": "", "kind": "", "error": None, "mod": None}
            try:
                spec = importlib.util.spec_from_file_location(f"bdt_plugin_{p.stem}", p)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if not hasattr(mod, "predict"):
                    raise AttributeError("plugin has no predict(snapshot) function")
                entry.update(name=getattr(mod, "NAME", p.stem), description=getattr(mod, "DESCRIPTION", ""),
                             kind=getattr(mod, "KIND", ""), mod=mod)
            except Exception as e:
                entry["error"] = f"{type(e).__name__}: {e}"
            self.plugins[p.stem] = entry
            self.enabled.setdefault(p.stem, True)
        return self.summary()

    def run(self, snapshot: dict):
        for key, pl in self.plugins.items():
            if pl["mod"] is None or not self.enabled.get(key, True):
                continue
            t0 = time.perf_counter()
            try:
                out = pl["mod"].predict(snapshot)
                self.results[key] = {"ok": True, "out": out, "ms": (time.perf_counter() - t0) * 1000, "t": time.time()}
            except Exception as e:
                self.results[key] = {"ok": False, "error": f"{type(e).__name__}: {e}",
                                     "trace": traceback.format_exc(limit=3), "t": time.time()}

    def summary(self):
        return [{"key": k, "file": p["file"], "name": p["name"], "description": p["description"], "kind": p["kind"],
                 "load_error": p["error"], "enabled": self.enabled.get(k, True), "result": self.results.get(k)}
                for k, p in self.plugins.items()]
