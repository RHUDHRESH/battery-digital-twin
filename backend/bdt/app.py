"""Battery R&D Workbench - API server.

Run:  backend/.venv/Scripts/python -m uvicorn bdt.app:app --app-dir backend --port 8000
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from fastapi.responses import FileResponse

from .data.ingest import parse_table
from .data.logger import SESS_DIR, SessionLogger, list_sessions
from .model.livetwin import LiveTwin, forecast
from .plugins import PluginHost
from .engines.battery import build_battery_card
from .engines.cluster import heating_rate_1c, load_fleet, run_clustering, save_fleet, synthetic_population
from .engines.gate import run_gate
from .engines.safefit import run_safefit
from .engines.twin import DEFAULT_UNCERTAINTY, run_twin
from .hw.link import AUDIT, REC_DIR, DATA_DIR, Link, ReplayLink, SerialLink, VirtualLink, find_port, list_ports, sniff
from .hw.protocols import DalyCodec, JbdCodec, ModbusCodec, make_codec
from .model.chem import LFP
from .model.estimator import Estimator
from .model.vehicle import CYCLES, PRESETS, Vehicle, adapt_cycle, cycle_from_csv, make_cycle, mission_requirements

VEH_FILE = DATA_DIR / "vehicles.json"
PROFILE_FILE = DATA_DIR / "link_profile.json"   # remembered adapter + protocol + nameplate (auto-reconnect)


def _local_only(request: Request):
    """BMS writes are allowed only from this PC, even when the UI is shared on the LAN (there is no login)."""
    host = request.client.host if request.client else ""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:  # via the UI proxy: the LAST entry is appended by the proxy itself and cannot be forged
        host = fwd.split(",")[-1].strip()
    if host.startswith("::ffff:"):
        host = host[7:]
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(403, "BMS writes are only allowed from the PC the battery is plugged into")


def _clean(o):
    """JSON-safe conversion of numpy scalars/arrays and NaN/inf."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.ndarray):
        return _clean(o.tolist())
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return f if np.isfinite(f) else None
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


