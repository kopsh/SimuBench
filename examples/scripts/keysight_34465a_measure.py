"""Measure DC voltage with a Keysight 34465A, as the Truevolt series programming examples show it.

The flow follows the manual's triggered-measurement example (SCPI Programming Reference >
"Using CONFigure"): configure the function, range and resolution, select the bus trigger, place
the instrument in the wait-for-trigger state with INITiate, deliver *TRG once per trigger count,
and read the readings out of reading memory with R?. It runs unchanged against a real 34465A or
a SimuBench bench:

    python keysight_34465a_measure.py TCPIP0::192.168.1.31::INSTR
    python keysight_34465a_measure.py dut --bench examples/benches/keysight-34465a.bench.yaml
"""

from __future__ import annotations

import argparse

import pyvisa

TRIGGERS = 4
SAMPLES = 5


def parse_block(raw: str) -> list[float]:
    """The readings of a definite-length block (#N<length><data>) as R? returns it."""
    assert raw.startswith("#"), raw
    digits = int(raw[1])
    length = int(raw[2 : 2 + digits])
    return [float(v) for v in raw[2 + digits : 2 + digits + length].split(",")]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("resource", help="VISA resource (or instrument name on a SimuBench bench)")
    ap.add_argument("--bench", help="run on a SimuBench bench file instead of a real instrument")
    args = ap.parse_args()
    rm = pyvisa.ResourceManager(f"{args.bench}@simubench" if args.bench else "")
    dmm = rm.open_resource(args.resource, read_termination="\n", write_termination="\n", timeout=5000)

    print(dmm.query("*IDN?"))
    dmm.write("*RST")
    dmm.write(":CONF:VOLT:DC 10, 0.001")
    dmm.write(f":TRIG:SOUR BUS;:TRIG:COUN {TRIGGERS}")
    dmm.write(f":SAMP:COUN {SAMPLES}")
    dmm.write(":INIT")
    for _ in range(TRIGGERS):
        dmm.write("*TRG")
        dmm.query("*OPC?")  # this trigger's readings are done
    points = int(dmm.query(":DATA:POINts?"))
    readings = parse_block(dmm.query("R?"))
    err = dmm.query(":SYST:ERR?")
    dmm.write(":TRIG:SOUR IMM;:TRIG:COUN 1;:SAMP:COUN 1")
    dmm.close()
    rm.close()

    print(f"{points} points requested, {len(readings)} readings: {readings[0]} ... {readings[-1]}")
    print(err)
    assert points == TRIGGERS * SAMPLES and len(readings) == points
    assert err.startswith("+0")


if __name__ == "__main__":
    main()
