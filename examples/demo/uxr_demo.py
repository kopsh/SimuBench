#!/usr/bin/env python3
"""Demonstrate a Keysight Infiniium UXR1104B: autoscale, measurements, FFT and waveform readback.

The script uses only PyVISA and the commands of the Infiniium UXR Programmer's Guide (11.80.00012):
the same file runs against a real UXR or a SimuBench bench (``--bench``), without SimuBench
imported. On ``examples/demo/uxr.bench.yaml`` two 33622A generators drive the four channels with a
10 MHz sine, a 1 MHz square, a 500 kHz triangle and a 2 MHz pulse:

    python uxr_demo.py scope --bench examples/demo/uxr.bench.yaml --csv ch4.csv
    python uxr_demo.py TCPIP0::192.168.1.61::hislip0::INSTR

The steps (docs/demo-uxr.md):

1. ``*IDN?``, ``*RST``, ``*CLS``, headers off.
2. ``:AUToscale``: the scope finds the channels with a signal, sets their scale and offset, the
   timebase and the edge trigger.
3. A timebase that holds several periods of every channel (1 µs/div), 20 000 points, ``:DIGitize``.
4. Per channel: frequency, amplitude, AC RMS over whole cycles, rise time, duty cycle.
5. An FFT of the square (``:FUNCtion1:FFTMagnitude``): its odd harmonics and their levels
   relative to the fundamental (1/3 and 1/5 in voltage: -9.5 dB and -14.0 dB).
6. One channel's waveform through ``:WAVeform:DATA?`` (WORD, LSBFirst), scaled by the preamble.
7. ``:SYSTem:ERRor?`` until it answers 0.
"""

from __future__ import annotations

import argparse
import csv
import math
from typing import Any

import numpy as np
import pyvisa

CHANNELS = (1, 2, 3, 4)
#: The measurements of step 4: (column, query); {src} is the channel.
MEASUREMENTS = (
    ("freq_hz", ":MEASure:FREQuency? {src}"),
    ("vamp_v", ":MEASure:VAMPlitude? {src}"),
    ("vrms_ac_v", ":MEASure:VRMS? CYCLe,AC,{src}"),
    ("rise_s", ":MEASure:RISetime? {src}"),
    ("duty_pct", ":MEASure:DUTYcycle? {src}"),
)
#: What the scope answers for a measurement it cannot make (no full period, no edge).
INVALID = 9.99999e37
TIMEBASE = 1e-6  # s/div: 10 µs on screen, five periods of the slowest signal (500 kHz)
POINTS = 20000  # 2 GSa/s over the 10 µs
FFT_LEVEL = -30.0  # dBm: the FFT peaks counted lie above this level (the noise floor is far below)


def errors(scope) -> list[str]:
    """Every error in the queue; :SYSTem:ERRor? answers 0 when it is empty."""
    found = []
    while (code := scope.query(":SYSTem:ERRor?").strip()) not in ("0", "+0"):
        found.append(code)
    return found


def number(text: str) -> float | None:
    """A measurement result; None for the invalid value."""
    value = float(text)
    return None if value >= INVALID * 0.99 else value


def autoscale(scope) -> dict[str, Any]:
    """Step 2: :AUToscale, then what it chose."""
    scope.write(":AUToscale")
    scope.query("*OPC?")
    channels = {}
    for n in CHANNELS:
        channels[n] = {
            "display": scope.query(f":CHANnel{n}:DISPlay?").strip() == "1",
            "scale": float(scope.query(f":CHANnel{n}:SCALe?")),
            "offset": float(scope.query(f":CHANnel{n}:OFFSet?")),
        }
    return {
        "channels": channels,
        "timebase": float(scope.query(":TIMebase:SCALe?")),
        "trigger": scope.query(":TRIGger:EDGE:SOURce?").strip(),
    }


def measure(scope) -> dict[int, dict[str, float | None]]:
    """Steps 3 and 4: a timebase with whole periods of every channel, one acquisition, the table."""
    scope.write(f":TIMebase:SCALe {TIMEBASE:g}")
    scope.write(f":ACQuire:POINts {POINTS}")
    scope.write(":DIGitize")
    return {
        n: {name: number(scope.query(query.format(src=f"CHANnel{n}"))) for name, query in MEASUREMENTS}
        for n in CHANNELS
    }


def fft_peaks(scope, source: str = "CHANnel2", count: int = 3) -> list[tuple[float, float]]:
    """Step 5: the first ``count`` FFT peaks of ``source`` above FFT_LEVEL, low to high frequency,
    as (Hz, dBm)."""
    scope.write(f":FUNCtion1:FFTMagnitude {source}")
    scope.write(":FUNCtion1:DISPlay ON")
    peaks = []
    for p in range(1, count + 1):
        freq = number(scope.query(f":MEASure:FFT:FREQuency? FUNCtion1,{p},{FFT_LEVEL:g}"))
        mag = number(scope.query(f":MEASure:FFT:MAGNitude? FUNCtion1,{p},{FFT_LEVEL:g}"))
        if freq is None or mag is None:
            break
        peaks.append((freq, mag))
    return peaks