class Workbench:
    def __init__(self):
        self.link: Link | None = None
        self.est = Estimator(LFP)
        self.meta = {"label": "Demo LFP 16s 100Ah", "nominal_Ah": 100.0, "i_max_dis": 150.0, "i_max_chg": 50.0,
                     "mass_kg": 42.0, "connector": "", "protocol": "", "ambient_c": 25.0}
        self.card: dict | None = None
        self.cycles = {c: make_cycle(c) for c in CYCLES}
        self.vehicles = self._load_vehicles()
        self.lock = threading.Lock()
        self.writes_armed = False
        self.last_card_t = 0.0
        self.twin = LiveTwin()
        self.logger: SessionLogger | None = None
        self.plugins = PluginHost()
        self.last_plugin_t = 0.0

    # ------------------------------------------------------------------ vehicles
    def _load_vehicles(self):
        vs = {v.id: v for v in PRESETS}
        if VEH_FILE.exists():
            for d in json.loads(VEH_FILE.read_text(encoding="utf-8")):
                vs[d["id"]] = Vehicle(**d)
        return vs

    def save_vehicle(self, d: dict):
        v = Vehicle(**d)
        self.vehicles[v.id] = v
        custom = [x.to_dict() for x in self.vehicles.values() if x.id not in {p.id for p in PRESETS} or x != next(
            (p for p in PRESETS if p.id == x.id), None)]
        VEH_FILE.write_text(json.dumps(custom, indent=1), encoding="utf-8")
        return v

    # ------------------------------------------------------------------ link
    def attach(self, link: Link):
        if self.link:
            self.link.stop()
        self.est = Estimator(LFP, nominal_Ah=self.meta["nominal_Ah"], i_max_dis=self.meta.get("i_max_dis"),
                             i_max_chg=self.meta.get("i_max_chg"))
        self.card = None
        old = self.twin
        self.twin = LiveTwin()
        self.twin.ocv_corr, self.twin.ocv_n = old.ocv_corr.copy(), old.ocv_n.copy()  # keep learned OCV calibration
        link.on_telemetry = self._on_tel
        self.link = link
        link.start()

    def _on_tel(self, tel):
        if not tel.cells or any(c <= 0 for c in tel.cells):
            return  # wait until all cell frames arrived
        # adopt the BMS's design capacity as the nameplate unless the user entered one
        if tel.design_Ah and not self.meta.get("nominal_user") and abs(self.meta.get("nominal_Ah", 0) - tel.design_Ah) > 0.01:
            self.meta["nominal_Ah"] = tel.design_Ah
            self.est.nominal_Ah = tel.design_Ah
        t = tel.truth["sim_time_s"] if tel.truth else tel.t
        if self.est.last_t is not None and t <= self.est.last_t:
            return
        with self.lock:
            self.est.update(t, tel.current, tel.cells, tel.temps, tel.soc_bms, tel.residual_Ah, tel.full_Ah)
            try:
                row = self.twin.update(self.est, t, tel.current, tel.cells, tel.temps, tel.soc_bms)
            except Exception as e:  # the twin must never break acquisition
                row = None
                self.twin_error = str(e)
            if self.logger:
                self.logger.write(t, tel.current, list(tel.cells), list(tel.temps), tel.soc_bms, row)

    def refresh_card(self):
        if not self.link or not self.est.buf:
            return self.card
        with self.lock:
            try:
                self.card = build_battery_card(self.est, self.link.tel, self.meta)
            except Exception as e:  # never kill the stream on an estimator edge case
                self.card = {"error": str(e)}
        if self.card and "error" not in self.card and time.time() - self.last_plugin_t >= 1.0:
            self.last_plugin_t = time.time()
            self.plugins.run({"battery": _clean(self.card), "twin": self.twin.fidelity(),
                              "history": self.twin.history(900, 900)})
        return self.card

    # ------------------------------------------------------------------ evaluation
    def fleet_features(self):
        c = self.card
        if not c or "error" in c:
            return None
        d = c["electrical"]["dcir"]
        if not d:
            return None
        q = c["degradation"]["capacity"]["value"]
        return {"capacity_Ah": q, "dcir_mohm": d["value"] * 1000,
                "dTdt": heating_rate_1c(c["identity"]["nominal_Ah"], d["value"]),
                "dv_mv": c["electrical"]["rest_dv_mv"] if c["electrical"]["rest_dv_mv"] is not None
                else c["electrical"]["dv_mv"], "series": c["identity"]["series"]}

    def evaluate(self, vehicle_id: str, algorithms: list[str], runs: int = 300, start: str = "full",
                 uncertainty: dict | None = None, cycle: str | None = None):
        card = self.refresh_card()
        if not card or "error" in card:
            raise HTTPException(409, "no battery data yet - connect RS485, start demo, or load a file")
        veh = self.vehicles[vehicle_id]
        cyc = adapt_cycle(self.cycles[cycle or veh.cycle], veh)
        gate = run_gate(card, veh)
        with self.lock:
            pack, st, _ = self.est.build_pack()
        i_max = self.meta.get("i_max_dis") or 150.0
        out = {"vehicle": veh.to_dict(), "cycle": cyc["name"], "gate": gate, "battery": card, "results": {}}
        if "A" in algorithms:
            out["results"]["A"] = run_safefit(card, veh, cyc, gate, pack, st, i_max)
        if "C" in algorithms:
            out["results"]["C"] = run_twin(pack, st, veh, cyc, gate, i_max, N=runs,
                                           start_soc=1.0 if start == "full" else None, uncertainty=uncertainty,
                                           battery_mass_kg=self.meta.get("mass_kg") or 0.0)
        if "B" in algorithms:
            out["results"]["B"] = run_clustering(load_fleet(), self.fleet_features(), list(self.vehicles.values()),
                                                 self.cycles, veh, gate)
        scores = {k: r.get("score") for k, r in out["results"].items() if r.get("score") is not None}
        out["fusion"] = {
            "scores": scores,
            "conservative": min(scores.values()) if scores else None,
            "verdict": "INCOMPATIBLE" if gate["verdict"] == "FAIL" else (
                "TEST REQUIRED" if gate["verdict"] == "UNKNOWN" else "SCORED"),
            "rule": "Gate decides safety; scores rank compatibility; fused view reports the most conservative.",
        }
        return _clean(out)


