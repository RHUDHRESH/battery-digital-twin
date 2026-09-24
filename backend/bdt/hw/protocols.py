"""RS485 / UART BMS protocol codecs.

Frame layouts are implemented from publicly circulated protocol descriptions
(Daly "UART/485 communication protocol", JBD/Xiaoxiang "BMS protocol"), and
Modbus RTU (Modbus.org spec, CRC-16/MODBUS). The Daly and JBD layouts were written
from memory of those documents and have NOT yet been checked against your device:
the sniffer + raw console exist precisely to confirm them against real bytes.

Internal sign convention everywhere in this code base: current > 0 = DISCHARGE.
Both Daly and JBD report current > 0 = CHARGE, so decoders negate it. If your
device disagrees, flip `invert_current` on the link.
"""
from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field


# ---------------------------------------------------------------- telemetry model
@dataclass
class Telemetry:
    t: float = 0.0
    pack_v: float | None = None
    current: float | None = None          # A, >0 discharge
    soc_bms: float | None = None          # 0..1 as reported by BMS (not trusted for LFP)
    cells: list[float] = field(default_factory=list)   # V
    temps: list[float] = field(default_factory=list)   # C
    charge_mos: bool | None = None
    discharge_mos: bool | None = None
    cycles: int | None = None
    residual_Ah: float | None = None
    full_Ah: float | None = None          # MEASURED full-charge capacity, if the BMS reports one
    design_Ah: float | None = None        # nameplate / design capacity register
    n_cells: int | None = None
    n_temps: int | None = None
    faults: list[str] = field(default_factory=list)
    source: str = ""
    truth: dict | None = None             # only populated by the simulator

    def complete(self) -> bool:
        return self.pack_v is not None and self.current is not None and len(self.cells) > 0

    def to_dict(self):
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


# ---------------------------------------------------------------- Modbus CRC
def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


class Codec:
    name = "base"
    invert_current = False

    def __init__(self):
        self.buf = bytearray()

    def polls(self) -> list[bytes]:
        return []

    def feed(self, data: bytes, tel: Telemetry) -> list[dict]:
        """Consume bytes, update tel in place, return decoded frame descriptors."""
        raise NotImplementedError

    def command(self, name: str) -> bytes | None:
        return None

    def _sign(self, i_charge_positive: float) -> float:
        i = -i_charge_positive
        return -i if self.invert_current else i


# ================================================================ DALY
class DalyCodec(Codec):
    """Daly smart BMS, 13-byte frames: A5 | addr | cmd | 08 | data[8] | sum8."""
    name = "daly"
    HOST_ADDR = 0x40  # 0x40 = RS485 / UART host per Daly doc; some firmwares use 0x80 on UART

    @staticmethod
    def frame(cmd: int, addr: int = 0x40, data: bytes = b"\x00" * 8) -> bytes:
        f = bytes([0xA5, addr, cmd, 0x08]) + data.ljust(8, b"\x00")[:8]
        return f + bytes([sum(f) & 0xFF])

    def polls(self):
        return [self.frame(c, self.HOST_ADDR) for c in (0x90, 0x91, 0x92, 0x93, 0x94, 0x95, 0x96, 0x98)]

    def command(self, name):
        table = {
            "discharge_mos_on": (0xD9, 1), "discharge_mos_off": (0xD9, 0),
            "charge_mos_on": (0xDA, 1), "charge_mos_off": (0xDA, 0),
        }
        if name not in table:
            return None
        cmd, v = table[name]
        return self.frame(cmd, self.HOST_ADDR, bytes([v]))

    def feed(self, data, tel):
        self.buf += data
        out = []
        while True:
            i = self.buf.find(0xA5)
            if i < 0:
                self.buf.clear()
                break
            if i > 0:
                del self.buf[:i]
            if len(self.buf) < 13:
                break
            f = bytes(self.buf[:13])
            if (sum(f[:12]) & 0xFF) != f[12] or f[3] != 0x08:
                del self.buf[:1]
                continue
            del self.buf[:13]
            if f[1] == self.HOST_ADDR:  # our own echoed request (half-duplex echo)
                continue
            out.append(self._decode(f[2], f[4:12], tel))
        return out

    def _decode(self, cmd, d, tel):
        u16 = lambda o: (d[o] << 8) | d[o + 1]
        desc = {"proto": "daly", "cmd": f"0x{cmd:02X}"}
        if cmd == 0x90:
            tel.pack_v = u16(0) / 10.0
            tel.current = self._sign((u16(4) - 30000) / 10.0)
            tel.soc_bms = u16(6) / 1000.0
            desc["name"] = "SOC/total voltage/current"
        elif cmd == 0x93:
            state = {0: "idle", 1: "charge", 2: "discharge"}.get(d[0], "?")
            tel.charge_mos, tel.discharge_mos = bool(d[1]), bool(d[2])
            tel.residual_Ah = ((d[4] << 24) | (d[5] << 16) | (d[6] << 8) | d[7]) / 1000.0
            desc["name"] = f"MOS status ({state})"
        elif cmd == 0x94:
            tel.n_cells, tel.n_temps = d[0], d[1]
            tel.cycles = u16(5)
            desc["name"] = "status info"
        elif cmd == 0x95:
            fno = d[0]
            n = tel.n_cells or 16
            if len(tel.cells) != n:
                tel.cells = [0.0] * n
            for k in range(3):
                idx = (fno - 1) * 3 + k
                if 0 <= idx < n:
                    tel.cells[idx] = ((d[1 + 2 * k] << 8) | d[2 + 2 * k]) / 1000.0
            desc["name"] = f"cell voltages frame {fno}"
        elif cmd == 0x96:
            fno = d[0]
            n = tel.n_temps or 4
            if len(tel.temps) != n:
                tel.temps = [0.0] * n
            for k in range(7):
                idx = (fno - 1) * 7 + k
                if 0 <= idx < n:
                    tel.temps[idx] = float(d[1 + k] - 40)
            desc["name"] = f"temperatures frame {fno}"
        elif cmd == 0x98:
            names = []
            for byte_i, b in enumerate(d[:7]):
                for bit in range(8):
                    if b >> bit & 1:
                        names.append(f"fault_b{byte_i}_{bit}")
            tel.faults = names
            desc["name"] = "failure codes"
        elif cmd in (0x91, 0x92):
            desc["name"] = "min/max cell voltage" if cmd == 0x91 else "min/max temperature"
        else:
            desc["name"] = "unhandled"
        desc["data"] = d.hex(" ")
        return desc


