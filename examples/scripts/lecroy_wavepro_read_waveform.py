"""Read a waveform from a Teledyne LeCroy WavePro 804HD, the way the MAUI Remote Control and
Automation Manual shows it.

The steps follow the manual's waveform transfer: ``C1:INSPECT? "WAVEDESC"`` gives the descriptor
(item ``name: value`` pairs: point count, first point, horizontal interval and offset, vertical
offset and gain, timebase, volts per division), and ``C1:INSPECT? "DATA_ARRAY_1",FLOAT`` gives the
record as text volts, separated by spaces. The script runs unchanged against a real WavePro 804HD
or a SimuBench bench:

    python lecroy_wavepro_read_waveform.py TCPIP0::192.168.1.70::INSTR C1
    python lecroy_wavepro_read_waveform.py scope C1 --bench examples/benches/lecroy-wavepro804hd.bench.yaml
"""

from __future__ import annotations

import argparse

import numpy as np
import pyvisa


def _quoted(reply: str) -> str:
    """The text between the reply's first and last double quote."""
    return reply.split('"', 1)[1].rsplit('"', 1)[0]


def read_descriptor(scope, channel: str = "C1") -> dict[str, float]:
    """The WAVEDESC fields: the manual's ``name: value`` pairs, each value as a number."""
    text = _quoted(scope.query(f'{channel}:INSPECT? "WAVEDESC"').strip())
    tokens = text.split()
    fields: dict[str, float] = {}
    for name, value in zip(tokens[::2], tokens[1::2]):
        if not name.endswith(":"):
            raise ValueError(f"unexpected descriptor item {name!r}")
        fields[name[:-1]] = float(value)
    return fields


def read_waveform(scope, channel: str = "C1") -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
    """Times and voltages of the channel's record, and its descriptor."""
    desc = read_descriptor(scope, channel)
    volts = np.array([float(v) for v in _quoted(scope.query(f'{channel}:INSPECT? "DATA_ARRAY_1",FLOAT')).split()])
    start = desc["HORIZ_OFFSET"] + desc["FIRST_POINT"] * desc["HORIZ_INTERVAL"]
    times = start + np.arange(volts.size) * desc["HORIZ_INTERVAL"]
    return times, volts, desc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("resource", help="VISA resource (or instrument name on a SimuBench bench)")
    ap.add_argument("channel", nargs="?", default="C1")
    ap.add_argument("--bench", help="run against this SimuBench bench instead of an instrument")
    args = ap.parse_args()
    rm = pyvisa.ResourceManager(f"{args.bench}@simubench" if args.bench else "")
    scope = rm.open_resource(args.resource, read_termination="\n", write_termination="\n", timeout=20000)
    try:
        print(scope.query("*IDN?").strip())
        times, volts, desc = read_waveform(scope, args.channel)
        print(
            f"{volts.size} points at {1 / desc['HORIZ_INTERVAL']:.4g} Sa/s, "
            f"{times[0]:.6g} s to {times[-1]:.6g} s, {volts.min():.4g} V to {volts.max():.4g} V"
        )
    finally:
        rm.close()


if __name__ == "__main__":
    main()
