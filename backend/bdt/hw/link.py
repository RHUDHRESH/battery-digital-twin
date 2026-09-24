"""Transport layer: real RS485 (pyserial), virtual demo bus, and file replay.

All three produce the same thing: raw bytes -> codec -> Telemetry, with every
TX/RX byte mirrored to a ring buffer (raw console) and optionally a recorder.
Every write is appended to an audit log.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path

from .protocols import Codec, DalyCodec, JbdCodec, ModbusCodec, Telemetry, make_codec

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
REC_DIR = DATA_DIR / "recordings"
AUDIT = DATA_DIR / "write_audit.jsonl"
REC_DIR.mkdir(parents=True, exist_ok=True)


def list_ports():
    from serial.tools import list_ports as lp
    out = []
    for p in lp.comports():
        out.append({
            "device": p.device, "description": p.description, "hwid": p.hwid,
            "vid": f"{p.vid:04X}" if p.vid else None, "pid": f"{p.pid:04X}" if p.pid else None,
            "chip": _chip(p.vid, p.pid, p.description or ""),
        })
    return out


def _chip(vid, pid, desc):
    known = {(0x1A86, 0x7523): "CH340", (0x1A86, 0x5523): "CH341", (0x0403, 0x6001): "FTDI FT232R",
             (0x0403, 0x6015): "FTDI FT231X", (0x10C4, 0xEA60): "Silicon Labs CP210x",
             (0x067B, 0x2303): "Prolific PL2303"}
    if (vid, pid) in known:
        return known[(vid, pid)]
    if "bluetooth" in desc.lower():
        return "Bluetooth SPP (not an RS485 adapter)"
    return None


class Link:
    """Base: poll loop + decoding + frame ring + recording."""

    kind = "none"

    def __init__(self, codec: Codec, poll_hz: float = 1.0):
        self.codec = codec
        self.poll_hz = poll_hz
        self.tel = Telemetry(source=self.kind)
        self.frames: deque = deque(maxlen=2000)
        self.seq = 0
        self.lock = threading.Lock()
        self.running = False
        self.polling = True
        self.error: str | None = None
        self.rx_bytes = self.tx_bytes = self.good_frames = 0
        self._rec = None
        self._rec_path: Path | None = None
        self.on_telemetry = None  # callback(Telemetry)
        self.inter_frame_s = 0.03  # half-duplex turnaround between polls on a real bus

    # ------------------------------------------------------------ plumbing
    def _log(self, direction, data: bytes, decoded=None):
        with self.lock:
            self.seq += 1
            item = {"seq": self.seq, "t": time.time(), "dir": direction, "hex": data.hex(" ")}
            if decoded:
                item["decoded"] = decoded
            self.frames.append(item)
            if self._rec:
                self._rec.write(json.dumps({"t": item["t"], "dir": direction, "hex": data.hex()}) + "\n")

    def _rx(self, data: bytes):
        if not data:
            return
        self.rx_bytes += len(data)
        decoded = self.codec.feed(data, self.tel)
        self.good_frames += len(decoded)
        self._log("rx", data, decoded or None)

    def _emit(self):
        """Publish ONE coherent snapshot per poll cycle (all frames of the cycle decoded).

        Emitting per frame would pair a new current with last cycle's cell voltages,
        which silently destroys DCIR and relaxation identification."""
        if self.tel.complete():
            self.tel.t = time.time()
            if self.on_telemetry:
                self.on_telemetry(self.tel)

    def _tx(self, data: bytes):
        raise NotImplementedError

    def send(self, data: bytes):
        self.tx_bytes += len(data)
        self._log("tx", data)
        self._tx(data)

    def write_audited(self, data: bytes, why: str):
        with open(AUDIT, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "link": self.kind, "why": why, "hex": data.hex(" ")}) + "\n")
        self.send(data)

    # ------------------------------------------------------------ recording
    def start_recording(self, label: str = ""):
        self.stop_recording()
        name = time.strftime("%Y%m%d-%H%M%S") + (f"-{label}" if label else "") + ".jsonl"
        self._rec_path = REC_DIR / name
        self._rec = open(self._rec_path, "w", encoding="utf-8")
        self._rec.write(json.dumps({"meta": {"codec": self.codec.name, "link": self.kind}}) + "\n")
        return name

    def stop_recording(self):
        if self._rec:
            self._rec.close()
            self._rec = None
            return self._rec_path.name
        return None

    # ------------------------------------------------------------ lifecycle
    def start(self):
        self.running = True
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def stop(self):
        self.running = False
        self.stop_recording()

    def _poll_loop(self):
        while self.running:
            t0 = time.monotonic()
            if self.polling:
                try:
                    for req in self.codec.polls():
                        self.send(req)
                        if self.inter_frame_s:
                            time.sleep(self.inter_frame_s)
                    if self.inter_frame_s:
                        time.sleep(0.1)  # let the last reply land
                    self._emit()
                except Exception as e:  # keep the loop alive; surface the error
                    self.error = str(e)
            elif self.good_frames:
                self._emit()  # passive listening: publish whatever the bus delivered
            time.sleep(max(0.0, 1.0 / self.poll_hz - (time.monotonic() - t0)))

    def status(self):
        return {"kind": self.kind, "codec": self.codec.name, "running": self.running, "polling": self.polling,
                "error": self.error, "rx_bytes": self.rx_bytes, "tx_bytes": self.tx_bytes,
                "good_frames": self.good_frames, "recording": self._rec_path.name if self._rec else None,
                "invert_current": self.codec.invert_current}


class SerialLink(Link):
    kind = "rs485"

    def __init__(self, port: str, baud: int, codec: Codec, poll_hz=1.0, parity="N", stopbits=1):
        super().__init__(codec, poll_hz)
        import serial
        self.ser = serial.Serial(port, baud, timeout=0.05, parity=parity, stopbits=stopbits)
        self.port, self.baud = port, baud
        # half-duplex: wait for the longest expected reply (~80 bytes, 10 bits/byte) plus BMS turnaround
        # before the next request, or the next request collides with the reply on the bus
        self.inter_frame_s = 80 * 10 / baud + 0.06

    def _tx(self, data):
        self.ser.write(data)
        self.ser.flush()

    def start(self):
        super().start()
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self):
        while self.running:
            try:
                data = self.ser.read(256)
                if data:
                    self._rx(data)
            except Exception as e:
                self.error = str(e)
                time.sleep(0.5)

    def stop(self):
        super().stop()
        try:
            self.ser.close()
        except Exception:
            pass

    def status(self):
        return {**super().status(), "port": self.port, "baud": self.baud}


class VirtualLink(Link):
    """Demo mode: a virtual Daly BMS on a virtual bus."""
    kind = "demo"

    def __init__(self, speed=8.0, seed=7, series=16, Q_Ah=100.0):
        """speed = simulated seconds per wall second (each poll cycle = 1 simulated second)."""
        from ..model.simulator import VirtualDalyBMS, VirtualPack
        super().__init__(DalyCodec(), poll_hz=speed)
        self.pack = VirtualPack(n=series, Q_Ah=Q_Ah, speed=speed, seed=seed)
        self.bms = VirtualDalyBMS(self.pack, sim_step=1.0)
        self.inter_frame_s = 0.0

    def _tx(self, data):
        reply = self.bms.respond(data)
        if reply:
            self.tel.truth = self.pack.truth()
            self._rx(reply)


class ReplayLink(Link):
    """Replays a recorded session through the codec, at `speed` x real time."""
    kind = "replay"

    def __init__(self, path: Path, speed: float = 1.0):
        lines = path.read_text(encoding="utf-8").splitlines()
        meta = json.loads(lines[0]).get("meta", {})
        super().__init__(make_codec(meta.get("codec", "daly")))
        self.items = [json.loads(l) for l in lines[1:] if l.strip()]
        self.speed = speed
        self.polling = False

    def _tx(self, data):
        pass

    def start(self):
        self.running = True
        threading.Thread(target=self._play, daemon=True).start()

    def _play(self):
        if not self.items:
            return
        t_first, w0 = self.items[0]["t"], time.monotonic()
        first_poll = self.codec.polls()[0].hex() if self.codec.polls() else None
        for it in self.items:
            if not self.running:
                return
            delay = (it["t"] - t_first) / self.speed - (time.monotonic() - w0)
            if delay > 0:
                time.sleep(delay)
            if it["dir"] == "rx":
                self._rx(bytes.fromhex(it["hex"]))
            elif it["hex"] == first_poll:
                self._emit()  # a new poll cycle starts: the previous one is complete
        self.running = False


# ---------------------------------------------------------------- sniffer
BAUDS = [9600, 19200, 38400, 57600, 115200, 4800]


def sniff(port: str, bauds=None, listen_s=1.2):
    """Fingerprint an unknown device: passive listen, then active probes per baud.

    Returns candidate (baud, protocol) pairs ranked by number of valid frames.
    Active probes only READ (Daly 0x90, JBD 0x03, Modbus fn3/fn4 reg 0 on slaves 1..3).
    """
    import serial
    results = []
    for baud in bauds or BAUDS:
        entry = {"baud": baud, "passive_bytes": 0, "passive_hex": "", "hits": []}
        try:
            with serial.Serial(port, baud, timeout=0.1) as ser:
                ser.reset_input_buffer()
                t_end = time.monotonic() + listen_s
                passive = bytearray()
                while time.monotonic() < t_end:
                    passive += ser.read(256)
                entry["passive_bytes"] = len(passive)
                entry["passive_hex"] = bytes(passive[:64]).hex(" ")
                for cod in (DalyCodec(), JbdCodec()):
                    tel = Telemetry()
                    frames = cod.feed(bytes(passive), tel)
                    if frames:
                        entry["hits"].append({"protocol": cod.name, "mode": "passive", "frames": len(frames)})
                probes = [("daly", DalyCodec.frame(0x90), DalyCodec()),
                          ("daly(0x80)", DalyCodec.frame(0x90, 0x80), DalyCodec()),
                          ("jbd", JbdCodec.req(0x03), JbdCodec())]
                for sl in (1, 2, 3):
                    for fn in (3, 4):
                        probes.append((f"modbus slave {sl} fn{fn}", ModbusCodec.read_req(sl, fn, 0, 2), ModbusCodec(sl)))
                for label, req, cod in probes:
                    rx = b""
                    for attempt in range(2):  # some BMSs (e.g. JBD) sleep and ignore the first frame
                        ser.reset_input_buffer()
                        ser.write(req)
                        time.sleep(0.3)
                        rx = ser.read(512)
                        if rx and rx != req:
                            break
                    if isinstance(cod, ModbusCodec):
                        cod._pending = [(req[1], 0, 2)]
                    frames = cod.feed(rx, Telemetry()) if rx else []
                    if frames:
                        entry["hits"].append({"protocol": label, "mode": "active", "frames": len(frames),
                                              "reply": rx[:40].hex(" ")})
                    elif rx and rx != req:
                        entry["hits"].append({"protocol": label, "mode": "active-unparsed", "frames": 0,
                                              "reply": rx[:40].hex(" ")})
        except Exception as e:
            entry["error"] = str(e)
        results.append(entry)
    ranked = sorted(
        [dict(baud=r["baud"], **h) for r in results for h in r["hits"] if h["frames"] > 0],
        key=lambda h: -h["frames"])
    return {"port": port, "per_baud": results, "best": ranked[0] if ranked else None}