# ================================================================ JBD / Xiaoxiang
class JbdCodec(Codec):
    """JBD: DD | A5(read) cmd len data chk16 | 77 ; reply DD cmd status len data chk16 77."""
    name = "jbd"

    @staticmethod
    def req(cmd: int, payload: bytes = b"") -> bytes:
        body = bytes([cmd, len(payload)]) + payload
        chk = (0x10000 - sum(body)) & 0xFFFF
        return b"\xDD\xA5" + body + struct.pack(">H", chk) + b"\x77"

    def polls(self):
        return [self.req(0x03), self.req(0x04)]

    def feed(self, data, tel):
        self.buf += data
        out = []
        while True:
            i = self.buf.find(0xDD)
            if i < 0:
                self.buf.clear()
                break
            if i > 0:
                del self.buf[:i]
            if len(self.buf) < 7:
                break
            if self.buf[1] == 0xA5:  # echoed request
                ln = self.buf[3]
                del self.buf[: min(len(self.buf), 7 + ln)]
                continue
            ln = self.buf[3]
            total = 7 + ln
            if len(self.buf) < total:
                break
            f = bytes(self.buf[:total])
            if f[-1] != 0x77:
                del self.buf[:1]
                continue
            chk = struct.unpack(">H", f[-3:-1])[0]
            ok = chk in ((0x10000 - sum(f[2:-3])) & 0xFFFF, (0x10000 - sum(f[1:-3])) & 0xFFFF)
            del self.buf[:total]
            if not ok:
                out.append({"proto": "jbd", "name": "checksum error", "data": f.hex(" ")})
                continue
            out.append(self._decode(f[1], f[2], f[4:-3], tel))
        return out

    def _decode(self, cmd, status, d, tel):
        desc = {"proto": "jbd", "cmd": f"0x{cmd:02X}", "status": status, "data": d.hex(" ")}
        if cmd == 0x03 and len(d) >= 23:
            v, i, rem, nom, cyc = struct.unpack(">HhHHH", d[:10])
            tel.pack_v = v / 100.0
            tel.current = self._sign(i / 100.0)
            tel.residual_Ah = rem / 100.0
            tel.design_Ah = nom / 100.0   # JBD "nominal capacity" = design value, not a measurement
            tel.cycles = cyc
            prot = struct.unpack(">H", d[16:18])[0]
            tel.faults = [f"protect_bit{b}" for b in range(16) if prot >> b & 1]
            tel.soc_bms = d[19] / 100.0
            tel.charge_mos, tel.discharge_mos = bool(d[20] & 1), bool(d[20] & 2)
            tel.n_cells, tel.n_temps = d[21], d[22]
            tel.temps = [
                (struct.unpack(">H", d[23 + 2 * k: 25 + 2 * k])[0] - 2731) / 10.0
                for k in range(tel.n_temps) if 25 + 2 * k <= len(d)
            ]
            desc["name"] = "basic info"
        elif cmd == 0x04:
            tel.cells = [struct.unpack(">H", d[k:k + 2])[0] / 1000.0 for k in range(0, len(d) - 1, 2)]
            desc["name"] = "cell voltages"
        else:
            desc["name"] = "unhandled"
        return desc