wb = Workbench()
app = FastAPI(title="Battery R&D Workbench")


# ======================================================================== adapter memory + auto-reconnect
def load_profile():
    try:
        return json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_profile(p):
    PROFILE_FILE.write_text(json.dumps(p, indent=1), encoding="utf-8")


def _open_serial(params: dict, port: str):
    codec = make_codec(params["protocol"], slave=params.get("slave", 1), reg_map=_load_modbus_map())
    codec.invert_current = params.get("invert_current", False)
    return SerialLink(port, params["baud"], codec, poll_hz=params.get("poll_hz", 1.0))


class Watchdog:
    """Every 2 s: if the adapter vanished, drop the link; if the remembered adapter is present
    (under whatever COM name it has now) and nothing is connected, reconnect."""

    def __init__(self):
        self.state = "idle"
        self.last_msg = ""
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            time.sleep(2.0)
            try:
                self.tick()
            except Exception as e:
                self.last_msg = f"watchdog: {e}"

    def tick(self):
        prof = load_profile()
        link = wb.link
        if isinstance(link, SerialLink) and getattr(link, "lost", False):
            link.stop()
            wb.link = None
            self.state, self.last_msg = "lost", f"adapter on {link.port} disconnected; waiting for it to come back"
            link = None
        if link is not None or not prof or not prof.get("auto", True):
            if link is not None:
                self.state = "connected"
            return
        port = find_port(prof["adapter"])
        if not port:
            self.state = "waiting"
            self.last_msg = f"waiting for adapter {prof['adapter'].get('vid')}:{prof['adapter'].get('pid')}"
            return
        try:
            link = _open_serial(prof["params"], port)
        except Exception as e:
            self.state, self.last_msg = "retrying", f"{port}: {e}"
            return
        wb.meta.update(prof.get("meta", {}))
        wb.attach(link)
        self.state, self.last_msg = "connected", f"reconnected on {port}"


watchdog = Watchdog()


# ======================================================================== link API
class OpenReq(BaseModel):
    port: str
    baud: int = 9600
    protocol: str = "daly"
    slave: int = 1
    poll_hz: float = 1.0
    invert_current: bool = False


@app.get("/api/ports")
def ports():
    return list_ports()


@app.post("/api/link/demo")
def link_demo(speed: float = 8.0, series: int = 4):
    series = max(1, min(int(series), 24))
    v = series * 3.2
    wb.meta.update({"label": f"Demo LFP {v:g} V 100 Ah ({series}s, virtual Daly BMS)", "nominal_Ah": 100.0, "nominal_user": False,
                    "i_max_dis": 150.0, "mass_kg": round(2.1 * series + 1.5, 1)})
    wb.attach(VirtualLink(speed=speed, series=series))
    return wb.link.status()


@app.post("/api/link/open")
def link_open(r: OpenReq):
    params = {"baud": r.baud, "protocol": r.protocol, "slave": r.slave, "poll_hz": r.poll_hz,
              "invert_current": r.invert_current}
    if wb.link:
        wb.link.stop()
        wb.link = None
    try:
        link = _open_serial(params, r.port)
    except Exception as e:
        raise HTTPException(400, f"cannot open {r.port}: {e}")
    prev = load_profile()
    port_info = next((p for p in list_ports() if p["device"] == r.port), {"device": r.port})
    adapter = {"vid": port_info.get("vid"), "pid": port_info.get("pid"), "serial": port_info.get("serial"),
               "location": port_info.get("location"), "port": r.port, "chip": port_info.get("chip")}
    same = prev and prev.get("adapter", {}).get("vid") == adapter["vid"] and prev.get("params", {}).get("protocol") == r.protocol
    if same and prev.get("meta"):
        wb.meta.update(prev["meta"])       # same battery setup: keep the nameplate the user entered
    else:
        wb.meta.update({"label": f"{r.protocol.upper()} battery", "i_max_dis": None, "i_max_chg": None,
                        "mass_kg": 0.0, "nominal_user": False})
    wb.attach(link)
    save_profile({"adapter": adapter, "params": params, "meta": wb.meta, "auto": True, "saved": time.time()})
    return link.status()


