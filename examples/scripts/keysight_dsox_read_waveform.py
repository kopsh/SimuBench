"""Read a waveform from a Keysight InfiniiVision 2000 X-Series oscilloscope, the way the
Programmer's Guide shows it.

The steps follow the guide's waveform chapter (02.66.0000): :DIGitize the channel, choose BYTE,
WORD or ASCii data and the points mode, read :WAVeform:PREamble? and :WAVeform:DATA?, and convert
with ``voltage = (code - yreference) * yincrement + yorigin`` and
``time = (index - xreference) * xincrement + xorigin``. The script runs unchanged against a real
DSO-X 2024A or a SimuBench bench:

    python keysight_dsox_read_waveform.py TCPIP0::192.168.1.32::INSTR CHANnel1
    python keysight_dsox_read_waveform.py scope CHANnel1 --bench examples/benches/keysight-dsox2024a.bench.yaml
"""

from __future__ import annotations

import argparse

import numpy as np
import pyvisa


def read_waveform(scope, source: str = "CHANnel1", fmt: str = "BYTE", points: int | str = 1000) -> tuple[np.ndarray, np.ndarray]:
    """Times and voltages of one acquisition of ``source``."""
    scope.write(f":DIGitize {source}")
    scope.write(f":WAVeform:SOURce {source}")
    scope.write(f":WAVeform:FORMat {fmt}")
    scope.write(":WAVeform:UNSigned 1")
    scope.write(":WAVeform:BYTeorder MSBFirst")
    scope.write(":WAVeform:POINts:MODE " + ("NORMal" if points == 1000 else "MAXimum"))
    scope.write(f":WAVeform:POINts {points}")
    pre = scope.query(":WAVeform:PREamble?").strip().split(",")
    _, _, n, _ = (int(x) for x in pre[:4])
    xinc, xorg, xref = float(pre[4]), float(pre[5]), int(pre[6])
    yinc, yorg, yref = float(pre[7]), float(pre[8]), int(pre[9])
    if fmt == "ASCii":
        # ASCii data comes in the same #8 block as binary data: skip the header, split the text.
        raw = scope.query(":WAVeform:DATA?").strip()
        volts = np.array([float(v) for v in raw[2 + int(raw[1]) :].split(",")])
    else:
        dtype = "B" if fmt == "BYTE" else "H"
        codes = np.array(scope.query_binary_values(":WAVeform:DATA?", datatype=dtype, is_big_endian=True))
        volts = (codes - yref) * yinc + yorg
    times = (np.arange(n) - xref) * xinc + xorg
    return times, volts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("resource", help="VISA resource (or instrument name on a SimuBench bench)")
    ap.add_argument("source", nargs="?", default="CHANnel1")
    ap.add_argument("--bench", help="run against this SimuBench bench instead of an instrument")
    args = ap.parse_args()
    rm = pyvisa.ResourceManager(f"{args.bench}@simubench" if args.bench else "")
    scope = rm.open_resource(args.resource, read_termination="\n", write_termination="\n", timeout=10000)
    try:
        times, volts = read_waveform(scope, args.source)
        print(f"{len(volts)} points, {times[0]:.6g} s to {times[-1]:.6g} s, {volts.min():.4g} V to {volts.max():.4g} V")
    finally:
        rm.close()


if __name__ == "__main__":
    main()
