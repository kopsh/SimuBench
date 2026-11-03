"""Program and check two outputs of a Keysight N6705C, as the N6700 programming examples do it.

Every command carries its channel list (the N6700 family requires one): set voltage and current,
enable over-current protection on channel 3, switch the outputs on, wait with *OPC?, read back
with MEASure. Channel 1 (N6762A) drives 10 ohm in constant voltage; channel 3 (N6752A) drives 1 ohm
with a 2 A setting, so it goes into constant current and its protection trips the output. It runs
unchanged against a real N6705C or a SimuBench bench:

    python keysight_n6705c_check.py TCPIP0::192.168.1.40::INSTR
    python keysight_n6705c_check.py psu --bench examples/benches/keysight-n6705c.bench.yaml
"""

from __future__ import annotations

import argparse

import pyvisa


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("resource", help="VISA resource (or instrument name on a SimuBench bench)")
    ap.add_argument("--bench", help="run on a SimuBench bench file instead of a real instrument")
    args = ap.parse_args()
    rm = pyvisa.ResourceManager(f"{args.bench}@simubench" if args.bench else "")
    psu = rm.open_resource(args.resource, read_termination="\n", write_termination="\n", timeout=5000)

    print(psu.query("*IDN?"))
    print("modules:", psu.query("SYST:CHAN:MOD? (@1:4)"))
    psu.write("*RST;*CLS")
    psu.write("VOLT 5,(@1,3)")
    psu.write("CURR 1,(@1)")
    psu.write("CURR 2,(@3)")
    psu.write("CURR:PROT:DEL 0,(@3);:CURR:PROT:STAT ON,(@3)")
    psu.write("OUTP ON,(@1,3)")
    psu.query("*OPC?")
    volts = [float(v) for v in psu.query("MEAS:VOLT? (@1,3)").split(",")]
    amps = [float(i) for i in psu.query("MEAS:CURR? (@1,3)").split(",")]
    states = psu.query("OUTP? (@1,3)")
    err = psu.query("SYST:ERR?")
    psu.write("OUTP OFF,(@1:4)")
    psu.close()
    rm.close()

    print(f"channel 1: {volts[0]:.4f} V, {amps[0]:.4f} A")
    print(f"channel 3: {volts[1]:.4f} V, {amps[1]:.4f} A")
    print("output states:", states)
    print(err)
    assert err.startswith("+0"), err