@app.get("/api/link/profile")
def link_profile():
    prof = load_profile()
    return {"profile": prof, "watchdog": {"state": watchdog.state, "message": watchdog.last_msg},
            "resolved_port": find_port(prof["adapter"]) if prof else None}


@app.post("/api/link/profile/auto")
def link_profile_auto(on: bool):
    prof = load_profile()
    if not prof:
        raise HTTPException(404, "no remembered adapter yet - connect once from Hardware")
    prof["auto"] = on
    save_profile(prof)
    return link_profile()


@app.delete("/api/link/profile")
def link_profile_forget():
    PROFILE_FILE.unlink(missing_ok=True)
    return {"ok": True}


@app.post("/api/link/close")
def link_close():
    prof = load_profile()
    if prof and isinstance(wb.link, SerialLink):  # a deliberate disconnect must not be undone by the watchdog
        prof["auto"] = False
        save_profile(prof)
    if wb.link:
        wb.link.stop()
    wb.link = None
    return {"ok": True}


@app.get("/api/link/status")
def link_status():
    return wb.link.status() if wb.link else {"kind": "none", "running": False}


@app.post("/api/link/sniff")
def link_sniff(port: str):
    if wb.link and getattr(wb.link, "port", None) == port:
        raise HTTPException(409, "port in use by the active link - close it first")
    return sniff(port)


@app.post("/api/link/polling")
def link_polling(on: bool):
    if wb.link:
        wb.link.polling = on
    return link_status()


@app.post("/api/link/invert")
def link_invert(on: bool):
    if wb.link:
        wb.link.codec.invert_current = on
    return link_status()


@app.get("/api/link/frames")
def frames(since: int = 0, limit: int = 300):
    if not wb.link:
        return []
    with wb.link.lock:
        items = [f for f in wb.link.frames if f["seq"] > since]
    return items[-limit:]


@app.post("/api/link/arm")
def arm(on: bool, request: Request):
    _local_only(request)
    wb.writes_armed = on
    return {"armed": on}


class RawReq(BaseModel):
    hex: str
    append: str = "none"  # none | modbus_crc | daly_sum | jbd_sum


@app.post("/api/link/raw")
def raw(r: RawReq, request: Request):
    _local_only(request)
    if not wb.link:
        raise HTTPException(409, "no link")
    if not wb.writes_armed:
        raise HTTPException(403, "writes are disarmed - arm them in the Hardware tab first")
    data = bytes.fromhex(r.hex.replace(" ", "").replace("0x", ""))
    if r.append == "modbus_crc":
        from .hw.protocols import crc16_modbus
        data += crc16_modbus(data).to_bytes(2, "little")
    elif r.append == "daly_sum":
        data += bytes([sum(data) & 0xFF])
    elif r.append == "jbd_sum":
        body = data[2:] if data[:1] == b"\xDD" else data
        data += ((0x10000 - sum(body)) & 0xFFFF).to_bytes(2, "big") + b"\x77"
    wb.link.write_audited(data, "raw console")
    return {"sent": data.hex(" ")}


@app.post("/api/link/cmd")
def cmd(name: str, request: Request):
    _local_only(request)
    if not wb.link:
        raise HTTPException(409, "no link")
    if not wb.writes_armed:
        raise HTTPException(403, "writes are disarmed")
    data = wb.link.codec.command(name)
    if data is None:
        raise HTTPException(400, f"codec '{wb.link.codec.name}' has no command '{name}'")
    wb.link.write_audited(data, f"command {name}")
    return {"sent": data.hex(" ")}


