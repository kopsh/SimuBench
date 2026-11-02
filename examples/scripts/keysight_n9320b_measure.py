"""Measure a tone on a Keysight (Agilent) N9320B the way the guide's marker examples do.

The flow follows the Programmer's Guide's IEEE common commands and the frequency, bandwidth,
sweep, detector, marker and trace chapters: reset, set the 10 MHz window, let RBW/VBW/sweep time
couple automatically, put the reference level at 0 dBm, peak-search the marker, read the marker
frequency and level, the marker frequency counter and the whole trace. The same script runs
against a real N9320B or a SimuBench bench:

    python keysight_n9320b_measure.py TCPIP0::192.168.1.30::INSTR
    python keysight_n9320b_measure.py sa --bench examples/benches/keysight_n9320b.bench.yaml

The bench's SDG2042X feeds 2 Vpp (HiZ setting) into the 50 ohm RF_IN, so the fundamental is about
+3.98 dBm. The trace reply is a comma-separated list of levels in the current amplitude unit.
"""

from __future__ import annotations

import argparse

import pyvisa

POINTS = 1001
CENTER_HZ = 10.0e6
SPAN_HZ = 2.0e6


def levels(raw: str) -> list[float]:
    return [float(v) for v in raw.split(",") if v.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("resource", help="VISA resource (or instrument name on a SimuBench bench)")
    ap.add_argument("--bench", help="run on a SimuBench bench file instead of a real instrument")
    args = ap.parse_args()
    rm = pyvisa.ResourceManager(f"{args.bench}@simubench" if args.bench else "")
    sa = rm.open_resource(args.resource, read_termination="\n", write_termination="\n", timeout=10000)

    print(sa.query("*IDN?"))
    sa.write("*RST")
    sa.write(f":FREQ:CENT {CENTER_HZ}")
    sa.write(f":FREQ:SPAN {SPAN_HZ}")
    sa.write(":BAND:RES:AUTO ON")
    sa.write(":BAND:VID:AUTO ON")
    sa.write(":SWE:TIME:AUTO ON")
    sa.write(":DISP:WIND:TRAC:Y:RLEV 0")
    sa.write(":CALC:MARK:PEAK:THR -30")
    sa.write(":CALC:MARK:PEAK:EXC 6")
    sa.write(":CALC:MARK:MAX")
    marker_hz = float(sa.query(":CALC:MARK:X?"))
    marker_dbm = float(sa.query(":CALC:MARK:Y?"))
    print(f"marker: {marker_hz:.0f} Hz, {marker_dbm:.3f} dBm")
    sa.write(":CALC:MARK:FCO ON")  # the marker counter is off after *RST
    counter_hz = float(sa.query(":CALC:MARK:FCO:X?"))
    print(f"counter: {counter_hz:.0f} Hz")
    trace = levels(sa.query(":TRAC:DATA? TRACE1"))
    print(f"trace: {len(trace)} points, peak {max(trace):.3f} dBm")
    error = sa.query(":SYST:ERR?")
    sa.close()
    rm.close()
    print(error)

    assert error.startswith("+0"), error
    assert len(trace) == POINTS, len(trace)
    assert abs(marker_hz - CENTER_HZ) <= SPAN_HZ / (POINTS - 1), marker_hz
    assert abs(counter_hz - CENTER_HZ) <= 1.0, counter_hz
    assert -2.0 < marker_dbm < 6.0, marker_dbm


if __name__ == "__main__":
    main()
