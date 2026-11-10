"""Drive a Keysight M8194A the way the User Guide's programming recommendations do, and check it with
a Keysight Infiniium UXR0334B.

The guide (M8194-91020, Remote Programming) says to start from ``*RST``, set the modes and parameters,
enable the outputs and send ``:INIT:IMM`` last. Channel 1 gets an amplitude and an offset
(``:VOLTage1``, ``:VOLTage1:OFFSet``), channel 2 high and low levels (``:VOLTage2:HIGH``, ``:LOW``);
a UXR's 50 ohm inputs read the DC level each channel puts out. No waveform is loaded: the M8194A's
segment memory (``:TRACe``) is not modeled yet, so the outputs are the DC levels only.

The same script runs against the real instruments or a SimuBench bench:

    python keysight_m8194a_levels.py TCPIP0::192.168.1.50::hislip0::INSTR TCPIP0::192.168.1.60::hislip0::INSTR
    python keysight_m8194a_levels.py awg scope --bench examples/benches/keysight-m8194a.bench.yaml
"""

from __future__ import annotations

import argparse
import math

import pyvisa


def dc_level(scope, channel: int) -> float:
    """The scope's mean voltage of one channel, after one acquisition."""
    scope.write(f":DIGitize CHANnel{channel}")
    return float(scope.query(f":MEASure:VAVerage? DISPlay,CHANnel{channel}"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("awg", help="VISA resource of the M8194A (or its name on a SimuBench bench)")
    ap.add_argument("scope", help="VISA resource of the UXR0334B (or its name on the bench)")
    ap.add_argument("--bench", help="run on a SimuBench bench file instead of real instruments")
    args = ap.parse_args()
    rm = pyvisa.ResourceManager(f"{args.bench}@simubench" if args.bench else "")
    awg = rm.open_resource(args.awg, read_termination="\n", write_termination="\n", timeout=10000)
    scope = rm.open_resource(args.scope, read_termination="\n", write_termination="\n", timeout=10000)
    try:
        print(awg.query("*IDN?").strip())
        awg.write("*RST")  # the guide: start programming from the default setting
        awg.write("*CLS")
        scope.write("*RST")
        for ch in (1, 2):
            scope.write(f":CHANnel{ch}:SCALe 0.2")

        awg.write(":INSTrument:DACMode DUAL")
        awg.write(":FREQuency:RASTer 1.2E11")
        awg.write(":VOLTage1 0.4")
        awg.write(":VOLTage1:OFFSet 0.3")
        awg.write(":VOLTage2:HIGH 0.5")
        awg.write(":VOLTage2:LOW 0.1")
        for ch in (1, 2):
            awg.write(f":OUTPut{ch} ON")
        awg.write(":INITiate:IMMediate")  # the guide: start data generation as the last command
        assert awg.query("*OPC?").strip() == "1"
        print("ch1: " + ",".join(awg.query(q).strip() for q in (":VOLTage1?", ":VOLTage1:OFFSet?")))
        print("ch2: " + ",".join(awg.query(q).strip() for q in (":VOLTage2:HIGH?", ":VOLTage2:LOW?")))
        level1 = dc_level(scope, 1)
        level2 = dc_level(scope, 2)
        print(f"scope: {level1:.4g} V, {level2:.4g} V")
        error = awg.query(":SYSTem:ERRor?").strip()
        print(error)
    finally:
        awg.close()
        scope.close()
        rm.close()

    assert math.isclose(level1, 0.3, abs_tol=0.02), level1  # the offset is the mean of a zero table
    assert math.isclose(level2, 0.3, abs_tol=0.02), level2  # (high + low) / 2
    assert error.startswith("0,"), error


if __name__ == "__main__":
    main()