@app.get("/api/link/audit")
def audit():
    if not AUDIT.exists():
        return []
    return [json.loads(l) for l in AUDIT.read_text(encoding="utf-8").splitlines()[-200:]]


MODBUS_MAP = DATA_DIR / "modbus_map.json"


def _load_modbus_map():
    return json.loads(MODBUS_MAP.read_text(encoding="utf-8")) if MODBUS_MAP.exists() else []


@app.get("/api/modbus/map")
def get_map():
    return _load_modbus_map()


@app.post("/api/modbus/map")
def set_map(m: list[dict]):
    MODBUS_MAP.write_text(json.dumps(m, indent=1), encoding="utf-8")
    if wb.link and isinstance(wb.link.codec, ModbusCodec):
        wb.link.codec.map = m
    return {"ok": True, "n": len(m)}


# ======================================================================== recording
@app.post("/api/record/start")
def rec_start(label: str = ""):
    if not wb.link:
        raise HTTPException(409, "no link")
    return {"file": wb.link.start_recording(label)}


@app.post("/api/record/stop")
def rec_stop():
    return {"file": wb.link.stop_recording() if wb.link else None}


@app.get("/api/record/list")
def rec_list():
    return [{"file": p.name, "bytes": p.stat().st_size, "mtime": p.stat().st_mtime}
            for p in sorted(REC_DIR.glob("*.jsonl"), key=lambda p: -p.stat().st_mtime)]


@app.post("/api/record/replay")
def rec_replay(file: str, speed: float = 1.0):
    p = (REC_DIR / file).resolve()
    if p.parent != REC_DIR.resolve() or not p.exists():
        raise HTTPException(404, "no such recording")
    wb.attach(ReplayLink(p, speed))
    return wb.link.status()


# ======================================================================== battery / vehicles
@app.get("/api/battery")
def battery():
    return _clean(wb.refresh_card())


@app.get("/api/battery/meta")
def get_meta():
    return wb.meta


@app.post("/api/battery/meta")
def set_meta(m: dict):
    if "nominal_Ah" in m:
        m["nominal_user"] = True
    wb.meta.update(m)
    prof = load_profile()
    if prof and isinstance(wb.link, SerialLink):
        prof["meta"] = wb.meta
        save_profile(prof)
    wb.est.nominal_Ah = float(wb.meta.get("nominal_Ah", 100.0))
    wb.est.i_max_dis = wb.meta.get("i_max_dis")
    return wb.meta


@app.get("/api/vehicles")
def vehicles():
    return [v.to_dict() for v in wb.vehicles.values()]


@app.post("/api/vehicles")
def save_vehicle(d: dict):
    try:
        return wb.save_vehicle(d).to_dict()
    except TypeError as e:
        raise HTTPException(400, str(e))


@app.get("/api/cycles")
def cycles():
    return [{"id": k, "name": c["name"], "duration_s": len(c["v"]), "km": float(np.sum(c["v"]) / 1000),
             "v_kmh": (c["v"][::2] * 3.6).tolist(), "grade_pct": c["grade_pct"][::2].tolist()}
            for k, c in wb.cycles.items()]


@app.post("/api/cycles/upload")
async def cycle_upload(file: UploadFile = File(...)):
    import pandas as pd, io
    df = pd.read_csv(io.BytesIO(await file.read()))
    cols = {c.lower().split("(")[0].strip(): c for c in df.columns}
    t = df[cols.get("t", cols.get("time"))]
    v = df[cols.get("speed_kmh", cols.get("speed"))]
    g = df[cols["grade_pct"]] if "grade_pct" in cols else None
    key = Path(file.filename).stem
    wb.cycles[key] = cycle_from_csv(t, v, g, name=key)
    return {"id": key}


@app.get("/api/requirements")
def requirements(vehicle: str, cycle: str | None = None):
    v = wb.vehicles[vehicle]
    return _clean(mission_requirements(v, adapt_cycle(wb.cycles[cycle or v.cycle], v), wb.meta.get("mass_kg") or 0))


