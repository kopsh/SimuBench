"""Scan a Keysight 34970A's multiplexer channels the way the User's Guide's scanning sections do.

The flow follows the guide's "Scanning" and "Viewing readings stored in memory" sections: set the
internal DMM's integration time and a manual 10 V range on the 34970A's channels, build the scan
list, trigger immediately and read the labels with ``READ?`` (readings go straight to the output
buffer), then ``INITiate`` a two-sweep scan into reading memory, wait with ``*OPC?``, fetch it with
``FETCh?``, count it with ``DATA:POINts?`` and take the oldest readings out with ``DATA:REMove?``.
It closes and opens a relay and, at the end, switches the 10 V range to >10 GΩ with
``INPut:IMPedance:AUTO`` to show the relay's loading of the bench circuit.

The same script runs against a real 34970A over GPIB or a SimuBench bench:

    python keysight_34970a_scan.py GPIB0::9::INSTR
    python keysight_34970a_scan.py daq --bench examples/benches/keysight_34970a.bench.yaml

On the example bench each channel has a 1 MΩ source, so the internal DMM's 10 MΩ divides it and
the readings are 10/11 of 1 V, 2 V and 4 V; with >10 GΩ the readings are the settings themselves.
The 34970A's scan channels power up configured for DC volts (the guide's Factory Reset State), so
the SENSe commands below address their default function; on a unit whose channels were configured
for another function, configure them for DC volts first.
"""

from __future__ import annotations

import argparse
import math

import pyvisa

#: The 34901A channels wired on the example bench, and the source voltage on each.
CHANNELS = (101, 102, 103)
SETTINGS = (1.0, 2.0, 4.0)
#: A 1 MΩ source behind the internal DMM's 10 MΩ input reads 10/11 of its open-circuit voltage.
LOADED = tuple(v * 10.0 / 11.0 for v in SETTINGS)


def numbers(raw: str) -> list[float]:
    return [float(v) for v in raw.split(",") if v.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("resource", help="VISA resource (or instrument name on a SimuBench bench)")
    ap.add_argument("--bench", help="run on a SimuBench bench file instead of a real instrument")
    args = ap.parse_args()
    rm = pyvisa.ResourceManager(f"{args.bench}@simubench" if args.bench else "")
    daq = rm.open_resource(args.resource, read_termination="\n", write_termination="\n", timeout=30000)

    try:
        print(daq.query("*IDN?").strip())
        daq.write("*RST")
        daq.write("*CLS")

        # The internal DMM measures the analog bus: 1 PLC, manual 10 V range on every channel.
        for channel in CHANNELS:
            daq.write(f"SENS:VOLT:DC:NPLC 1,(@{channel})")
            daq.write(f"SENS:VOLT:DC:RANG 10,(@{channel})")
        daq.write(f"SENS:VOLT:DC:RANG:AUTO OFF,(@{CHANNELS[0]}:{CHANNELS[-1]})")

        # One sweep with READ?: the readings are not stored in memory (guide p.122).
        daq.write(f"ROUT:SCAN (@{CHANNELS[0]}:{CHANNELS[-1]})")
        daq.write("TRIG:SOUR IMM")
        daq.write("TRIG:COUN 1")
        readings = numbers(daq.query("READ?"))
        print("scan: " + ",".join(f"{v:.8g}" for v in readings))

        # Two sweeps into reading memory, fetched without clearing it; DATA:REMove? takes out.
        daq.write("TRIG:COUN 2")
        daq.write("INIT")
        print("opc: " + daq.query("*OPC?").strip())
        stored = numbers(daq.query("FETC?"))
        points = int(daq.query("DATA:POIN?"))
        removed = numbers(daq.query("DATA:REM? 3"))
        left = int(daq.query("DATA:POIN?"))
        print(f"memory: stored={len(stored)} points={points} removed={len(removed)} left={left}")

        # The relay is a switch in the bench circuit: closing 101 loads its source.
        daq.write("ROUT:CLOS (@101)")
        closed = daq.query("ROUT:CLOS? (@101,102)").strip()
        daq.write("ROUT:OPEN (@101)")
        opened = daq.query("ROUT:CLOS? (@101)").strip()
        print(f"relays: closed={closed} opened={opened}")

        # >10 GΩ on the 10 V range: the same scan now reads the sources' open-circuit settings.
        daq.write(f"INP:IMP:AUTO ON,(@{CHANNELS[0]}:{CHANNELS[-1]})")
        high_z = numbers(daq.query("READ?"))
        print("high-z: " + ",".join(f"{v:.8g}" for v in high_z))

        error = daq.query("SYST:ERR?").strip()
        print(error)
    finally:
        daq.close()
        rm.close()

    assert len(readings) == len(CHANNELS), readings
    assert all(math.isclose(a, b, rel_tol=1e-4) for a, b in zip(readings, LOADED, strict=True)), readings
    assert stored[: len(CHANNELS)] == readings, (stored, readings)
    assert points == 2 * len(CHANNELS), points
    assert len(removed) == len(CHANNELS), removed
    assert left == len(CHANNELS), left
    assert closed == "1,0" and opened == "0", (closed, opened)
    assert len(high_z) == 2 * len(CHANNELS), high_z
    assert all(
        math.isclose(a, b, rel_tol=1e-3) for a, b in zip(high_z[: len(CHANNELS)], SETTINGS, strict=True)
    ), high_z
    assert error.startswith("+0"), error


if __name__ == "__main__":
    main()
