"""Source DC and AC voltage points with a Fluke 5730A and read them with a Fluke 8588A.

The 5730A side follows its Operators Manual (chapter 6): OUT <value> V[, <freq> HZ] sets the output,
OPER switches it to the terminals, *OPC? waits until it has settled, FAULT? reads the fault queue
(0 when empty), STBY returns to standby. The 8588A reads each point with CONFigure + READ?. It runs
unchanged against the real instruments or a SimuBench bench:

    python fluke_5730a_verify.py GPIB0::6::INSTR GPIB0::7::INSTR
    python fluke_5730a_verify.py cal dmm --bench examples/benches/fluke-5730a.bench.yaml
"""

from __future__ import annotations

import argparse

import pyvisa

POINTS = [
    ("OUT 1 V, 0 HZ", ":CONF:VOLT:DC 10"),
    ("OUT 10 V", ":CONF:VOLT:DC 10"),
    ("OUT -10 V", ":CONF:VOLT:DC 10"),
    ("OUT 1 V, 1 KHZ", ":CONF:VOLT:AC 1"),
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cal", help="VISA resource of the 5730A (or its name on a SimuBench bench)")
    ap.add_argument("dmm", help="VISA resource of the 8588A (or its name on a SimuBench bench)")
    ap.add_argument("--bench", help="run on a SimuBench bench file instead of real instruments")
    args = ap.parse_args()
    rm = pyvisa.ResourceManager(f"{args.bench}@simubench" if args.bench else "")
    cal = rm.open_resource(args.cal, read_termination="\n", write_termination="\n", timeout=20000)
    dmm = rm.open_resource(args.dmm, read_termination="\n", write_termination="\n", timeout=20000)

    print(cal.query("*IDN?"))
    cal.write("*RST")
    dmm.write("*RST")
    results = []
    for out, conf in POINTS:
        cal.write(out)
        cal.write("OPER")
        cal.query("*OPC?")  # settled
        dmm.write(conf)
        reading = float(dmm.query(":READ?"))
        amplitude, unit, freq = cal.query("OUT?").split(",")
        results.append((float(amplitude), unit, float(freq), reading))
    cal.write("STBY")
    fault = cal.query("FAULT?")
    cal.close()
    dmm.close()
    rm.close()

    for amplitude, unit, freq, reading in results:
        print(f"{amplitude:+.6f} {unit} {freq:g} Hz -> {reading:+.9e}")
    print("fault:", fault)
    assert fault == "0", fault
