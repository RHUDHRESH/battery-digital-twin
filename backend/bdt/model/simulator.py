"""Virtual 16s LFP module + virtual Daly BMS.

The simulator does not hand telemetry to the app directly. It answers Daly poll
frames with Daly-encoded bytes, so demo mode exercises the SAME decoder,
estimator and engines as real RS485 hardware. Ground-truth parameters are
exposed separately so the estimator can be scored against them.
"""
from __future__ import annotations

import threading
import time

import numpy as np

from .chem import LFP
from .ecm import CellParams, ECMPack, PackState, ThermalParams


# (current A [+discharge], duration s) — a lab-style duty that contains clean
# load steps (for DCIR) and long rests (for 2RC relaxation identification).
DUTY = [
    (0, 90), (30, 180), (0, 300), (100, 10), (0, 40), (100, 10), (0, 40), (-50, 10), (0, 60),
    ("drive", 360), (0, 420), (60, 240), (0, 300), (-45, 900), (0, 360),
]


class VirtualPack:
    def __init__(self, n=16, Q_Ah=100.0, seed=7, speed=6.0, T_amb=28.0):
        rng = np.random.default_rng(seed)
        self.n, self.speed, self.T_amb = n, speed, T_amb
        base = CellParams(Q_Ah=Q_Ah)
        Q = Q_Ah * 0.93 * (1 + rng.normal(0, 0.015, n))        # ~93 % SOH module
        R0 = base.R0 * 1.25 * (1 + rng.normal(0, 0.06, n))       # aged resistance
        weak = int(rng.integers(0, n))
        Q[weak] *= 0.94
        R0[weak] *= 1.35                                         # one weak cell
        self.weak_cell = weak
        self.pack = ECMPack(LFP, Q, R0, np.full(n, base.R1 * 1.2), np.full(n, base.tau1),
                            np.full(n, base.R2 * 1.2), np.full(n, base.tau2),
                            ThermalParams())
        self.st = PackState(0.72 + rng.normal(0, 0.008, n), T=T_amb)
        self.sensor_T_offset = rng.normal(0, 0.4, 4)
        self.rng = rng
        self.I = 0.0
        self.t = 0.0
        self.cycles = 142
        self.chg_mos = self.dis_mos = True
        self._duty_i, self._duty_left = 0, DUTY[0][1]
        self._drive_level = 30.0
        self._lock = threading.Lock()
        self._last_wall = time.monotonic()

    # ----------------------------------------------------------------- physics
    def _duty_current(self):
        spec = DUTY[self._duty_i][0]
        if spec == "drive":
            if self.rng.random() < 0.08:
                self._drive_level = float(self.rng.choice([15, 35, 60, 95, -25]))
            return self._drive_level
        return float(spec)

    def advance(self, sim_dt: float | None = None):
        """Advance by sim_dt seconds (default: wall-clock elapsed x speed)."""
        with self._lock:
            now = time.monotonic()
            if sim_dt is None:
                sim_dt = min((now - self._last_wall) * self.speed, 30.0)
            self._last_wall = now
            h = 0.5
            while sim_dt > 1e-9:
                dt = min(h, sim_dt, self._duty_left)
                I = self._duty_current()
                # BMS protection & SOC housekeeping: keep the demo inside a sane window
                v = self.pack.cell_voltages(self.st, I)
                if I > 0 and (np.min(v) < 2.8 or not self.dis_mos):
                    I = 0.0
                if I < 0 and (np.max(v) > 3.55 or not self.chg_mos):
                    I = 0.0
                self.I = I
                self.pack.step(self.st, I, dt, self.T_amb)
                self.t += dt
                sim_dt -= dt
                self._duty_left -= dt
                if self._duty_left <= 1e-9:
                    self._duty_i = (self._duty_i + 1) % len(DUTY)
                    self._duty_left = DUTY[self._duty_i][1]
                    if self._duty_i == 0:
                        self.cycles += 1
                    # steer SOC: skip discharge blocks when low, charge blocks when high
                    zmean = float(np.mean(self.st.z))
                    spec = DUTY[self._duty_i][0]
                    if (zmean < 0.3 and spec != "drive" and spec > 0) or (zmean > 0.9 and spec != "drive" and spec < 0):
                        self._duty_left = 1.0

    def measured(self):
        """Sensor-level values with realistic quantisation/noise."""
        v = self.pack.cell_voltages(self.st, self.I) + self.rng.normal(0, 0.0008, self.n)
        temps = self.st.T + self.sensor_T_offset + self.rng.normal(0, 0.15, 4)
        return v, temps

    def truth(self):
        p = self.pack
        return {
            "sim_time_s": self.t, "current_A": self.I,
            "soc_cells": self.st.z.tolist(), "T_pack": float(self.st.T),
            "Q_Ah": p.Q.tolist(), "R0": p.R0.tolist(), "R1": p.R1.tolist(),
            "tau1": p.tau1.tolist(), "R2": p.R2.tolist(), "tau2": p.tau2.tolist(),
            "weak_cell": self.weak_cell,
        }


