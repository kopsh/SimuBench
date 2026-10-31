"""Oscilloscope low-frequency calibration against a source, the way a host program drives the bench.

The host's interface contract (issue 146): identify both instruments by the model field of
``*IDN?``, set them up, then run two flows, each ending in a table:

- amplitude response: the source outputs narrow pulses (duty 0.1 %–5 %, millivolts) at about
  10 kHz–5.5 MHz; the scope reads each waveform back in pieces and measures the pulse amplitude,
  giving a frequency–amplitude response;
- amplitude calibration: one channel over DC levels and AC amplitudes, with one amplitude set in
  VPP, VRMS and dBm in turn — the same wave each way — giving a calibration table.

The script talks plain PyVISA and runs unchanged against real instruments or a SimuBench bench:

    python lowfreq_cal_host.py --calibrator TCPIP0::127.0.0.1::5025::SOCKET \\
                               --scope TCPIP0::127.0.0.1::5026::SOCKET
    python lowfreq_cal_host.py --bench draft.bench.yaml          # in-process

``PERSist 5`` is this host's setup and no template speaks it (issue 146): the scope's definition
must learn it, or the scope must report it per its unknown-command policy — the host checks both
error queues at the end and refuses to pass with an error pending. The beeper request to the
source is a convenience probe: a lenient definition ignores it and the run continues.
"""

from __future__ import annotations

import argparse
import json
import math

import pyvisa

FREQUENCIES = [1e4, 2e4, 5e4, 1e5, 2e5, 5e5, 1e6, 2e6, 5.5e6]
PIECE = 30000  # the readback's largest chunk
DC_LEVELS = [-0.05, -0.025, 0.0, 0.025, 0.05]
AC_AMPLITUDES = [0.02, 0.05, 0.1]
AC_FREQUENCY = 1e3


def open_instrument(rm: pyvisa.ResourceManager, resource: str, model_token: str):
    inst = rm.open_resource(resource, read_termination="\n", write_termination="\n", timeout=20000)
    idn = inst.query("*IDN?").strip()
    fields = idn.split(",")
    if len(fields) < 2 or model_token.upper() not in fields[1].upper():
        raise RuntimeError(f"{resource}: *IDN? {idn!r} is not a {model_token}")
    return inst


def ladder(value: float) -> float:
    """The smallest 1-2-5 number of the same decade order that is ``value`` or a little above."""
    exponent = math.floor(math.log10(value))
    for e in (exponent, exponent + 1):
        for step in (1.0, 2.0, 5.0):
            candidate = step * 10.0**e
            if candidate >= value * (1 - 1e-9):
                return candidate
    raise AssertionError("unreachable")


def duty_for(freq: float) -> float:
    """Pulse duty as a ratio: a high time of about 200 ns, within 0.1 %–5 % (the source's range)."""
    return min(0.05, max(0.001, 200e-9 * freq))


def v_div(amp_vpp: float) -> float:
    """Volts per division: the wave spans about five divisions."""
    return ladder(amp_vpp / 5)


def t_div(freq: float, points: int) -> float:
    """Seconds per division: about two periods on screen, sampling within 20 GSa/s at ``points``."""
    return ladder(max(0.2 / freq, points / 10 / 20e9))


def dbm_of(vrms: float, ohms: float = 50.0) -> float:
    return 10 * math.log10(vrms**2 / ohms * 1000)


def setup(scope, cal, points: int, channel: int = 1, cal_channel: int = 1) -> None:
    for cmd in ("*RST", "*CLS", "SYST:PRES"):
        scope.write(cmd)
    for n in range(1, 5):
        scope.write(f"C{n}:TRACE {'ON' if n == channel else 'OFF'}")
    scope.write("RCLK INTERNAL")
    scope.write("PERSist 5")
    scope.write(f"C{channel}:CPL D50")
    scope.write(f"TRSE Edge,SR,C{channel}")
    scope.write("TRMD AUTO")
    scope.write("ILVD OFF")
    scope.write(f"PACU 1,AMPL,C{channel}")
    scope.write(f"MSIZ {points}")
    cal.write(":SYSTem:CLKSource INTernal")
    cal.write(":SYSTem:BEEPer:STATe OFF")  # a convenience probe; a lenient source ignores it
    for n in range(1, 5):
        cal.write(f":CHANnel{n}:OUTPut 0")
    cal.write(f":CHANnel{cal_channel}:BASE:OFFSet 0")
    cal.write(f":CHANnel{cal_channel}:AMPLitude:UNIT VPP")
    cal.write(f":CHANnel{cal_channel}:LOAD 50")


