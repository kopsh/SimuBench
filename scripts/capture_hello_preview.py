"""Capture the home page's Hello World preview from the engine: never a hand-drawn waveform.

Runs the packaged Hello World bench and writes, for web/src/features/home/hello-preview.json:
several scope acquisitions of CH1 (volts, decimated to 400 points over the 10 divisions), the
same number of spectrum analyzer sweeps (dBm, 1001 points), the instruments' *IDN? replies and the
peak marker after :CALC:MARK:MAX — what a script sees on a real bench. Re-run after a change to
the bench or to a model it uses:

    uv run python scripts/capture_hello_preview.py
"""

from __future__ import annotations

import contextlib
import json
import shutil
import struct
import sys
import tempfile
from pathlib import Path

import pyvisa

ROOT = Path(__file__).resolve().parents[1]
HELLO = ROOT / "src" / "simubench" / "library" / "examples" / "hello.bench.yaml"
OUT = ROOT / "web" / "src" / "features" / "home" / "hello-preview.json"
CAPTURES = 5
POINTS = 400


def read_block(inst) -> bytes:
    head = inst.read_bytes(2)
    data = inst.read_bytes(int(inst.read_bytes(int(head[1:2]))))
    inst.read_bytes(1)
    with contextlib.suppress(pyvisa.errors.VisaIOError):
        inst.read_bytes(1)  # the second line feed some firmware sends
    return data


def scope_capture(scope) -> list[float]:
    """CH1 in volts, as the SDS programming guide's read-waveform example converts it."""
    scope.write(":WAVeform:STARt 0")
    scope.write("WAV:SOUR C1")
    scope.write("WAV:PREamble?")
    desc = read_block(scope)
    probe = struct.unpack("<f", desc[0x148:0x14C])[0]
    vdiv = struct.unpack("<f", desc[0x9C:0xA0])[0] * probe
    offset = struct.unpack("<f", desc[0xA0:0xA4])[0] * probe
    code = struct.unpack("<f", desc[0xA4:0xA8])[0]
    scope.write("WAV:DATA?")
    raw = read_block(scope)
    volts = [b / code * vdiv - offset for b in struct.unpack(f"<{len(raw)}b", raw)]
    step = len(volts) / POINTS
    return [round(volts[int(i * step)], 4) for i in range(POINTS)]


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        bench = Path(tmp) / HELLO.name
        shutil.copy(HELLO, bench)
        rm = pyvisa.ResourceManager(f"{bench}@simubench")
        scope = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=20000)
        sa = rm.open_resource("sa", read_termination="\n", write_termination="\n", timeout=20000)
        out: dict = {"source": "scripts/capture_hello_preview.py", "scope": [], "sa": []}
        for _ in range(CAPTURES):
            out["scope"].append(scope_capture(scope))
            out["sa"].append([round(float(v), 2) for v in sa.query(":TRAC:DATA? TRACE1").split(",")])
            sa.write(":INIT:IMM")
            sa.query("*OPC?")
        out["idn"] = {"scope": scope.query("*IDN?").strip(), "sa": sa.query("*IDN?").strip()}
        sa.write(":CALC:MARK:MAX")
        out["marker"] = {"x": sa.query(":CALC:MARK:X?").strip(), "y": sa.query(":CALC:MARK:Y?").strip()}
        rm.close()
    OUT.write_text(json.dumps(out, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
