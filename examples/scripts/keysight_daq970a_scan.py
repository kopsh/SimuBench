"""Scan a Keysight DAQ970A's DAQM901A channels the way the Programming Guide's examples do.

The flow follows the guide's CONFigure, READ?, INITiate/FETCh?, R? and DATA:REMove? entries:
``CONF:VOLT:DC 10,DEF,(@101:103)`` configures three channels and redefines the scan list, ``READ?``
scans them once (READ? is INITiate followed by FETCh?, so the readings also stay in reading memory),
an ``INITiate`` of a two-sweep scan fills memory, ``*OPC?`` waits for it, ``R?`` takes the oldest
readings out as a definite-length block and ``DATA:REMove?`` the next ones. ``MEAS:FRES?`` and
``MEAS:CURR:DC?`` measure a 1 kΩ resistor four-wire (channel n paired with n+10) and the current
channel 121, and at the end ``VOLT:DC:IMP:AUTO ON`` switches the 10 V range to >10 GΩ to show
the internal DMM's loading of the bench circuit.

The same script runs against a real DAQ970A over LAN or a SimuBench bench:

    python keysight_daq970a_scan.py TCPIP0::192.168.1.40::inst0::INSTR
    python keysight_daq970a_scan.py daq --bench examples/benches/keysight-daq970a.bench.yaml

On the example bench each voltage channel has a 1 MΩ source, so the internal DMM's 10 MΩ divides it
and the readings are 10/11 of 1 V, 2 V and 4 V; with >10 GΩ they are the settings themselves.
"""

from __future__ import annotations

import argparse
import math

import pyvisa

#: The DAQM901A channels wired on the example bench, and the source voltage on each.
CHANNELS = (101, 102, 103)
SETTINGS = (1.0, 2.0, 4.0)
#: A 1 MΩ source behind the internal DMM's 10 MΩ input reads 10/11 of its open-circuit voltage.
LOADED = tuple(v * 10.0 / 11.0 for v in SETTINGS)
SCAN = f"(@{CHANNELS[0]}:{CHANNELS[-1]})"


def numbers(raw: str) -> list[float]:
    return [float(v) for v in raw.split(",") if v.strip()]


def block(raw: str) -> str:
    """The data of an IEEE 488.2 definite-length block ``#<n><length><data>``."""
    assert raw.startswith("#"), raw
    n = int(raw[1])
    length = int(raw[2 : 2 + n]) if n else 0
    return raw[2 + n : 2 + n + length]


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

        # DC volts on the 10 V range at 1 PLC; CONFigure redefines the scan list.
        daq.write(f"CONF:VOLT:DC 10,DEF,{SCAN}")
        scan_list = block(daq.query("ROUT:SCAN?").strip())
        readings = numbers(daq.query("READ?"))
        print(f"scan {scan_list}: " + ",".join(f"{v:.8g}" for v in readings))

        # Two sweeps into reading memory; R? and DATA:REMove? take the oldest readings out.
        daq.write("TRIG:COUN 2")
        daq.write("INIT")
        print("opc: " + daq.query("*OPC?").strip())
        points = int(daq.query("DATA:POIN?"))
        oldest = numbers(block(daq.query("R? 2").strip()))
        removed = numbers(daq.query("DATA:REM? 3"))
        left = int(daq.query("DATA:POIN?"))
        print(f"memory: points={points} r={len(oldest)} removed={len(removed)} left={left}")

        # A 1 kΩ resistor four-wire on 104 (114 senses) and 5 V through 1 kΩ on current channel 121.
        fres = float(daq.query("MEAS:FRES? 1000,DEF,(@104)"))
        current = float(daq.query("MEAS:CURR:DC? 0.01,DEF,(@121)"))
        print(f"fres: {fres:.6g} ohm  current: {current * 1e3:.6g} mA")

        # >10 GΩ on the 10 V range: the same scan now reads the sources' open-circuit settings.
        daq.write(f"CONF:VOLT:DC 10,DEF,{SCAN}")
        daq.write(f"VOLT:DC:IMP:AUTO ON,{SCAN}")
        high_z = numbers(daq.query("READ?"))
        print("high-z: " + ",".join(f"{v:.8g}" for v in high_z))

        error = daq.query("SYST:ERR?").strip()
        print(error)
    finally:
        daq.close()
        rm.close()

    assert scan_list == "(@101:103)", scan_list
    assert len(readings) == len(CHANNELS), readings
    assert all(math.isclose(a, b, rel_tol=1e-4) for a, b in zip(readings, LOADED, strict=True)), readings
    assert points == 2 * len(CHANNELS), points
    assert oldest == readings[:2], (oldest, readings)
    assert len(removed) == 3 and left == 1, (removed, left)
    assert math.isclose(fres, 1000.0, rel_tol=1e-3), fres
    assert math.isclose(current, 5.0 / (1000.0 + 6.0), rel_tol=2e-2), current
    assert all(math.isclose(a, b, rel_tol=1e-3) for a, b in zip(high_z, SETTINGS, strict=True)), high_z
    assert error.startswith("+0"), error


if __name__ == "__main__":
    main()
