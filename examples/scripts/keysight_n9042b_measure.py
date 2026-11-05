"""Measure a CW tone on a Keysight N9042B UXA the way the X-Series SA-mode reference does.

The flow follows the reference's Swept SA chapters: reset (*RST leaves single sweep), set the
window around the tone, let RBW/VBW/sweep time couple automatically, take one sweep and wait for
it with *OPC?, peak-search marker 1, read its frequency and level, turn on the marker counter, and
read trace 1 as ASCII. The same script runs against a real N9042B or a SimuBench bench:

    python keysight_n9042b_measure.py TCPIP0::192.168.1.31::hislip0::INSTR
    python keysight_n9042b_measure.py sa --bench examples/benches/keysight-n9042b.bench.yaml

The bench's SMA100B feeds a 5.8 GHz, -10 dBm carrier into the 50 ohm RF_IN.
"""

from __future__ import annotations

import argparse

import pyvisa

POINTS = 1001
CENTER_HZ = 5.8e9
SPAN_HZ = 1.0e6


def levels(raw: str) -> list[float]:
    return [float(v) for v in raw.split(",") if v.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("resource", help="VISA resource (or instrument name on a SimuBench bench)")
    ap.add_argument("--bench", help="run on a SimuBench bench file instead of a real instrument")
    args = ap.parse_args()
    rm = pyvisa.ResourceManager(f"{args.bench}@simubench" if args.bench else "")
    sa = rm.open_resource(args.resource, read_termination="\n", write_termination="\n", timeout=20000)

    print(sa.query("*IDN?"))
    sa.write("*RST")  # mode preset, single sweep
    sa.write(":INIT:CONT OFF")
    sa.write(f":FREQ:CENT {CENTER_HZ}")
    sa.write(f":FREQ:SPAN {SPAN_HZ}")
    sa.write(":BAND:AUTO ON;:BAND:VID:AUTO ON;:SWE:TIME:AUTO ON")
    sa.write(":DISP:WIND:TRAC:Y:RLEV 0 dBm")
    sa.write(":DET:TRAC1 POS")
    sa.write(":TRAC1:TYPE WRIT")
    sa.write(":FORM:DATA ASC")
    print(f"rbw: {float(sa.query(':BAND?')):.0f} Hz, sweep: {float(sa.query(':SWE:TIME?')) * 1e3:.3f} ms")
    sa.write(":INIT:IMM")
    sa.query("*OPC?")
    sa.write(":CALC:MARK1:MODE POS")
    sa.write(":CALC:MARK1:MAX")
    marker_hz = float(sa.query(":CALC:MARK1:X?"))
    marker_dbm = float(sa.query(":CALC:MARK1:Y?"))
    print(f"marker: {marker_hz:.0f} Hz, {marker_dbm:.3f} dBm")
    sa.write(":CALC:MARK1:FCO ON")
    counter_hz = float(sa.query(":CALC:MARK1:FCO:X?"))
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
    assert -12.0 < marker_dbm < -8.0, marker_dbm


if __name__ == "__main__":
    main()