class VirtualDalyBMS:
    """Answers Daly request frames exactly like a real BMS on the bus."""

    def __init__(self, pack: VirtualPack, sim_step: float = 1.0):
        self.p = pack
        self.sim_step = sim_step   # simulated seconds per poll cycle (one coherent BMS snapshot)
        self._snap = None

    @staticmethod
    def _frame(cmd, data: bytes):
        f = bytes([0xA5, 0x01, cmd, 0x08]) + data.ljust(8, b"\x00")[:8]
        return f + bytes([sum(f) & 0xFF])

    def respond(self, req: bytes) -> bytes:
        out = bytearray()
        for k in range(0, len(req) - 12, 13):
            f = req[k:k + 13]
            if f[0] != 0xA5 or (sum(f[:12]) & 0xFF) != f[12]:
                continue
            out += self._answer(f[2], f[4:12])
        return bytes(out)

    def _answer(self, cmd, d):
        p = self.p
        if cmd == 0x90 or self._snap is None:   # start of a poll cycle: advance + sample once
            p.advance(self.sim_step)
            self._snap = p.measured()
        v, temps = self._snap
        mv = np.round(v * 1000).astype(int)
        pack_v = float(np.sum(v))
        be16 = lambda x: int(x).to_bytes(2, "big")
        if cmd == 0x90:
            i_raw = int(round(30000 - p.I * 10))  # Daly: + = charge
            soc = int(round(np.clip(np.mean(p.st.z), 0, 1) * 1000))
            return self._frame(0x90, be16(round(pack_v * 10)) + be16(0) + be16(i_raw) + be16(soc))
        if cmd == 0x91:
            return self._frame(0x91, be16(mv.max()) + bytes([int(mv.argmax()) + 1]) + be16(mv.min()) + bytes([int(mv.argmin()) + 1]))
        if cmd == 0x92:
            t = np.round(temps).astype(int) + 40
            return self._frame(0x92, bytes([int(t.max()), int(t.argmax()) + 1, int(t.min()), int(t.argmin()) + 1]))
        if cmd == 0x93:
            state = 2 if p.I > 0.5 else (1 if p.I < -0.5 else 0)
            rem = int(np.mean(p.st.z * p.pack.Q) * 1000)
            return self._frame(0x93, bytes([state, int(p.chg_mos), int(p.dis_mos), 0]) + rem.to_bytes(4, "big"))
        if cmd == 0x94:
            return self._frame(0x94, bytes([p.n, 4, 0, 0, 0]) + be16(p.cycles))
        if cmd == 0x95:
            out = b""
            for fno in range(1, (p.n + 2) // 3 + 1):
                cells = b"".join(be16(mv[i]) if i < p.n else b"\x00\x00" for i in range((fno - 1) * 3, fno * 3))
                out += self._frame(0x95, bytes([fno]) + cells)
            return out
        if cmd == 0x96:
            t = (np.round(temps).astype(int) + 40).tolist()
            return self._frame(0x96, bytes([1] + t + [0] * (7 - len(t))))
        if cmd == 0x98:
            return self._frame(0x98, b"\x00" * 8)
        if cmd == 0xD9:
            p.dis_mos = bool(d[0])
            return self._frame(0xD9, d)
        if cmd == 0xDA:
            p.chg_mos = bool(d[0])
            return self._frame(0xDA, d)
        return b""