class EvalReq(BaseModel):
    vehicle: str
    algorithms: list[str] = ["A", "C", "B"]
    runs: int = 300
    start: str = "full"
    cycle: str | None = None
    uncertainty: dict | None = None


@app.post("/api/evaluate")
def evaluate(r: EvalReq):
    return wb.evaluate(r.vehicle, r.algorithms, max(20, min(r.runs, 3000)), r.start, r.uncertainty, r.cycle)


@app.get("/api/uncertainty")
def uncertainty():
    return DEFAULT_UNCERTAINTY


# ======================================================================== fleet (Algorithm B)
@app.get("/api/fleet")
def fleet():
    return load_fleet()


@app.post("/api/fleet/snapshot")
def fleet_snapshot(label: str = ""):
    f = wb.fleet_features()
    if not f:
        raise HTTPException(409, "need a DCIR estimate before snapshotting (wait for load steps)")
    rows = load_fleet()
    f.update({"id": label or f"BAT-{len(rows) + 1:03d}", "synthetic": False, "lot": "measured",
              "t": time.time(), "source": wb.card["evidence"]["source"]})
    rows.append(f)
    save_fleet(rows)
    return f


@app.post("/api/fleet/seed")
def fleet_seed(n: int = 120):
    series = (wb.card or {}).get("identity", {}).get("series", 16) if wb.card and "error" not in wb.card else 16
    nominal = wb.meta.get("nominal_Ah", 100.0)
    rows = [r for r in load_fleet() if not r.get("synthetic")] + synthetic_population(n, n_series=series, nominal_Ah=nominal)
    save_fleet(rows)
    return {"n": len(rows)}


@app.post("/api/fleet/clear-synthetic")
def fleet_clear():
    rows = [r for r in load_fleet() if not r.get("synthetic")]
    save_fleet(rows)
    return {"n": len(rows)}


# ======================================================================== file ingest
@app.post("/api/ingest")
async def ingest(file: UploadFile = File(...), series: int = Form(16), nominal_Ah: float = Form(100.0)):
    raw = await file.read()
    try:
        d = parse_table(raw, file.filename)
    except Exception as e:
        raise HTTPException(400, str(e))
    cells = d["cells"]
    if cells.shape[1] == 1 and series > 1:
        cells = np.repeat(cells / series, series, axis=1)  # pack-level file: assume balanced cells
    if wb.link:
        wb.link.stop()
        wb.link = None
    wb.meta.update({"label": f"File: {file.filename}", "nominal_Ah": nominal_Ah})
    wb.est = Estimator(LFP, nominal_Ah=nominal_Ah, i_max_dis=wb.meta.get("i_max_dis"))

    class _Tel:  # minimal telemetry facade for the card builder
        source = "file"
        temps = [float(d["T"][-1])]
        faults, cycles, charge_mos, discharge_mos, truth = [], None, None, None, None

    for k in range(len(d["t"])):
        wb.est.update(float(d["t"][k]), float(d["I"][k]), cells[k].tolist(), [float(d["T"][k])])

    class _L:
        tel = _Tel()
        def status(self):
            return {"kind": "file", "running": False}
        def stop(self):
            pass
    wb.link = _L()
    card = wb.refresh_card()
    return _clean({"rows": d["rows"], "columns": d["columns"], "cell_columns": d["cell_columns"],
                   "current_flipped": d["current_flipped"], "battery": card})


# ======================================================================== digital twin
@app.get("/api/twin/history")
def twin_history(window: float = 600, points: int = 600):
    return _clean(wb.twin.history(window, points))


@app.post("/api/twin/reset-calibration")
def twin_reset_cal():
    wb.twin.ocv_corr[:] = 0
    wb.twin.ocv_n[:] = 0
    return {"ok": True}


@app.get("/api/twin/fidelity")
def twin_fidelity(window: float = 600):
    return _clean(wb.twin.fidelity(window))


class ForecastReq(BaseModel):
    profile: list[dict]
    ambient_c: float | None = None


