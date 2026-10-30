"""Scope noise (issue 24): the SDS2104X Plus's input noise and ADC, read the way a script reads them.

Expectations come from refcalc.noise and the data sheet: 80 uV rms at 500 MHz, white, so
80 uV * sqrt(100 / 500) at this model's 100 MHz; 30 codes per division at 8 bits, 120 at 10 bits.
Intervals follow the sample count (architecture.md: statistics are checked against intervals).
"""

from __future__ import annotations

import math
import struct

import numpy as np
import pytest
import pyvisa
from refcalc.noise import quantized_sigma, sigma_interval

from .conftest import BENCHES

SIGMA_IN = 80e-6 * math.sqrt(100 / 500)
POINTS = 100_000


def read_block(scope) -> bytes:
    """A #N block and the line feeds after it (the payload may hold 0x0A bytes)."""
    head = scope.read_bytes(2)
    data = scope.read_bytes(int(scope.read_bytes(int(head[1:2]))))
    scope.read_bytes(2)  # "0A 0A"
    return data


def read_volts(scope, step=None) -> np.ndarray:
    """One single acquisition of C1 in volts, by the guide's procedure (WAVEDESC + codes).

    The forced frame takes its record time (completion: pending): wait for it with *OPC? before
    reading, or on the stepped clock, where only stepping moves time, ``step`` past it."""
    scope.write(":TRIGger:MODE FTRIG;:WAVeform:SOURce C1;:WAVeform:STARt 0")
    if step is None:
        assert scope.query("*OPC?") == "1"
    else:
        step(0.02)  # 10 divisions of 1 ms and the overhead
    scope.write(":WAVeform:PREamble?")
    desc = read_block(scope)
    vdiv, ofst, per_div = struct.unpack("<fff", desc[156:168])
    probe = struct.unpack("<f", desc[328:332])[0]
    bits = struct.unpack("<h", desc[172:174])[0]
    scope.write(":WAVeform:WIDTh " + ("WORD" if bits > 8 else "BYTE"))
    scope.write(":WAVeform:DATA?")
    codes = np.frombuffer(read_block(scope), dtype="<i2" if bits > 8 else "i1").astype(float)
    return codes / per_div * vdiv * probe - ofst * probe


@pytest.fixture
def scope():
    rm = pyvisa.ResourceManager(f"{BENCHES / 'siglent_noise.bench.yaml'}@simubench")
    inst = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=5000)
    inst.write("*RST;:TIMebase:SCALe 1E-3;:ACQuire:MDEPth 100k")
    yield inst
    rm.close()


def assert_sigma(v: np.ndarray, expected: float) -> None:
    lo, hi = sigma_interval(expected, v.size)
    assert lo <= float(np.std(v)) <= hi, (float(np.std(v)), expected)


@pytest.mark.parametrize(
    ("scale", "resolution", "codes"), [(5e-4, "8Bits", 30), (5e-4, "10Bits", 120), (1e-3, "8Bits", 30)]
)
def test_noise_matches_the_data_sheet_and_the_adc(scope, scale, resolution, codes):
    scope.write(f":CHANnel1:SCALe {scale};:ACQuire:RESolution {resolution}")
    v = read_volts(scope)
    assert v.size == POINTS
    assert abs(float(np.mean(v))) < 5 * SIGMA_IN / math.sqrt(POINTS) + scale / codes / 2
    assert_sigma(v, quantized_sigma(SIGMA_IN, scale / codes))


def test_each_acquisition_differs(scope):
    scope.write(":CHANnel1:SCALe 5E-4")
    first, second = read_volts(scope), read_volts(scope)
    assert not np.array_equal(first, second)  # noise moves with virtual time


def test_same_seed_and_virtual_times_repeat_the_noise(monkeypatch):
    """Noise is a function of the seed and virtual time, not of the run. On the stepped clock the
    virtual instants are exactly the ones asked for (the command clock also counts real idle time,
    which differs from run to run; its runs are reproduced by replaying their records)."""
    monkeypatch.setenv("SIMUBENCH_CLOCK", "stepped")

    def run() -> list[np.ndarray]:
        rm = pyvisa.ResourceManager(f"{BENCHES / 'siglent_noise.bench.yaml'}@simubench")
        try:
            inst = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=5000)
            inst.write("*RST;:TIMebase:SCALe 1E-3;:ACQuire:MDEPth 100k;:CHANnel1:SCALe 5E-4")
            step = rm.visalib.bench.advance
            out = [read_volts(inst, step)]
            step(1e-3)
            out.append(read_volts(inst, step))
            return out
        finally:
            rm.close()

    a, b = run(), run()
    assert not np.array_equal(a[0], a[1])
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])


def test_averaging_lowers_the_noise_by_sqrt_n(scope):
    scope.write(":CHANnel1:SCALe 5E-4;:ACQuire:TYPE AVERage,16")
    assert_sigma(read_volts(scope), quantized_sigma(SIGMA_IN, 5e-4 / 30, averages=16))


def test_the_probe_ratio_scales_the_noise_in_display_volts(scope):
    scope.write(":CHANnel1:PROBe VALue,10;:CHANnel1:SCALe 5E-3")  # 0.5 mV/div at the input
    assert_sigma(read_volts(scope), quantized_sigma(10 * SIGMA_IN, 5e-3 / 30))
