"""Low-frequency calibration, the way the host program drives the bench: calibrator -> reference scope.

The steps follow the host program's interface contract (docs/lowfreq-cal.md): identify both
instruments by the model field of ``*IDN?``, set them up, then for each frequency let the
calibrator output a square wave, acquire once and read the waveform back in pieces with
``WAVEFORM_SETUP ... FP,<points read>`` and ``C<n>:INSPECT? "DATA_ARRAY_1",FLOAT``, and measure its
amplitude. The script talks plain PyVISA and runs unchanged against real instruments or a bench:

    simubench serve examples/lowfreq-cal/lowfreq_cal.bench.yaml      # prints the addresses
    python lowfreq_cal_demo.py --calibrator TCPIP0::127.0.0.1::5025::SOCKET \\
                               --scope TCPIP0::127.0.0.1::5026::SOCKET
    python lowfreq_cal_demo.py --bench examples/lowfreq-cal/lowfreq_cal.bench.yaml   # in-process
"""

from __future__ import annotations

import argparse
import json

import pyvisa

FREQUENCIES = [1e3, 2e3, 5e3, 1e4, 2e4, 5e4, 1e5, 2e5, 5e5, 1e6]
PERIODS = 10  # periods on screen (ten divisions)


def open_instrument(rm: pyvisa.ResourceManager, resource: str, model_token: str):
    inst = rm.open_resource(resource, read_termination="\n", write_termination="\n", timeout=20000)
    idn = inst.query("*IDN?").strip()
    fields = idn.split(",")
    if len(fields) < 2 or model_token.upper() not in fields[1].upper():
        raise RuntimeError(f"{resource}: *IDN? {idn!r} is not a {model_token}")
    return inst


def setup(scope, cal, channel: int, cal_channel: int, points: int) -> None:
    for cmd in ("*RST", "*CLS", "SYST:PRES"):
        scope.write(cmd)
    for n in range(1, 5):
        scope.write(f"C{n}:TRACE {'ON' if n == channel else 'OFF'}")
    scope.write("RCLK INTERNAL")
    scope.write(f"C{channel}:CPL D50")
    scope.write(f"TRSE Edge,SR,C{channel}")
    scope.write("TRMD AUTO")
    scope.write("ILVD OFF")
    scope.write(f"PACU 1,AMPL,C{channel}")
    scope.write(f"MSIZ {points}")
    cal.write(":SYSTem:CLKSource INTernal")
    cal.write(f":CHANnel{cal_channel}:BASE:WAVe SQUare")
    cal.write(f":CHANnel{cal_channel}:BASE:OFFSet 0")
    cal.write(f":CHANnel{cal_channel}:AMPLitude:UNIT VPP")
    cal.write(f":CHANnel{cal_channel}:BASE:DUTY 0.5")
    cal.write(f":CHANnel{cal_channel}:LOAD 50")


def read_waveform(scope, channel: int, point: int, piece: int = 30000) -> list[float]:
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
    """Top minus base of a square wave: the medians of the samples above and below the middle."""
    hi, lo = max(data), min(data)
    mid = (hi + lo) / 2
    top = sorted(x for x in data if x >= mid)
    base = sorted(x for x in data if x < mid)
    return top[len(top) // 2] - base[len(base) // 2]


def measure(scope, cal, freq: float, amp_vpp: float, channel: int, cal_channel: int, point: int) -> float:
    cal.write(f":CHANnel{cal_channel}:BASE:AMPLitude {amp_vpp}")
    cal.write(f":CHANnel{cal_channel}:BASE:FREQuency {freq}")
    cal.write(f":CHANnel{cal_channel}:OUTPut 1")
    scope.write(f"C{channel}:VDIV {amp_vpp / 6 * 1000:.6g} mV")  # the wave spans six divisions
    scope.write(f"TDIV {PERIODS / freq / 10:.6g}")
    scope.write(f"C{channel}:OFFSET 0")
    scope.write(f"C{channel}:TRLV 0")
    scope.write("TRMD SINGLE")
    scope.query("*OPC?")
    return amplitude(read_waveform(scope, channel, point))


def run(scope, cal, amp_vpp=1.0, channel=1, cal_channel=1, point=30000, frequencies=FREQUENCIES):
    """Initialization, acquisition per frequency, wrap-up. Returns [(frequency, amplitude), ...]."""
    setup(scope, cal, channel, cal_channel, point)
    try:
        return [(f, measure(scope, cal, f, amp_vpp, channel, cal_channel, point)) for f in frequencies]
    finally:
        cal.write(f":CHANnel{cal_channel}:OUTPut 0")
        scope.write("TRMD AUTO")
        err = scope.query("SYST:ERR?").strip()
        if not err.startswith("0"):
            raise RuntimeError(f"scope reports {err}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scope", default="scope", help="VISA resource of the reference scope")
    ap.add_argument("--calibrator", default="calibrator", help="VISA resource of the calibrator")
    ap.add_argument("--bench", help="run in-process on this SimuBench bench (resources are instrument names)")
    ap.add_argument("--scope-model", default="LFCAL-REFSCOPE", help="model token *IDN? must contain")
    ap.add_argument("--calibrator-model", default="LFCAL-CALIBRATOR", help="model token *IDN? must contain")
    ap.add_argument("--amplitude", type=float, default=1.0, help="Vpp into 50 Ohm")
    ap.add_argument("--points", type=int, default=30000)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rm = pyvisa.ResourceManager(f"{args.bench}@simubench") if args.bench else pyvisa.ResourceManager()
    try:
        scope = open_instrument(rm, args.scope, args.scope_model)
        cal = open_instrument(rm, args.calibrator, args.calibrator_model)
        response = run(scope, cal, amp_vpp=args.amplitude, point=args.points)
    finally:
        rm.close()
    if args.json:
        print(json.dumps([{"frequency_hz": f, "amplitude_vpp": a} for f, a in response]))
        return
    print(f"{'frequency':>12}  {'amplitude':>12}  {'relative':>9}")
    for f, a in response:
        print(f"{f:>10.0f} Hz  {a:>10.6f} V  {a / args.amplitude:>9.6f}")


if __name__ == "__main__":
    main()
