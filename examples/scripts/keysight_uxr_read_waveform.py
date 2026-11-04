"""Read a waveform from a Keysight Infiniium UXR-Series oscilloscope, the way the Programmer's Guide
shows it.

The steps follow the guide's examples (11.60.00000): headers off, :DIGitize the channel, WORD data
with the LSBFirst byte order and streaming on (#0 blocks), the 24 fields of :WAVeform:PREamble?,
:WAVeform:DATA? as signed 16-bit words, ``voltage = (code - yreference) * yincrement + yorigin`` and
``time = (index - xreference) * xincrement + xorigin``, then :SYSTem:ERRor? until it answers 0. The
script runs unchanged against a real UXR0334B or a SimuBench bench:

    python keysight_uxr_read_waveform.py TCPIP0::192.168.1.60::hislip0::INSTR CHANnel1
    python keysight_uxr_read_waveform.py scope CHANnel1 --bench examples/benches/keysight-uxr0334b.bench.yaml
"""

from __future__ import annotations

import argparse

import numpy as np
import pyvisa


def read_preamble(scope) -> dict[str, float | int | str]:
    """The waveform preamble, field by field as the guide's example parses it."""
    pre = scope.query(":WAVeform:PREamble?").strip().split(",")
    return {
        "format": int(pre[0]),
        "type": int(pre[1]),
        "points": int(pre[2]),
        "count": int(pre[3]),
        "xinc": float(pre[4]),
        "xorg": float(pre[5]),
        "xref": float(pre[6]),
        "yinc": float(pre[7]),
        "yorg": float(pre[8]),
        "yref": float(pre[9]),
        "coupling": int(pre[10]),
        "xdisprange": float(pre[11]),
        "xdisporg": float(pre[12]),
        "ydisprange": float(pre[13]),
        "ydisporg": float(pre[14]),
        "date": pre[15],
        "time": pre[16],
        "frame_model": pre[17],
        "acq_mode": int(pre[18]),
        "completion": int(pre[19]),
        "xunits": int(pre[20]),
        "yunits": int(pre[21]),
        "max_bandwidth": float(pre[22]),
        "min_bandwidth": float(pre[23]),
    }


def errors(scope) -> list[int]:
    """Every error in the queue: :SYSTem:ERRor? answers the number alone, 0 when it is empty."""
    found = []
    while (code := int(scope.query(":SYSTem:ERRor?"))) != 0:
        found.append(code)
    return found


def read_waveform(scope, source: str = "CHANnel1") -> tuple[np.ndarray, np.ndarray, dict]:
    """Times and voltages of one acquisition of ``source``, and its preamble."""
    scope.write(":SYSTem:HEADer OFF")
    scope.write(f":DIGitize {source}")
    scope.write(f":WAVeform:SOURce {source}")
    scope.write(":WAVeform:FORMat WORD")
    scope.write(":WAVeform:BYTeorder LSBFirst")
    scope.write(":WAVeform:STReaming ON")  # the guide recommends streaming for new programs (#0 blocks)
    wfm = read_preamble(scope)
    # a #0 block carries no length: tell the reader how many points to expect (the guide's example)
    num_pts = int(scope.query(":WAVeform:POINts?"))
    codes = np.array(
        scope.query_binary_values(
            ":WAVeform:DATA?", datatype="h", is_big_endian=False, container=np.array, data_points=num_pts
        )
    )
    volts = (codes - wfm["yref"]) * wfm["yinc"] + wfm["yorg"]
    times = (np.arange(codes.size) - wfm["xref"]) * wfm["xinc"] + wfm["xorg"]
    return times, volts, wfm


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("resource", help="VISA resource (or instrument name on a SimuBench bench)")
    ap.add_argument("source", nargs="?", default="CHANnel1")
    ap.add_argument("--bench", help="run against this SimuBench bench instead of an instrument")
    args = ap.parse_args()
    rm = pyvisa.ResourceManager(f"{args.bench}@simubench" if args.bench else "")
    scope = rm.open_resource(args.resource, read_termination="\n", write_termination="\n", timeout=20000)
    try:
        print(scope.query("*IDN?").strip())
        times, volts, wfm = read_waveform(scope, args.source)
        print(
            f"{len(volts)} points at {1 / wfm['xinc']:.4g} Sa/s, {times[0]:.6g} s to {times[-1]:.6g} s, "
            f"{volts.min():.4g} V to {volts.max():.4g} V"
        )
        bad = errors(scope)
        if bad:
            print(f"errors: {bad}")
    finally:
        rm.close()


if __name__ == "__main__":
    main()
