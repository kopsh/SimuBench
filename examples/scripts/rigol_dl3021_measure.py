"""Measure a RIGOL DL3021 electronic load the way the DL3000 programming guide shows it.

The flow follows the programming guide's examples: check ``*IDN?``, reset, switch the static
function with ``:SOURce:FUNCtion``, set the CC current (and the slew and Von), turn the input on,
and read the input voltage, current and power back with ``:MEASure``. It then switches to CV and
reads the operating point the source allows. The same script runs against a real DL3021 or a
SimuBench bench:

    python rigol_dl3021_measure.py TCPIP0::192.168.1.41::INSTR
    python rigol_dl3021_measure.py dut --bench examples/benches/rigol_dl3021.bench.yaml
"""

from __future__ import annotations

import argparse
import time

import pyvisa

SETTLE_S = 0.05  # the input current is settled long before the 200 ms integration window


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("resource", help="VISA resource (or instrument name on a SimuBench bench)")
    ap.add_argument("--bench", help="run on a SimuBench bench file instead of a real instrument")
    args = ap.parse_args()
    rm = pyvisa.ResourceManager(f"{args.bench}@simubench" if args.bench else "")
    load = rm.open_resource(args.resource, read_termination="\n", write_termination="\n", timeout=5000)

    print(load.query("*IDN?"))
    load.write("*RST")
    load.write(":SOUR:FUNC CURR")
    load.write(":SOUR:CURR:SLEW MAX")  # 1 A/us: the 1 mA setpoint is reached at once
    load.write(":SOUR:CURR:VON 0")
    load.write(":SOUR:CURR 0.001")
    load.write(":SOUR:INP 1")
    time.sleep(SETTLE_S)
    v_cc = float(load.query(":MEASure:VOLTage?"))
    i_cc = float(load.query(":MEASure:CURRent?"))
    p_cc = float(load.query(":MEASure:POWer?"))

    load.write(":SOUR:FUNC VOLT")
    load.write(":SOUR:VOLT 2")
    time.sleep(SETTLE_S)
    v_cv = float(load.query(":MEASure:VOLTage?"))
    i_cv = float(load.query(":MEASure:CURRent?"))
    p_cv = float(load.query(":MEASure:POWer?"))

    load.write(":SOUR:INP 0")
    err = load.query(":SYSTem:ERRor?").strip()
    load.close()
    rm.close()

    print(f"CC 1 mA: {v_cc} V, {i_cc} A, {p_cc} W")
    print(f"CV 2 V:  {v_cv} V, {i_cv} A, {p_cv} W")
    print(err)
    assert i_cc > 0 and v_cc > 1.0 and p_cc > 0
    assert v_cv > 1.0 and i_cv > 0 and p_cv > 0
    assert err.startswith("0,")


if __name__ == "__main__":
    main()
