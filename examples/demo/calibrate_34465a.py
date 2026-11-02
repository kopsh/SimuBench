#!/usr/bin/env python3
"""Calibrate a Keysight 34465A against a Fluke 5522A, the way a calibration script would.

The script uses only PyVISA and the instruments' standard commands (D5): the same file runs
against the two real instruments, a SOCKET/HiSLIP address, or SimuBench's in-process backend:

    python calibrate_34465a.py --bench examples/demo/calibration.bench.yaml \\
        --assets assets.json --function DC --report report-dc.csv

The calibration procedure follows the 34465A service guide: as-found measurements, a zero and a
gain adjustment per DC range while the security is off, then as-left measurements. The reference
source is checked against the bench's calibration register before anything is measured (the CSV
or JSON that ``simubench assets`` prints). The report is a CSV: phase, nominal, reading, limits
and the conformity statement with a guard band (ILAC-G8's non-binary decision: pass when the
error is inside the limits shrunk by w, fail when it is beyond the limits widened by w, and no
statement in between).

The script does not import SimuBench. The truth of each statement is checked afterwards with
``simubench verdicts`` (see docs/demo-calibration.md).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyvisa

#: 1-year DC limits of the 34465A (Truevolt data sheet 5991-1983EN): % reading + % range.
DC_LIMITS = {
    0.0: 5.0e-6,  # 0 V at the 0.1 V range: 0.0005 % of range
    1.0: 40.0e-6,  # 0.0035 % of 1 V + 0.0005 % of 1 V
    10.0: 4.0e-4,
    100.0: 4.0e-3,
}
#: 1-year AC limit at 1 V, 1 kHz: 0.06 % of reading + 0.03 % of range (1 V range).
AC_LIMIT_1V = 9.0e-4
#: Guard band w: the demo takes 20 % of the limit as w so the three decisions all occur. A real
#: procedure derives w from the expanded uncertainty U (ILAC-G8); do not copy this ratio.
GUARD = 0.2
#: The points of the demo; 0 V uses the 0.1 V range automatically.
DC_POINTS = (0.0, 1.0, -1.0, 10.0, -10.0, 100.0)
AC_POINTS = (1.0,)
DC_RANGES = (0.1, 1.0, 10.0, 100.0)


@dataclass
class Point:
    phase: str
    nominal: float
    reading: float
    low: float
    high: float
    decision: str


def q(inst: Any, command: str) -> str:
    """One query: write, then read the reply (real instruments and SimuBench alike)."""
    inst.write(command)
    reply = inst.read().strip()
    if not reply:
        raise RuntimeError(f"{command}: no reply")
    return reply


def check_error(dut: Any, what: str) -> None:
    reply = q(dut, ":SYSTem:ERRor?")
    if not reply.startswith("+0,"):
        raise RuntimeError(f"{what}: the instrument reports {reply}")


def ident(inst: Any) -> tuple[str, str]:
    fields = q(inst, "*IDN?").split(",")
    if len(fields) < 3:
        raise RuntimeError(f"cannot read the identity: {','.join(fields)}")
    return fields[1].strip(), fields[2].strip()


def parse_reading(reply: str) -> float:
    try:
        return float(reply.lstrip("+"))
    except ValueError as exc:
        raise RuntimeError(f"the meter answered {reply!r}, which is not a reading") from exc


def read_register(path: Path) -> dict[str, dict[str, Any]]:
    """The calibration register as ``simubench assets --format json`` prints it, by serial."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise RuntimeError(f"{path}: expected the JSON list printed by simubench assets")
    return {str(row.get("serial")): row for row in rows if isinstance(row, dict)}


def check_register(register: dict[str, dict[str, Any]], serial: str, what: str) -> dict[str, Any]:
    row = register.get(serial)
    if row is None:
        raise RuntimeError(f"{what} {serial}: not in the calibration register")
    if row.get("status") != "in_use":
        raise RuntimeError(f"{what} {serial}: status is {row.get('status')!r}")
    due_in = row.get("due_in_days")
    if due_in is None or int(due_in) < 0:
        raise RuntimeError(f"{what} {serial}: calibration expired ({due_in} days)")
    return row


def measure(cal: Any, dut: Any, function: str, nominal: float) -> float:
    """Set the reference and read the meter; the caller chose the function and settled the source."""
    if nominal == 0.0:
        cal.write("OUT 0 V")
    elif function == "AC":
        cal.write(f"OUT {nominal:g} V, 1 kHz")
    else:
        cal.write(f"OUT {nominal:g} V")
    cal.write("OPER")  # >= 33 V sends the 5522A back to standby; operate again before measuring
    cal.write("*WAI")  # the calibrator's settling time, as OPC? on the real instrument
    what = "AC" if function == "AC" else "DC"
    return parse_reading(q(dut, f":MEASure:VOLTage:{what}?"))


def limit(function: str, nominal: float) -> float:
    if function == "AC":
        return AC_LIMIT_1V if nominal == 1.0 else AC_LIMIT_1V * abs(nominal)
    return DC_LIMITS[abs(nominal)]


def decide(nominal: float, reading: float, limit_value: float) -> tuple[float, float, str]:
    error = reading - nominal
    low, high = -limit_value, limit_value
    guard = GUARD * limit_value
    if error < low - guard or error > high + guard:
        return low, high, "FAIL"
    if low + guard <= error <= high - guard:
        return low, high, "PASS"
    return low, high, "N/A"


