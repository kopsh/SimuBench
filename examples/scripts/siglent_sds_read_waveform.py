"""Read a waveform from a Siglent SDS oscilloscope, as the SDS Series Programming Guide shows it.

The steps follow the guide's "Read Waveform Data Example" (EN11F): read the WAVEDESC preamble,
then the data in pieces of :WAVeform:MAXPoint? points, and convert with
``volts = code / code_per_div * vdiv - offset`` and ``t = delay - 10 div * tdiv / 2 + i * interval``.
The script runs unchanged against a real SDS2000X Plus or a SimuBench bench:

    python siglent_sds_read_waveform.py TCPIP0::192.168.1.30::INSTR C1
    python siglent_sds_read_waveform.py scope C1 --bench examples/benches/siglent_scope_rc.bench.yaml
"""

from __future__ import annotations

import argparse
import contextlib
import math
import struct

import pyvisa

HORI_NUM = 10
TDIV_ENUM = [
    200e-12, 500e-12, 1e-9, 2e-9, 5e-9, 10e-9, 20e-9, 50e-9, 100e-9, 200e-9, 500e-9,
    1e-6, 2e-6, 5e-6, 10e-6, 20e-6, 50e-6, 100e-6, 200e-6, 500e-6,
    1e-3, 2e-3, 5e-3, 10e-3, 20e-3, 50e-3, 100e-3, 200e-3, 500e-3,
    1, 2, 5, 10, 20, 50, 100, 200, 500, 1000,
]  # fmt: skip


def read_block(sds) -> bytes:
    """A definite-length block (#N<length><data>) and the line feeds after it."""
    head = sds.read_bytes(2)
    length = int(sds.read_bytes(int(head[1:2])))
    data = sds.read_bytes(length)
    sds.read_bytes(1)  # "0A"
    with contextlib.suppress(pyvisa.errors.VisaIOError):
        sds.read_bytes(1)  # the second "0A" of "0A 0A"
    return data


def main_desc(recv: bytes) -> tuple[float, float, float, float, float, float, int]:
    """Waveform parameters from the WAVEDESC block (addresses from the guide's Table 1)."""
    interval = struct.unpack("<f", recv[0xB0:0xB4])[0]
    delay = struct.unpack("<d", recv[0xB4:0xBC])[0]
    tdiv = TDIV_ENUM[struct.unpack("<h", recv[0x144:0x146])[0]]
    probe = struct.unpack("<f", recv[0x148:0x14C])[0]
    vdiv = struct.unpack("<f", recv[0x9C:0xA0])[0] * probe
    offset = struct.unpack("<f", recv[0xA0:0xA4])[0] * probe
    code = struct.unpack("<f", recv[0xA4:0xA8])[0]
    adc_bit = struct.unpack("<h", recv[0xAC:0xAE])[0]
    return vdiv, offset, interval, delay, tdiv, code, adc_bit


def read_waveform(sds, channel: str = "C1") -> tuple[list[float], list[float]]:
    """Times and voltages of the channel's waveform."""
    sds.write(":WAVeform:STARt 0")
    sds.write(f"WAV:SOUR {channel}")
    sds.write("WAV:PREamble?")
    vdiv, ofst, interval, delay, tdiv, code_per_div, adc_bit = main_desc(read_block(sds))
    points = float(sds.query(":ACQuire:POINts?").strip())
    one_piece = float(sds.query(":WAVeform:MAXPoint?").strip())
    read_times = math.ceil(points / one_piece)
    if points > one_piece:
        sds.write(f":WAVeform:POINt {int(one_piece)}")
    sds.write(":WAVeform:WIDTh WORD" if adc_bit > 8 else ":WAVeform:WIDTh BYTE")
    raw = b""
    for i in range(read_times):
        sds.write(f":WAVeform:STARt {int(i * one_piece)}")
        sds.write("WAV:DATA?")
        raw += read_block(sds)
    n = int(points)
    codes = struct.unpack(f"<{n}h", raw[: 2 * n]) if adc_bit > 8 else struct.unpack(f"{n}b", raw[:n])
    volts = [c / code_per_div * vdiv - ofst for c in codes]
    times = [delay - tdiv * HORI_NUM / 2 + i * interval for i in range(n)]
    return times, volts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("resource", help="VISA resource (or instrument name on a SimuBench bench)")
    ap.add_argument("channel", nargs="?", default="C1")
    ap.add_argument("--bench", help="run on a SimuBench bench file instead of a real instrument")
    args = ap.parse_args()
    rm = pyvisa.ResourceManager(f"{args.bench}@simubench" if args.bench else "")
    sds = rm.open_resource(args.resource, read_termination="\n", write_termination="\n", timeout=5000)
    sds.write(":TRIGger:MODE SINGle")
    times, volts = read_waveform(sds, args.channel)
    print(
        f"{len(volts)} points, {times[0]:.6g} s to {times[-1]:.6g} s, {min(volts):.4g} V to {max(volts):.4g} V"
    )


if __name__ == "__main__":
    main()
