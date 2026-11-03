"""Verify a calibrator's DC and AC voltage points with a Fluke 8588A, as its programming examples do it.

The 8588A side follows the Remote Programmer's Manual: CONFigure:<function> <range> sets the
function, the range and the trigger system (INIT:CONT OFF, TRIG:SOUR IMM, COUN 1), and READ?
takes and returns the reading. The source is a Fluke 5522A (OUT <value>; OPER; *OPC? waits for the
output to settle). It runs unchanged against the real instruments or a SimuBench bench:

    python fluke_8588a_verify.py GPIB0::7::INSTR GPIB0::4::INSTR
    python fluke_8588a_verify.py dmm cal --bench examples/benches/fluke-8588a.bench.yaml
"""

from __future__ import annotations

import argparse

import pyvisa

# (calibrator output, 8588A configure command): DC on the 10 V and 100 V ranges, then 1 kHz AC.
POINTS = [
    ("OUT 1 V", ":CONF:VOLT:DC 10"),
    ("OUT 10 V", ":CONF:VOLT:DC 10"),
    ("OUT 100 V", ":CONF:VOLT:DC 100"),
    ("OUT 1 V, 1 kHz", ":CONF:VOLT:AC 1"),
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dmm", help="VISA resource of the 8588A (or its name on a SimuBench bench)")
    ap.add_argument("cal", help="VISA resource of the 5522A (or its name on a SimuBench bench)")
    ap.add_argument("--bench", help="run on a SimuBench bench file instead of real instruments")
    args = ap.parse_args()
    rm = pyvisa.ResourceManager(f"{args.bench}@simubench" if args.bench else "")
    dmm = rm.open_resource(args.dmm, read_termination="\n", write_termination="\n", timeout=20000)
    cal = rm.open_resource(args.cal, read_termination="\n", write_termination="\n", timeout=20000)

    print(dmm.query("*IDN?"))
    dmm.write("*RST;*CLS")
    cal.write("*RST;*CLS")
    results = []
    for out, conf in POINTS:
        cal.write(out)
        cal.write("OPER")
        cal.query("*OPC?")  # the output has settled
        dmm.write(conf)
        reading = float(dmm.query(":READ?"))
        nominal = float(out.split()[1])
        results.append((out, reading, (reading - nominal) / nominal * 1e6))
    cal.write("STBY")
    err = dmm.query(":SYST:ERR?")
    dmm.close()
    cal.close()
    rm.close()

    for out, reading, ppm in results:
        print(f"{out:16} {reading:+.9e}  {ppm:+.2f} ppm")
    print(err)
    assert err.startswith("+0"), err