def as_found(cal: Any, dut: Any, function: str, points: tuple[float, ...]) -> list[Point]:
    dut.write(f"CONF:VOLT:{function}")
    if function == "AC":
        cal.write("OUT 1 V, 1 kHz")
    else:
        cal.write("OUT 0 V")
    cal.write("OPER")
    cal.write("*WAI")
    out = []
    for nominal in points:
        reading = measure(cal, dut, function, nominal)
        low, high, decision = decide(nominal, reading, limit(function, nominal))
        out.append(Point("as-found", nominal, reading, low, high, decision))
    return out


def adjust(cal: Any, dut: Any) -> None:
    """The service guide's DC adjustment: unsecure, zero and gain per range, store, secure."""
    dut.write('CAL:SEC:STAT OFF,"AT3446XA"')
    dut.write(":VOLT:DC:RANG:AUTO OFF")
    for range_v in DC_RANGES:
        dut.write(f":VOLT:DC:RANG {range_v:g}")
        cal.write("OUT 0 V")
        cal.write("OPER")
        cal.write("*WAI")
        dut.write("CAL:VAL 0")
        dut.write("CAL")
        check_error(dut, f"zero adjustment at the {range_v:g} V range")
        if range_v >= 1.0:
            cal.write(f"OUT {range_v:g} V")
            cal.write("OPER")
            cal.write("*WAI")
            dut.write(f"CAL:VAL {range_v:g}")
            dut.write("CAL")
            check_error(dut, f"gain adjustment at the {range_v:g} V range")
    dut.write("CAL:STOR")
    dut.write("CAL:SEC:STAT ON")
    dut.write(":VOLT:DC:RANG:AUTO ON")


def as_left(cal: Any, dut: Any, function: str, points: tuple[float, ...]) -> list[Point]:
    dut.write(f"CONF:VOLT:{function}")
    out = []
    for nominal in points:
        reading = measure(cal, dut, function, nominal)
        low, high, decision = decide(nominal, reading, limit(function, nominal))
        out.append(Point("as-left", nominal, reading, low, high, decision))
    return out


def write_report(path: Path, points: list[Point]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["phase", "nominal", "reading", "limit_low", "limit_high", "verdict"])
        for p in points:
            writer.writerow(
                [p.phase, f"{p.nominal:.9g}", f"{p.reading:.9g}", f"{p.low:.9g}", f"{p.high:.9g}", p.decision]
            )


def print_table(points: list[Point]) -> None:
    print(f"{'phase':<9}{'nominal':>10}{'reading':>16}{'error':>14}{'limit':>12}  verdict")
    for p in points:
        print(
            f"{p.phase:<9}{p.nominal:>10.6g}{p.reading:>16.9g}{p.reading - p.nominal:>14.3e}"
            f"{p.high:>12.3e}  {p.decision}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--bench", help="SimuBench bench file: use the in-process backend instead of VISA addresses"
    )
    parser.add_argument(
        "--cal", default="cal", help="calibrator VISA address (or instrument name with --bench)"
    )
    parser.add_argument("--dut", default="dut", help="DUT VISA address (or instrument name with --bench)")
    parser.add_argument(
        "--assets", required=True, help="calibration register JSON (simubench assets --format json)"
    )
    parser.add_argument(
        "--function", choices=["DC", "AC"], default="DC", help="measurement function to calibrate"
    )
    parser.add_argument("--report", required=True, help="CSV report to write")
    args = parser.parse_args(argv)

    register = read_register(Path(args.assets))
    rm = pyvisa.ResourceManager(f"{args.bench}@simubench") if args.bench else pyvisa.ResourceManager()
    try:
        cal = rm.open_resource(args.cal, read_termination="\n", write_termination="\n", timeout=20_000)
        dut = rm.open_resource(args.dut, read_termination="\n", write_termination="\n", timeout=20_000)
        cal_model, cal_serial = ident(cal)
        dut_model, dut_serial = ident(dut)
        if cal_model != "5522A":
            raise RuntimeError(f"the reference is a {cal_model}, not a Fluke 5522A")
        if dut_model != "34465A":
            raise RuntimeError(f"the DUT is a {dut_model}, not a Keysight 34465A")
        check_register(register, cal_serial, "reference")
        dut_asset = check_register(register, dut_serial, "DUT")
        print(
            f"{dut_model} {dut_serial}: calibration due in {dut_asset['due_in_days']} days (bench calendar)"
        )

        points = as_found(cal, dut, args.function, DC_POINTS if args.function == "DC" else AC_POINTS)
        if args.function == "DC":
            adjust(cal, dut)
        points += as_left(cal, dut, args.function, DC_POINTS if args.function == "DC" else AC_POINTS)
        write_report(Path(args.report), points)
        print_table(points)
        failed_found = sum(p.decision == "FAIL" for p in points if p.phase == "as-found")
        failed_left = sum(p.decision == "FAIL" for p in points if p.phase == "as-left")
        print(f"as-found failures: {failed_found}; as-left failures: {failed_left}; report: {args.report}")
        return 0 if failed_left == 0 else 1
    finally:
        rm.close()


if __name__ == "__main__":
    sys.exit(main())