def read_waveform(scope, source: str = "CHANnel4") -> tuple[np.ndarray, np.ndarray]:
    """Step 6: times and voltages of ``source`` in the last acquisition, as the guide reads them."""
    scope.write(f":WAVeform:SOURce {source}")
    scope.write(":WAVeform:FORMat WORD")
    scope.write(":WAVeform:BYTeorder LSBFirst")
    pre = scope.query(":WAVeform:PREamble?").strip().split(",")
    xinc, xorg, xref = float(pre[4]), float(pre[5]), float(pre[6])
    yinc, yorg, yref = float(pre[7]), float(pre[8]), float(pre[9])
    codes = np.array(
        scope.query_binary_values(":WAVeform:DATA?", datatype="h", is_big_endian=False, container=np.array)
    )
    volts = (codes - yref) * yinc + yorg
    times = (np.arange(codes.size) - xref) * xinc + xorg
    return times, volts


def run(scope, csv_path: str | None = None) -> dict[str, Any]:
    """The whole demonstration on an open session; prints each step and returns what it read."""
    idn = scope.query("*IDN?").strip()
    print(f"1. {idn}")
    scope.write("*RST")
    scope.write("*CLS")
    scope.write(":SYSTem:HEADer OFF")

    auto = autoscale(scope)
    print(f"2. :AUToscale -> {auto['timebase'] * 1e9:g} ns/div, trigger {auto['trigger']}")
    for n, ch in auto["channels"].items():
        state = (
            f"{ch['scale'] * 1e3:g} mV/div, offset {ch['offset'] * 1e3:+.1f} mV" if ch["display"] else "off"
        )
        print(f"   CH{n}: {state}")

    table = measure(scope)
    print(f"3. {TIMEBASE * 1e6:g} µs/div, {POINTS} points, :DIGitize")
    print("4.      freq        amplitude  AC rms     rise       duty")
    for n, row in table.items():
        cells = [_fmt(row["freq_hz"], "Hz"), _fmt(row["vamp_v"], "V"), _fmt(row["vrms_ac_v"], "V")]
        cells += [_fmt(row["rise_s"], "s"), "—" if row["duty_pct"] is None else f"{row['duty_pct']:.1f} %"]
        print((f"   CH{n}  " + "  ".join(f"{c:<9}" for c in cells)).rstrip())

    peaks = fft_peaks(scope)
    print(f"5. FFT of CH2 (peaks above {FFT_LEVEL:g} dBm)")
    for i, (freq, mag) in enumerate(peaks):
        rel = f"  {mag - peaks[0][1]:+.1f} dB" if i else ""
        print(f"   {_fmt(freq, 'Hz'):<9}  {mag:+.1f} dBm{rel}")

    times, volts = read_waveform(scope)
    print(f"6. CH4: {volts.size} points, {volts.min() * 1e3:.1f} mV to {volts.max() * 1e3:.1f} mV")
    if csv_path:
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            out = csv.writer(fh)
            out.writerow(["time_s", "volts"])
            out.writerows(zip(times.tolist(), volts.tolist(), strict=True))
        print(f"   written to {csv_path}")

    bad = errors(scope)
    print(f"7. :SYSTem:ERRor? {'0, no errors' if not bad else ', '.join(bad)}")
    return {
        "idn": idn,
        "autoscale": auto,
        "measurements": table,
        "fft": peaks,
        "waveform": (times, volts),
        "errors": bad,
    }


def _fmt(value: float | None, unit: str) -> str:
    """An engineering-notation value with an SI prefix; — for an invalid measurement."""
    if value is None:
        return "—"
    if value == 0:
        return f"0 {unit}"
    exp = max(-12, min(9, 3 * math.floor(math.log10(abs(value)) / 3)))
    prefix = {-12: "p", -9: "n", -6: "µ", -3: "m", 0: "", 3: "k", 6: "M", 9: "G"}[exp]
    return f"{value / 10**exp:.4g} {prefix}{unit}"


def main() -> dict[str, Any]:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("resource", help="VISA resource (or instrument name on a SimuBench bench)")
    ap.add_argument("--bench", help="run against this SimuBench bench instead of an instrument")
    ap.add_argument("--csv", help="write CH4's waveform to this CSV file")
    args = ap.parse_args()
    rm = pyvisa.ResourceManager(f"{args.bench}@simubench" if args.bench else "")
    scope = rm.open_resource(args.resource, read_termination="\n", write_termination="\n", timeout=20000)
    try:
        return run(scope, args.csv)
    finally:
        rm.close()


if __name__ == "__main__":
    main()
