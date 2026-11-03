"""Two-channel spot I-V with a Keysight B2912B, the way the B2900B/BL SCPI Command Reference writes it.

Each channel is set with its numeric suffix (``:SOUR2:VOLT``, ``:SENS2:CURR:PROT``, ``:OUTP2``) and
both are measured by one query with a channel list, ``:MEAS:CURR? (@1,2)``, which answers channel 1
then channel 2 (B2910-90130, Edition 2, "MEASure Subsystem" and "Channel List Parameter"). A
bus-triggered acquisition of both channels follows (``:TRIG1:SOUR BUS``, ``:INIT (@1,2)``, ``*TRG``,
``:FETC:ARR:CURR? (@1,2)``). The script runs unchanged against a real B2912B or a SimuBench bench:

    python keysight_b2912b_two_channel_iv.py TCPIP0::192.168.1.41::INSTR
    python keysight_b2912b_two_channel_iv.py smu --bench examples/benches/keysight-b2912b.bench.yaml
"""

from __future__ import annotations

import argparse

import pyvisa


def values(text: str) -> list[float]:
    return [float(v) for v in text.strip().split(",") if v.strip()]


def setup(smu, compliance: float = 0.01) -> None:
    """Both channels as voltage sources with a current compliance, outputs on."""
    smu.write("*RST")
    for ch in (1, 2):
        smu.write(f":SOUR{ch}:FUNC:MODE VOLT")
        smu.write(f":SOUR{ch}:VOLT 0")
        smu.write(f":SENS{ch}:CURR:PROT {compliance}")
        smu.write(f":OUTP{ch} ON")


def spot_iv(smu, levels: list[float]) -> list[tuple[float, float, float]]:
    """At each level, both channels force it and one ``MEAS:CURR? (@1,2)`` reads both currents."""
    points = []
    for v in levels:
        smu.write(f":SOUR1:VOLT {v}")
        smu.write(f":SOUR2:VOLT {v}")
        i1, i2 = values(smu.query(":MEAS:CURR? (@1,2)"))
        points.append((v, i1, i2))
    return points


def triggered_pair(smu) -> list[float]:
    """Arm both channels on the bus trigger, trigger them together and fetch both currents."""
    smu.write(":TRIG1:SOUR BUS")
    smu.write(":TRIG2:SOUR BUS")
    smu.write(":INIT (@1,2)")
    smu.write("*TRG")
    smu.query("*OPC?")
    return values(smu.query(":FETC:ARR:CURR? (@1,2)"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("resource", help="VISA resource (or instrument name on a SimuBench bench)")
    ap.add_argument("--bench", help="run against this SimuBench bench instead of an instrument")
    args = ap.parse_args()
    rm = pyvisa.ResourceManager(f"{args.bench}@simubench" if args.bench else "")
    smu = rm.open_resource(args.resource, read_termination="\n", write_termination="\n", timeout=10000)
    try:
        print(smu.query("*IDN?").strip())
        setup(smu)
        points = spot_iv(smu, [0.5, 1.0, 2.0])
        pair = triggered_pair(smu)
        for ch in (1, 2):
            smu.write(f":OUTP{ch} OFF")
        error = smu.query(":SYST:ERR?").strip()
    finally:
        smu.close()
        rm.close()

    for v, i1, i2 in points:
        print(
            f"{v:5.2f} V   ch1 {i1 * 1e3:8.4f} mA ({v / i1:8.1f} ohm)   ch2 {i2 * 1e3:8.4f} mA ({v / i2:8.1f} ohm)"
        )
    print(f"bus-triggered pair at 2 V: ch1 {pair[0]:.6e} A, ch2 {pair[1]:.6e} A")
    print(error)
    assert error.startswith("+0"), error


if __name__ == "__main__":
    main()
