"""Gap-free frequency readings from a Keysight 53230A, the way the 53220A/53230A User's Guide shows it.

The setup is the guide's Figure 3-1 (53220-90001, Edition 3, "53230A continuous, gap-free
measurement"): ``CONFigure:FREQuency``, ``TRIGger:COUNt 1``, ``SAMPle:COUNt <count>``,
``SENSe:FREQuency:MODE CONTinuous``, ``SENSe:FREQuency:GATE:SOURce TIME`` and the gate time. In
continuous mode the edge that closes one reading's gate opens the next one, so the readings follow
each other without dead time, which is what an Allan deviation needs: the script computes it from
the readings. A two-channel time interval follows with the ordinary ``CONFigure`` / ``INITiate`` /
``FETCh?`` flow. The script runs unchanged against a real 53230A or a SimuBench bench:

    python keysight_53230a_gap_free.py TCPIP0::192.168.1.34::INSTR
    python keysight_53230a_gap_free.py cnt --bench examples/benches/keysight-53230a.bench.yaml
"""

from __future__ import annotations

import argparse
import math

import pyvisa


def readings(text: str) -> list[float]:
    """A FETCh? reply: the readings of the trigger x sample count, comma separated."""
    return [float(value) for value in text.strip().split(",") if value.strip()]


def gap_free_frequency(cnt, count: int = 20, gate_time: float = 0.01, channel: int = 1) -> list[float]:
    """``count`` back-to-back frequency readings with ``gate_time`` each (guide Figure 3-1)."""
    cnt.write(f":CONF:FREQ 1E3, 1E-6, (@{channel})")
    cnt.write(":TRIG:COUN 1")  # continuous mode takes one trigger
    cnt.write(f":SAMP:COUN {count}")
    cnt.write(":SENS:FREQ:MODE CONT")
    cnt.write(":SENS:FREQ:GATE:SOUR TIME")
    cnt.write(f":SENS:FREQ:GATE:TIME {gate_time}")
    cnt.write(":INIT")
    cnt.write("*WAI")
    return readings(cnt.query(":FETCh?"))


def allan_deviation(values: list[float]) -> float:
    """The (non-overlapping) Allan deviation of adjacent readings, relative to their mean."""
    mean = sum(values) / len(values)
    diffs = [(b - a) / mean for a, b in zip(values, values[1:], strict=False)]
    return math.sqrt(sum(d * d for d in diffs) / (2 * len(diffs)))


def time_interval(cnt, start: int = 1, stop: int = 2) -> float:
    """Two-channel time interval from the first event on ``start`` to the next on ``stop``."""
    cnt.write(f":CONF:TINT (@{start}),(@{stop})")
    cnt.write(":SAMP:COUN 1")
    cnt.write(":INIT")
    cnt.write("*WAI")
    return readings(cnt.query(":FETCh?"))[0]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("resource", help="VISA resource (or instrument name on a SimuBench bench)")
    ap.add_argument("--bench", help="run against this SimuBench bench instead of an instrument")
    args = ap.parse_args()
    rm = pyvisa.ResourceManager(f"{args.bench}@simubench" if args.bench else "")
    cnt = rm.open_resource(args.resource, read_termination="\n", write_termination="\n", timeout=10000)
    try:
        print(cnt.query("*IDN?").strip())
        cnt.write("*RST")
        freq = gap_free_frequency(cnt)
        mode = cnt.query(":SENS:FREQ:MODE?").strip()
        cnt.write(":SENS:FREQ:MODE AUTO")
        interval = time_interval(cnt)
        error = cnt.query(":SYST:ERR?").strip()
    finally:
        cnt.close()
        rm.close()

    adev = allan_deviation(freq)
    print(f"gap-free ({mode}): {len(freq)} readings, mean {sum(freq) / len(freq):.9f} Hz")
    print(f"Allan deviation at 10 ms: {adev:.3g}")
    print(f"time interval CH1->CH2: {interval * 1e6:.3f} us")
    print(error)

    assert error.startswith("+0"), error
    assert mode == "CONT", mode
    assert all(abs(f - 1000.0) < 0.01 for f in freq), freq
    assert 0.0 < interval < 1e-3, interval


if __name__ == "__main__":
    main()