@app.post("/api/twin/forecast")
def twin_forecast(r: ForecastReq):
    if wb.twin.pack is None or wb.twin.st is None:
        raise HTTPException(409, "the twin has not synchronised with a battery yet")
    total = sum(float(p.get("duration_s", 0)) for p in r.profile)
    if total <= 0 or total > 12 * 3600:
        raise HTTPException(400, "profile must last between 1 s and 12 h")
    with wb.lock:
        pack, st = wb.twin.pack, wb.twin.st
        T_amb = r.ambient_c if r.ambient_c is not None else float(st.T)
        out = forecast(pack, st, r.profile, T_amb, wb.meta.get("i_max_dis"), ocv_corr=wb.twin._corr)
    out["start"] = {"soc": float(np.mean(st.z)), "T": float(st.T), "V": wb.twin.latest["V"] if wb.twin.latest else None}
    out["assumptions"] = {"ambient_c": T_amb, "params": wb.est.ecm_params()[1],
                          "thermal": "lumped C_th/hA defaults (not calibrated)",
                          "ocv_calibration_bins": len(wb.twin.ocv_table())}
    return _clean(out)


# ======================================================================== research sessions
class LogReq(BaseModel):
    label: str = ""
    notes: str = ""


@app.post("/api/log/start")
def log_start(r: LogReq):
    if wb.logger:
        raise HTTPException(409, f"already recording {wb.logger.name}")
    if not wb.link:
        raise HTTPException(409, "no battery connected")
    p, src = wb.est.ecm_params() if wb.est.buf else (None, None)
    wb.logger = SessionLogger(r.label, r.notes, {
        "battery": {k: v for k, v in wb.meta.items()}, "link": wb.link.status(),
        "chemistry": LFP.to_dict(), "params_start": {"cell": p.to_dict() if p else None, "provenance": src}})
    return {"name": wb.logger.name}


@app.post("/api/log/stop")
def log_stop():
    if not wb.logger:
        return {"name": None}
    p, src = wb.est.ecm_params() if wb.est.buf else (None, None)
    name = wb.logger.close({"params_end": {"cell": p.to_dict() if p else None, "provenance": src},
                            "twin_fidelity_end": _clean(wb.twin.fidelity()),
                            "ocv_calibration": _clean(wb.twin.ocv_table())})
    wb.logger = None
    return {"name": name}


@app.get("/api/log/list")
def log_list():
    return _clean(list_sessions())


@app.get("/api/log/download")
def log_download(name: str, kind: str = "csv"):
    p = (SESS_DIR / f"{name}.{'json' if kind == 'json' else 'csv'}").resolve()
    if p.parent != SESS_DIR.resolve() or not p.exists():
        raise HTTPException(404, "no such session")
    return FileResponse(p, filename=p.name)


# ======================================================================== inference plugins
@app.get("/api/models")
def models():
    return _clean(wb.plugins.summary())


@app.post("/api/models/reload")
def models_reload():
    return _clean(wb.plugins.load())


@app.post("/api/models/enable")
def models_enable(key: str, on: bool):
    wb.plugins.enabled[key] = on
    return _clean(wb.plugins.summary())


# ======================================================================== live stream
@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    try:
        while True:
            card = wb.refresh_card()
            st = wb.link.status() if wb.link else {"kind": "none"}
            tw = wb.twin.latest
            st = {**st, "watchdog": watchdog.state, "watchdog_msg": watchdog.last_msg}
            payload = {"t": time.time(), "link": st, "battery": card,
                       "twin": {"latest": tw, "fidelity": wb.twin.fidelity()} if tw else None,
                       "log": {"name": wb.logger.name, "rows": wb.logger.rows} if wb.logger else None,
                       "models": [{"key": p["key"], "name": p["name"], "result": p["result"], "load_error": p["load_error"]}
                                  for p in wb.plugins.summary()]}
            await sock.send_text(json.dumps(_clean(payload)))
            await asyncio.sleep(0.5)
    except (WebSocketDisconnect, RuntimeError):
        return