# ================================================================ Modbus RTU (generic, map-driven)
DEFAULT_MODBUS_MAP = [
    # {"name", "fn" (3|4), "addr", "type" (u16|i16|u32|i32), "scale", "field"}
    # Fill from your device's register document. field ∈ Telemetry attrs or "cell:<k>" / "temp:<k>".
]


class ModbusCodec(Codec):
    name = "modbus"

    def __init__(self, slave: int = 1, reg_map: list[dict] | None = None):
        super().__init__()
        self.slave = slave
        self.map = reg_map or list(DEFAULT_MODBUS_MAP)
        self._pending: list[tuple[int, int, int]] = []  # (fn, start, count) in order sent

    @staticmethod
    def read_req(slave, fn, start, count) -> bytes:
        body = struct.pack(">BBHH", slave, fn, start, count)
        return body + struct.pack("<H", crc16_modbus(body))

    @staticmethod
    def write_single(slave, addr, value) -> bytes:
        body = struct.pack(">BBHH", slave, 6, addr, value & 0xFFFF)
        return body + struct.pack("<H", crc16_modbus(body))

    def _blocks(self):
        by_fn: dict[int, list[int]] = {}
        for e in self.map:
            width = 2 if e.get("type", "u16") in ("u32", "i32") else 1
            by_fn.setdefault(e.get("fn", 3), []).extend(range(e["addr"], e["addr"] + width))
        blocks = []
        for fn, addrs in by_fn.items():
            addrs = sorted(set(addrs))
            start = prev = addrs[0]
            for a in addrs[1:] + [None]:
                if a is None or a != prev + 1 or a - start >= 120:
                    blocks.append((fn, start, prev - start + 1))
                    start = a
                prev = a if a is not None else prev
        return blocks

    def polls(self):
        self._pending = self._blocks()
        return [self.read_req(self.slave, fn, s, c) for fn, s, c in self._pending]

    def feed(self, data, tel):
        self.buf += data
        out = []
        while len(self.buf) >= 5:
            slave, fn = self.buf[0], self.buf[1]
            if fn & 0x80:
                total = 5
            elif fn in (3, 4):
                total = 5 + self.buf[2]
            elif fn in (6, 16):
                total = 8
            else:
                del self.buf[:1]
                continue
            if len(self.buf) < total:
                break
            f = bytes(self.buf[:total])
            if crc16_modbus(f[:-2]) != struct.unpack("<H", f[-2:])[0]:
                del self.buf[:1]
                continue
            del self.buf[:total]
            desc = {"proto": "modbus", "slave": slave, "fn": fn, "data": f.hex(" ")}
            if fn & 0x80:
                desc["name"] = f"exception code {f[2]}"
            elif fn in (3, 4) and self._pending:
                pfn, start, count = self._pending.pop(0)
                regs = struct.unpack(f">{f[2] // 2}H", f[3:-2])
                self._apply(pfn, start, regs, tel)
                desc["name"] = f"read {count} regs @ {start}"
                desc["regs"] = list(regs)
            else:
                desc["name"] = "reply"
            out.append(desc)
        return out

    def _apply(self, fn, start, regs, tel):
        for e in self.map:
            if e.get("fn", 3) != fn:
                continue
            k = e["addr"] - start
            typ = e.get("type", "u16")
            if k < 0 or k >= len(regs) or (typ in ("u32", "i32") and k + 1 >= len(regs)):
                continue
            if typ in ("u32", "i32"):
                raw = (regs[k] << 16) | regs[k + 1]
                if typ == "i32" and raw >= 1 << 31:
                    raw -= 1 << 32
            else:
                raw = regs[k]
                if typ == "i16" and raw >= 1 << 15:
                    raw -= 1 << 16
            val = raw * e.get("scale", 1.0) + e.get("offset", 0.0)
            fld = e["field"]
            if fld.startswith("cell:"):
                idx = int(fld[5:])
                tel.cells += [0.0] * max(0, idx + 1 - len(tel.cells))
                tel.cells[idx] = val
            elif fld.startswith("temp:"):
                idx = int(fld[5:])
                tel.temps += [0.0] * max(0, idx + 1 - len(tel.temps))
                tel.temps[idx] = val
            elif fld == "current_charge_positive":
                tel.current = self._sign(val)
            elif fld == "current":
                tel.current = -val if self.invert_current else val
            elif fld == "soc_pct":
                tel.soc_bms = val / 100.0
            elif hasattr(tel, fld):
                setattr(tel, fld, val)


CODECS = {"daly": DalyCodec, "jbd": JbdCodec, "modbus": ModbusCodec}


def make_codec(name: str, **kw) -> Codec:
    if name == "modbus":
        return ModbusCodec(slave=kw.get("slave", 1), reg_map=kw.get("reg_map"))
    return CODECS[name]()
