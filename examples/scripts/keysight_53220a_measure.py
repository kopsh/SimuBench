"""Measure with a Keysight 53220A counter, the way the 53220A/53230A User's Guide shows it.

The flow follows the guide's measurement chapter (53220-90001, Edition 4): ``*RST`` for a known
state, ``CONFigure`` to pick the function, ``SAMPle:COUNt`` for the readings per trigger, then
``INITiate`` / ``*WAI`` / ``FETCh?`` for frequency, ``CONFigure:TINTerval`` for a two-channel time
interval, and ``CONFigure:TOTalize:TIMed`` for a gated count. Statistics are enabled before the
measurements with ``CALCulate:STATe`` and ``CALCulate:AVERage:STATe`` and read with the individual
``CALCulate:AVERage`` queries. The script runs unchanged against a real 53220A or a SimuBench bench:

    python keysight_53220a_measure.py TCPIP0::192.168.1.33::INSTR
    python keysight_53220a_measure.py cnt --bench examples/benches/keysight_53220a.bench.yaml
"""

from __future__ import annotations

import argparse

import pyvisa


def readings(text: str) -> list[float]:
    """A FETCh? reply: the readings of the trigger x sample count, comma separated."""
    return [float(value) for value in text.strip().split(",") if value.strip()]


def frequency(cnt, channel: int = 1, count: int = 5, resolution: float = 1e-6) -> list[float]:
    """``CONFigure`` a 1 kHz frequency measurement and read ``count`` readings out of it.

    ``resolution`` is in hertz; 1e-6 Hz at 1 kHz is the guide's default resolution, the one that
    corresponds to a 0.1 s gate time (Table 5-2).
    """
    cnt.write(f":CONF:FREQ 1E3, {resolution}, (@{channel})")
    cnt.write(f":SAMP:COUN {count}")
    cnt.write(":INIT")
    cnt.write("*WAI")
    return readings(cnt.query(":FETCh?"))


def time_interval(cnt, start: int = 1, stop: int = 2) -> float:
    """Two-channel time interval from the first event on ``start`` to the next on ``stop``."""
    cnt.write(f":CONF:TINT (@{start}),(@{stop})")
    cnt.write(":SAMP:COUN 1")
    cnt.write(":INIT")
    cnt.write("*WAI")
    return readings(cnt.query(":FETCh?"))[0]


def totalize(cnt, gate_time: float = 0.1, channel: int = 1) -> float:
    """Gated totalize: the events on ``channel`` during ``gate_time`` seconds."""
    cnt.write(f":CONF:TOT:TIM {gate_time}, (@{channel})")
    cnt.write(":SAMP:COUN 1")
    cnt.write(":INIT")
    cnt.write("*WAI")
    return readings(cnt.query(":FETCh?"))[0]


def statistics(cnt) -> tuple[float, float, float, float, int]:
    """Enable statistics, take five frequency readings and return mean, sdev, min, max and count."""
    cnt.write(":CALC:STAT ON")
    cnt.write(":CALC:AVER:STAT ON")
    cnt.write(":CALC:AVER:CLE")  # start this set of statistics from zero
    frequency(cnt, count=5)
    count = int(cnt.query(":CALC:AVER:COUN:CURR?"))
    return (
        float(cnt.query(":CALC:AVER:AVER?")),
        float(cnt.query(":CALC:AVER:SDEV?")),
        float(cnt.query(":CALC:AVER:MIN?")),
        float(cnt.query(":CALC:AVER:MAX?")),
        count,
    )


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
        freq = frequency(cnt)
        interval = time_interval(cnt)
        events = totalize(cnt)
        mean, sdev, low, high, n = statistics(cnt)
        error = cnt.query(":SYST:ERR?").strip()
    finally:
        cnt.close()
        rm.close()

    print(f"frequency: {freq[0]:.6f} Hz ... {freq[-1]:.6f} Hz ({len(freq)} readings)")
    print(f"time interval CH1->CH2: {interval * 1e6:.3f} us")
    print(f"gated totalize: {events:.0f} events in 0.1 s")
    print(
        f"statistics: mean {mean:.6f} Hz, std dev {sdev:.3g} Hz, min {low:.6f}, max {high:.6f}, {n} readings"
    )
    print(error)

    assert error.startswith("+0"), error
    assert abs(freq[0] - 1000.0) < 0.1, freq
    assert 0.0 < interval < 1e-3, interval
    assert events >= 95, events
    assert abs(mean - 1000.0) < 0.1 and n >= 5, (mean, n)


if __name__ == "__main__":
    main()
