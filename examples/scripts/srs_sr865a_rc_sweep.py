"""Run an SR865A like a real lock-in program: internal reference, SINE_OUT into an RC low-pass,
one SNAP? reading of R and theta per frequency, auto phase, and an AUX output/input check.

The flow follows the SR865A Operation Manual (Revision 2.11, chapter 4 Programming): mnemonics with
comma-separated parameters, enumerations written as words (``ICPL AC``) or indexes, numbers with
units (``FREQ 1 KHZ``), and ``SNAP?`` for values taken at one instant. The SR865A has LAN, USB and
GPIB; this script uses the LAN VISA resource. A SimuBench bench declares that same address in the
instance's ``address:``, so the script runs unchanged against either:

    python srs_sr865a_rc_sweep.py TCPIP0::192.168.1.50::INSTR
    python srs_sr865a_rc_sweep.py TCPIP0::192.168.1.50::INSTR --bench examples/benches/srs-sr865a.bench.yaml

The script waits for the low-pass filter the way a real program does, with ``time.sleep``; on a
SimuBench bench the sleep advances virtual time (D47).
"""

from __future__ import annotations

import argparse
import time

import pyvisa

#: Reference frequencies of the sweep, Hz (the RC corner with the 50 Ohm source is 1515.76 Hz).
FREQUENCIES = [100.0, 355.9, 1000.0, 1515.76, 3000.0, 10000.0]

AMPLITUDE = "500 MV"  # SLVL with a unit: 0.5 V rms
SENSITIVITY = 1  # SCAL 1 = 500 mV full scale
TIME_CONSTANT = 9  # OFLT 9 = 30 ms
SLOPE = 3  # OFSL 3 = 24 dB/oct
SETTLE_S = 0.5  # several settling times of the 30 ms / 4-pole filter (virtual time under pytest)

TIMEOUT_MS = 10000


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("resource", nargs="?", default="TCPIP0::192.168.1.50::INSTR", help="VISA resource")
    ap.add_argument("--bench", help="run on a SimuBench bench file instead of a real instrument")
    args = ap.parse_args()
    rm = pyvisa.ResourceManager(f"{args.bench}@simubench" if args.bench else "")
    lia = rm.open_resource(args.resource, read_termination="\n", write_termination="\n", timeout=TIMEOUT_MS)

    print(lia.query("*IDN?"))
    lia.write("*RST")
    lia.write("RSRC INT")  # internal reference
    lia.write(f"SLVL {AMPLITUDE}")
    lia.write("IVMD VOLT;ISRC A;ICPL AC;IRNG 1V")
    lia.write(f"SCAL {SENSITIVITY}")
    lia.write(f"OFLT {TIME_CONSTANT}")
    lia.write(f"OFSL {SLOPE}")
    time.sleep(SETTLE_S)

    for frequency in FREQUENCIES:
        lia.write(f"FREQ {frequency}")
        time.sleep(SETTLE_S)
        magnitude, theta = (float(v) for v in lia.query("SNAP? R, THeta").split(","))
        print(f"{frequency:.2f},{magnitude:.9g},{theta:.6g}")

    # Auto phase at the last frequency: the reference phase takes theta, theta settles to 0.
    lia.write("APHS")
    time.sleep(SETTLE_S)
    phase = float(lia.query("PHAS?"))
    theta = float(lia.query("OUTP? THeta"))
    print(f"aphs,{phase:.6g},{theta:.3g}")

    # AUX output 2 (j = 1) is wired to AUX input 2 on the bench.
    lia.write("AUXV 1, 1.5")
    aux_out = float(lia.query("AUXV? 1"))
    aux_in = float(lia.query("OAUX? 1"))
    print(f"aux,{aux_out:.6g},{aux_in:.9g}")

    lia.close()
    rm.close()


if __name__ == "__main__":
    main()
