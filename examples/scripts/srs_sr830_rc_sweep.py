"""Run an SR830 like a real lock-in program: internal reference, SINE_OUT into an RC low-pass,
one OUTP? magnitude/phase reading per frequency, plus an AUX output/input check.

The flow follows the SR830 manual's programming style (chapter 5): the SR830 is not SCPI, so the
commands are four-letter mnemonics with comma-separated parameters and the queries answer with a
bare number and no command header. The SR830 has only GPIB and RS-232, no LAN: the default GPIB
address is 8 and the resource string is ``GPIB0::8::INSTR`` (use e.g.
``ASRL/dev/ttyUSB0::INSTR`` for RS-232). A SimuBench bench declares that same address in the
instance's ``address:``, and the in-process PyVISA backend maps it to the simulated instrument, so
this script runs unchanged against either:

    python srs_sr830_rc_sweep.py GPIB0::8::INSTR
    python srs_sr830_rc_sweep.py GPIB0::8::INSTR --bench examples/benches/srs_sr830.bench.yaml

The script waits for the low-pass filter the way a real program does — ``time.sleep`` for several
settling times. On a SimuBench bench the sleep advances virtual time (D47), so the numbers do not
depend on how fast the host is.
"""

from __future__ import annotations

import argparse
import time

import pyvisa

#: Reference frequencies of the sweep, Hz (the RC corner with the 50 Ohm source is 1515.76 Hz).
FREQUENCIES = [100.0, 355.9, 1000.0, 1515.76, 3000.0, 10000.0]

#: Amplitude of the internal oscillator, V rms. The 0.5 V range leaves headroom for the ~0.5 V
#: output at low frequency (R is below 90 % of full scale, so AUTO GAIN would choose it too).
AMPLITUDE = 0.5
SENSITIVITY = 25  # SENS index 25 = 500 mV full scale
TIME_CONSTANT = 7  # OFLT index 7 = 30 ms
SLOPE = 3  # OFSL index 3 = 24 dB/oct
SETTLE_S = 0.5  # several settling times of the 30 ms / 4-pole filter (virtual time under pytest)

TIMEOUT_MS = 10000


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("resource", nargs="?", default="GPIB0::8::INSTR", help="VISA resource")
    ap.add_argument("--bench", help="run on a SimuBench bench file instead of a real instrument")
    args = ap.parse_args()
    rm = pyvisa.ResourceManager(f"{args.bench}@simubench" if args.bench else "")
    lia = rm.open_resource(args.resource, read_termination="\n", write_termination="\n", timeout=TIMEOUT_MS)

    print(lia.query("*IDN?"))
    lia.write("*RST")
    lia.write("OUTX 1")  # answers to GPIB (the interface this program uses)
    lia.write("FMOD 1")  # internal reference
    lia.write(f"SLVL {AMPLITUDE}")
    lia.write(f"SENS {SENSITIVITY}")
    lia.write(f"OFLT {TIME_CONSTANT}")
    lia.write(f"OFSL {SLOPE}")
    lia.write("ILIN 0")  # both line notches out
    lia.write("ICPL 0")  # AC coupling

    # A real program waits the filter settling time before trusting the reading.
    time.sleep(SETTLE_S)

    for frequency in FREQUENCIES:
        lia.write(f"FREQ {frequency}")
        time.sleep(SETTLE_S)
        magnitude = float(lia.query("OUTP? 3"))  # R in volts
        theta = float(lia.query("OUTP? 4"))  # theta in degrees
        print(f"{frequency:.2f},{magnitude:.9g},{theta:.6g}")

    # AUX output 2 is wired to AUX input 2 on the bench; the 1 kOhm DAC drives the 1 MOhm ADC.
    lia.write("AUXV 2,1.5")
    aux_out = float(lia.query("AUXV? 2"))
    aux_in = float(lia.query("OAUX? 2"))
    print(f"aux,{aux_out:.6g},{aux_in:.9g}")

    lia.close()
    rm.close()


if __name__ == "__main__":
    main()
