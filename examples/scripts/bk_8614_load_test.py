"""Sweep a DC source's load regulation with a B&K Precision 8614 the way the 8600 manual programs it.

The flow follows the Programming Manual's chapter 4 (programming the input): reset, constant
current with the high range, the over-current protection on at a level just above the sweep, the
input on, then a few current steps each read back with MEASure (voltage) and FETCh (current,
power), the input off and the error queue read. The same script runs against a real 8614 or a
SimuBench bench:

    python bk_8614_load_test.py USB0::0xFFFF::0x8614::601400010000000001::INSTR
    python bk_8614_load_test.py load --bench examples/benches/bk-8614.bench.yaml

The bench's source is 48 V behind 0.1 ohm, so each step drops 0.1 V per ampere.
"""

from __future__ import annotations

import argparse

import pyvisa

STEPS_A = [0.0, 5.0, 10.0, 20.0]
OCP_A = 25.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("resource", help="VISA resource (or instrument name on a SimuBench bench)")
    ap.add_argument("--bench", help="run on a SimuBench bench file instead of a real instrument")
    args = ap.parse_args()
    rm = pyvisa.ResourceManager(f"{args.bench}@simubench" if args.bench else "")
    load = rm.open_resource(args.resource, read_termination="\n", write_termination="\n", timeout=5000)

    print(load.query("*IDN?"))
    load.write("*RST")
    load.write("*CLS")
    load.write("FUNC CURR")
    load.write("CURR:RANG MAX")
    load.write("CURR:SLEW MAX")
    load.write(f"CURR:PROT {OCP_A}")
    load.write("CURR:PROT:DEL 0")
    load.write("CURR:PROT:STAT ON")
    load.write(f"CURR {STEPS_A[0]}")
    load.write("INP ON")
    points = []
    for amps in STEPS_A:
        load.write(f"CURR {amps}")
        load.query("MEAS:VOLT?")  # one acquisition after the step: the input settles
        volts = float(load.query("MEAS:VOLT?"))
        current = float(load.query("FETC:CURR?"))
        power = float(load.query("FETC:POW?"))
        points.append((amps, volts, current, power))
        print(f"{amps:6.2f} A set: {volts:8.4f} V, {current:8.4f} A, {power:9.3f} W")
    load.write("INP OFF")
    tripped = int(load.query("STAT:QUES:COND?")) & (1 << 13)
    error = load.query("SYST:ERR?")
    load.close()
    rm.close()
    print(error)

    assert error.startswith("0,"), error
    assert not tripped, "the protection shut the input down"
    for amps, volts, current, power in points:
        assert abs(current - amps) < 0.01, (amps, current)
        assert abs(power - volts * current) < 0.01 * max(1.0, power), (volts, current, power)
    v0 = points[0][1]
    resistance = (v0 - points[-1][1]) / points[-1][2]
    print(f"source resistance: {resistance * 1e3:.1f} mOhm")


if __name__ == "__main__":
    main()
