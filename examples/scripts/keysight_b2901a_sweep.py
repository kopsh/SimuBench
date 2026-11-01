"""Run a staircase sweep on a Keysight (Agilent) B2901A, the way the B2900 programming flow does.

The flow follows the Command Reference's source/trigger chapters: select the voltage source, set
the current compliance, turn the output on, take a spot reading with MEASure, configure a linear
staircase sweep (source mode SWE, start/stop/points, trigger count = points), start it with
INITiate, wait with *OPC?, and read the measurement array with FETCh:ARRay?. FETCh does not need
the trace buffer to be armed (TRACe:FEED:CONTrol defaults to NEVer), so the same script runs
against a real B2901A or a SimuBench bench:

    python keysight_b2901a_sweep.py TCPIP0::192.168.1.40::INSTR
    python keysight_b2901a_sweep.py smu --bench examples/benches/keysight_b2901a.bench.yaml

The trace reply holds the elements selected by :FORMat:ELEMents:SENSe (VOLTage and CURRent first),
so the parser divides the values by the point count instead of assuming a fixed element list.
"""

from __future__ import annotations

import argparse

import pyvisa

POINTS = 5
START_V = 0.0
STOP_V = 2.0
COMPLIANCE_A = 0.01


def numbers(raw: str) -> list[float]:
    return [float(v) for v in raw.split(",") if v.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("resource", help="VISA resource (or instrument name on a SimuBench bench)")
    ap.add_argument("--bench", help="run on a SimuBench bench file instead of a real instrument")
    args = ap.parse_args()
    rm = pyvisa.ResourceManager(f"{args.bench}@simubench" if args.bench else "")
    smu = rm.open_resource(args.resource, read_termination="\n", write_termination="\n", timeout=10000)

    print(smu.query("*IDN?"))
    smu.write("*RST")
    smu.write(":SOUR:FUNC:MODE VOLT")
    smu.write(f":SENS:CURR:PROT {COMPLIANCE_A}")
    smu.write(":SENS:REM OFF")
    smu.write(":OUTP ON")

    spot_v = float(smu.query(":MEAS:VOLT?"))
    spot_i = float(smu.query(":MEAS:CURR?"))
    print(f"spot: {spot_v:.9g} V, {spot_i:.9g} A")

    smu.write(":SOUR:VOLT:MODE SWE")
    smu.write(f":SOUR:VOLT:STAR {START_V};:SOUR:VOLT:STOP {STOP_V};:SOUR:VOLT:POIN {POINTS}")
    # The B2900 sweeps one point per trigger: the trigger count is the point count.
    smu.write(f":TRIG:COUN {POINTS}")
    smu.write(":INIT")
    smu.query("*OPC?")
    measured = int(smu.query(":SYST:DATA:QUAN?"))
    values = numbers(smu.query(":FETC:ARR?"))
    error = smu.query(":SYST:ERR?")
    smu.write(":OUTP OFF;:SOUR:VOLT:MODE FIX")
    smu.close()
    rm.close()

    per_point = len(values) // measured if measured else 0
    points = [(values[k * per_point], values[k * per_point + 1]) for k in range(measured)]
    print(f"{measured} points")
    for voltage, current in points:
        print(f"{voltage:.9g},{current:.9g}")
    print(error)

    assert error.startswith("+0"), error
    assert measured == POINTS, measured


if __name__ == "__main__":
    main()
