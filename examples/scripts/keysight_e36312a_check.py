"""Check a Keysight E36312A's three outputs the way the E36300 programming flow does.

The flow follows the Programming Guide's channel-list and MEASure sections: select nothing (the
commands address channels with ``(@n)``), program each output's voltage and current limit, switch
the outputs on, wait for the output relays with ``*WAI``, read voltage and current back with
``MEASure`` and compute power as V x I (the manual has no power query). It then exercises the
channel list (``VOLT ... ,(@1,2)`` / ``VOLT? (@1:2)``), OVP (set a level below the output voltage,
see the trip, read error 729, raise the level, clear and see the output restored) and the error
queue. The same script runs against a real E36312A or a SimuBench bench:

    python keysight_e36312a_check.py TCPIP0::192.168.1.30::INSTR
    python keysight_e36312a_check.py psu --bench examples/benches/keysight_e36312a.bench.yaml

On the example bench CH1 sees 10 ohm (5 V / 0.5 A), CH2 and CH3 100 ohm (3.3 V / 12 V).
"""

from __future__ import annotations

import argparse
import math

import pyvisa

#: (channel, voltage, current limit) programmed on the example bench.
OUTPUTS = ((1, 5.0, 1.0), (2, 3.3, 0.5), (3, 12.0, 0.5))
OVP_LEVEL = 4.0
OVP_SAFE_LEVEL = 6.0


def numbers(raw: str) -> list[float]:
    return [float(v) for v in raw.split(",") if v.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("resource", help="VISA resource (or instrument name on a SimuBench bench)")
    ap.add_argument("--bench", help="run on a SimuBench bench file instead of a real instrument")
    args = ap.parse_args()
    rm = pyvisa.ResourceManager(f"{args.bench}@simubench" if args.bench else "")
    psu = rm.open_resource(args.resource, read_termination="\n", write_termination="\n", timeout=10000)

    print(psu.query("*IDN?"))
    psu.write("*RST")
    psu.write("*CLS")
    for channel, voltage, current in OUTPUTS:
        psu.write(f"VOLT {voltage},(@{channel})")
        psu.write(f"CURR {current},(@{channel})")
    psu.write("OUTP ON,(@1:3)")
    # The output relays close 10 ms after the output is switched on; *WAI advances virtual time.
    psu.query("*OPC?")

    voltages = numbers(psu.query("MEAS:VOLT? (@1:3)"))
    currents = numbers(psu.query("MEAS:CURR? (@1:3)"))
    for channel, voltage, current in zip((1, 2, 3), voltages, currents, strict=True):
        print(f"channel {channel}: {voltage:.7g} V, {current:.7g} A, {voltage * current:.7g} W")

    # A channel list writes each channel once and answers in the list's order.
    psu.write("VOLT 1.5,(@1,2)")
    listed = numbers(psu.query("VOLT? (@1:2)"))
    print("list: " + ",".join(f"{v:.8g}" for v in listed))

    # OVP: 5 V into a 4 V protection level trips the output; raising the level and clearing the
    # latch restores the output state that existed before the fault.
    psu.write("VOLT 5,(@1);:VOLT:PROT 4,(@1);:OUTP ON,(@1);*WAI")
    trip = int(psu.query("VOLT:PROT:TRIP?"))
    output = int(psu.query("OUTP? (@1)"))
    print(f"ovp: trip={trip} output={output}")
    psu.write("OUTP ON,(@1)")  # refused with 729 while the fault stands
    print("ovp error: " + psu.query("SYST:ERR?"))
    psu.write(f"VOLT:PROT {OVP_SAFE_LEVEL},(@1);:VOLT:PROT:CLE (@1)")
    recovered = int(psu.query("OUTP? (@1)"))
    print(f"recovered output={recovered}")
    psu.write("OUTP OFF,(@1:3)")
    error = psu.query("SYST:ERR?")
    print(error)
    psu.close()
    rm.close()

    assert error.startswith("+0"), error
    assert trip == 1 and output == 0 and recovered == 1, (trip, output, recovered)
    assert listed == [1.5, 1.5], listed
    assert len(voltages) == len(currents) == 3
    assert math.isclose(voltages[0], 5.0, rel_tol=1e-3)
    assert math.isclose(currents[0], 0.5, rel_tol=1e-3)


if __name__ == "__main__":
    main()
