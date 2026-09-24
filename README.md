# Battery Workbench

A battery R&D workbench for one battery on the bench, from a single cell to a 16s+ pack. The cell count is read from the BMS.

The battery is the hero: a live 3D model that opens up to its cells, with current flowing through the series path. On the right are live statistics. At the bottom is how well the battery suits the **application** it is tested against (a vehicle or load profile), and why. **Details** opens the evidence.

The demo battery can be 1, 4, 8 or 16 cells. Pick the size on the start screen.

## Set up (first time)

Requires Python 3.12+ and Node 20+.

```bash
python -m venv backend/.venv
backend/.venv/Scripts/pip install -r backend/requirements.txt   # on Linux/macOS: backend/.venv/bin/pip
cd frontend && npm install
```

## Run it

```powershell
./start.ps1          # engine on :8000, UI on :5173, opens the browser
```

To run the two parts by hand:

```bash
cd frontend && npm run engine    # FastAPI analysis engine (Python venv in backend/.venv)
cd frontend && npm run dev       # UI at http://localhost:5173
```

## Connect your RS485 battery

1. Plug in the USB-RS485 adapter and wire A to A, B to B, with a common GND if the BMS port has one.
2. Open **Hardware** and press **Rescan ports**. The adapter chip is identified from its USB VID/PID (CH340, FTDI, CP210x and so on).
3. Press **Identify device**. It listens passively, then sends read-only probes for Daly, JBD and Modbus RTU (slaves 1–3) at 4800 to 115200 baud, and fills in the settings it finds.
4. Press **Connect**. If current shows the wrong sign, flip **Current sign**.
5. Enter the nameplate (Ah, BMS current limits, mass). Without the BMS discharge limit, the gate stays at *test required*.

Writes (MOSFET commands and the raw hex console with a checksum helper) need **Writes armed**. Every write is appended to `backend/data/write_audit.jsonl`. **Record session** saves every raw frame, and **Replay** runs a recording back through the same decoder.

> The Daly and JBD frame layouts were implemented from memory of the published protocol descriptions and verified only against the built-in virtual BMS. Confirm them against your device's bytes in the bus-traffic console.

## Digital twin, R&D and inference (the dock at the bottom)

| Tab | What it does |
|---|---|
| Compatibility | Engines A, B and C against the selected application |
| Live | Measured vs twin-predicted voltage, current, temperature and cell spread (2 min to 6 h) |
| Twin fidelity | Residual (measured − twin) for the pack and per cell, the twin's parameters with their provenance, and OCV self-calibration |
| Ask the twin | Run a load profile (current, power, rest) forward from the battery's current state. Nothing is sent to the battery |
| Models | Inference plugins from `backend/plugins/*.py`, run on live data about once a second. Copy `_template_ml_model.py` to plug in a trained model |
| Data | Record research sessions to `backend/data/sessions/`: a CSV of every cell and sensor plus the twin's prediction, and a JSON sidecar with battery, link, chemistry, model parameters and OCV calibration |

The twin (`backend/bdt/model/livetwin.py`) is a per-cell 2RC model driven by the measured current. It uses the measured temperature and is nudged slowly toward the BMS SOC. After 5 minutes of rest it learns a correction to the generic LFP OCV table, pack-wide in 5 % SOC bins. It learns one pack-wide correction rather than one per cell, so real cell imbalance stays visible in the per-cell bias chart.

Known limit: forecasts treat all cells as identical until per-cell parameters are identified from load steps, so the cell a forecast names as failing first is not yet meaningful.

## What is in here

| Layer | Path | Notes |
|---|---|---|
| Protocol codecs | `backend/bdt/hw/protocols.py` | Daly, JBD, Modbus RTU (register-map driven) |
| Transport | `backend/bdt/hw/link.py` | serial, virtual demo bus, replay, sniffer, audit |
| 2RC electro-thermal model | `backend/bdt/model/ecm.py` | vectorised over cells and Monte Carlo runs; multi-constraint SOP |
| Estimator | `backend/bdt/model/estimator.py` | DCIR from load steps, 2RC from relaxation, OCV-anchored capacity |
| Demo battery | `backend/bdt/model/simulator.py` | 16s LFP with one weak cell, answering real Daly frames |
| Vehicles and duty cycles | `backend/bdt/model/vehicle.py` | road-load model; presets and cycles are illustrative and synthetic |
| Gate | `backend/bdt/engines/gate.py` | PASS / FAIL / UNKNOWN |
| Engine A | `backend/bdt/engines/safefit.py` | weakest-link margins |
| Engine B | `backend/bdt/engines/cluster.py` | LOF, then fuzzy C-means (Xie–Beni), then vehicle pass rates |
| Engine C | `backend/bdt/engines/twin.py` | Monte Carlo 2RC + thermal mission simulation, Wilson CI, sensitivity |

## Verified so far

Checked in demo mode against the simulator's ground truth: τ₁ within 1 %, R₁ within 7 %, τ₂ within 5 %, DCIR_1s within 5 %, and the weak cell identified correctly.
This checks the estimator's logic. It is **not** validation against real hardware.
No score in this tool has been validated against real vehicle missions yet.