def read_waveform(scope, channel: int, point: int, piece: int = PIECE) -> list[float]:
    """The host's readback: pieces of ``piece`` points from FP = points read so far."""
    data: list[float] = []
    while len(data) < point:
        scope.write(f"WAVEFORM_SETUP SP,0,NP,{min(piece, point - len(data))},FP,{len(data)},SN,0")
        reply = scope.query(f'C{channel}:INSPECT? "DATA_ARRAY_1",FLOAT')
        if "TCPIP" in reply:
            raise RuntimeError("communication error")
        values = list(map(float, reply.strip().split()[2:-1]))
        if not values:
            raise RuntimeError(f"no samples at FP {len(data)}")
        data += values
    return data[:point]


def amplitude(data: list[float]) -> float:
    """Top minus base: the medians of the samples above and below the middle of the record."""
    hi, lo = max(data), min(data)
    mid = (hi + lo) / 2
    top = sorted(x for x in data if x >= mid)
    base = sorted(x for x in data if x < mid)
    return top[len(top) // 2] - base[len(base) // 2]


def pulse_measure(
    scope, cal, freq: float, amp_vpp: float, duty: float, channel: int = 1, cal_channel: int = 1,
    points: int = 30000,
) -> float:  # fmt: skip
    """One pulse, one single acquisition, the amplitude read off the record."""
    cal.write(f":CHANnel{cal_channel}:BASE:WAVe PULSe")
    cal.write(f":CHANnel{cal_channel}:BASE:DUTY {duty:.6g}")
    cal.write(f":CHANnel{cal_channel}:BASE:AMPLitude {amp_vpp:.6g}")
    cal.write(f":CHANnel{cal_channel}:BASE:FREQuency {freq:.6g}")
    cal.write(f":CHANnel{cal_channel}:OUTPut 1")
    cal.query("*OPC?")  # the two connections are independent: the level must stand before the arming
    scope.write(f"C{channel}:VDIV {v_div(amp_vpp) * 1000:.6g} mV")
    scope.write(f"TDIV {t_div(freq, points):.6g}")
    scope.write(f"C{channel}:OFFSET 0")
    scope.write(f"C{channel}:TRLV 0")
    scope.write("TRMD SINGLE")
    scope.query("*OPC?")
    return amplitude(read_waveform(scope, channel, points))


def dc_measure(
    scope, cal, level_v: float, channel: int = 1, cal_channel: int = 1, points: int = 12345
) -> float:
    """One DC level, read as the mean of a record (a level never triggers: AUTO acquires anyway)."""
    cal.write(f":CHANnel{cal_channel}:BASE:WAVe DC")
    cal.write(f":CHANnel{cal_channel}:BASE:OFFSet {level_v:.6g}")
    cal.write(f":CHANnel{cal_channel}:OUTPut 1")
    cal.query("*OPC?")  # the level must stand before the acquisition
    if level_v:
        scope.write(f"C{channel}:VDIV {v_div(abs(level_v) * 2) * 1000:.6g} mV")
    scope.write(f"C{channel}:OFFSET 0")
    scope.write("TRMD AUTO")
    data = read_waveform(scope, channel, points)
    return sum(data) / len(data)


def response(
    scope, cal, amp_vpp: float = 0.05, channel: int = 1, cal_channel: int = 1, points: int = 30000,
    frequencies: list[float] | None = None,
) -> list[tuple[float, float]]:  # fmt: skip
    """Flow 1: the pulse amplitude at each frequency, as [(frequency_hz, amplitude_vpp), ...]."""
    setup(scope, cal, points, channel, cal_channel)
    out = []
    try:
        for f in frequencies or FREQUENCIES:
            amp = pulse_measure(scope, cal, f, amp_vpp, duty_for(f), channel, cal_channel, points)
            out.append((f, amp))
    finally:
        cal.write(f":CHANnel{cal_channel}:OUTPut 0")
        scope.write("TRMD AUTO")
    return out


def calibrate(scope, cal, channel: int = 1, cal_channel: int = 1, points: int = 12345) -> list[dict]:
    """Flow 2: DC levels and AC amplitudes of one channel; the units cross-checked on the way."""
    rows: list[dict] = []
    c, s = cal_channel, channel
    for level in DC_LEVELS:
        measured = dc_measure(scope, cal, level, channel, cal_channel, points)
        rows.append({"mode": "DC", "set_v": level, "measured_v": measured})
    cal.write(f":CHANnel{c}:BASE:WAVe SQUare")
    cal.write(f":CHANnel{c}:BASE:OFFSet 0")
    cal.write(f":CHANnel{c}:BASE:DUTY 0.5")
    cal.write(f":CHANnel{c}:BASE:FREQuency {AC_FREQUENCY:.6g}")
    scope.write(f"TDIV {t_div(AC_FREQUENCY, points):.6g}")
    scope.write(f"C{s}:TRLV 0")
    # one 0.5 Vpp point in each unit: VPP, VRMS (square: half the Vpp) and its dBm into 50 ohm
    ac = [("VPP", a, a) for a in AC_AMPLITUDES] + [("VRMS", 0.25, 0.5), ("DBM", dbm_of(0.25), 0.5)]
    for unit, value, peak in ac:
        cal.write(f":CHANnel{c}:AMPLitude:UNIT {unit}")
        cal.write(f":CHANnel{c}:BASE:AMPLitude {value:.6g}")
        cal.query("*OPC?")  # the level must stand before the arming
        scope.write(f"C{s}:VDIV {v_div(peak) * 1000:.6g} mV")
        scope.write("TRMD SINGLE")  # per row: SINGLE acquires at once, so arm it after the level
        scope.query("*OPC?")
        measured = amplitude(read_waveform(scope, s, points))
        rows.append({"mode": "AC", "unit": unit, "set": value, "measured_vpp": measured})
    return rows


def run(
    scope, cal, amp_vpp: float = 0.05, channel: int = 1, cal_channel: int = 1, points: int = 30000,
    frequencies: list[float] | None = None,
) -> tuple[list[tuple[float, float]], list[dict]]:  # fmt: skip
    """Initialization, both flows, wrap-up: the response and the calibration table."""
    setup(scope, cal, points, channel, cal_channel)
    try:
        resp = [
            (f, pulse_measure(scope, cal, f, amp_vpp, duty_for(f), channel, cal_channel, points))
            for f in frequencies or FREQUENCIES
        ]
        table = calibrate(scope, cal, channel, cal_channel, min(points, 12345))
    finally:
        cal.write(f":CHANnel{cal_channel}:OUTPut 0")
        scope.write("TRMD AUTO")
        for inst, query in ((scope, "SYST:ERR?"), (cal, ":SYSTem:ERRor?")):
            err = inst.query(query).strip()
            if not err.startswith("0"):
                raise RuntimeError(f"{query} says {err}")
    return resp, table


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scope", default="scope", help="VISA resource of the reference scope")
    ap.add_argument("--calibrator", default="cal", help="VISA resource of the calibration source")
    ap.add_argument("--bench", help="run in-process on this SimuBench bench (resources are instrument names)")
    ap.add_argument("--scope-model", default="LFCAL-REFSCOPE", help="model token *IDN? must contain")
    ap.add_argument("--calibrator-model", default="LFCAL-CALIBRATOR", help="model token *IDN? must contain")
    ap.add_argument("--amplitude", type=float, default=0.05, help="pulse amplitude in Vpp into 50 Ohm")
    ap.add_argument("--points", type=int, default=30000)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rm = pyvisa.ResourceManager(f"{args.bench}@simubench") if args.bench else pyvisa.ResourceManager()
    try:
        scope = open_instrument(rm, args.scope, args.scope_model)
        cal = open_instrument(rm, args.calibrator, args.calibrator_model)
        resp, table = run(scope, cal, amp_vpp=args.amplitude, points=args.points)
    finally:
        rm.close()
    if args.json:
        print(json.dumps({"response": resp, "calibration": table}))
        return
    print(f"{'frequency':>12}  {'amplitude':>12}  {'relative':>9}")
    for f, a in resp:
        print(f"{f:>10.0f} Hz  {a * 1000:>10.3f} mV  {a / args.amplitude:>9.5f}")
    print("calibration:")
    for row in table:
        print(f"  {row}")


if __name__ == "__main__":
    main()
