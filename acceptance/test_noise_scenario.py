"""Scope noise (issue 24, G4 #136): the SDS2104X Plus's noise and ADC, read as a script reads them.

Expectations come from refcalc.noise and the data sheet: 80 uV rms at 500 MHz, white, so
80 uV * sqrt(100 / 500) = 35.8 uV at this model's 100 MHz; 30 codes per division at 8 bits, 120
at 10 bits. The data sheet gives the noise of the sensitive divisions only; above them the
per-division values are the definition's estimate (noise_table, D46): max(35.8 uV, 0.015 v_div)
at the input - 7.5 mV rms at 0.5 V/div. Processing happens on the quantized codes: an average is
the mean of N quantized acquisitions, ERES b a boxcar of 4^b codes, PEAK keeps the largest
excursion of each interval at up to 64 x the record rate.
"""

from __future__ import annotations

import math
import struct

import numpy as np
import pytest
import pyvisa
from refcalc.noise import averaged_codes_sigma, quantized_sigma, sigma_interval

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
    """The average is taken after the quantization (G4/D46): the codes' lsb^2/12 averages down
    with the noise, which is why averaging resolves below one code at all. Read by the scope's
    RMS, which sees the record the average produced - a BYTE transfer would re-round it to the
    sample grid, as the instrument's sample memory would."""
    scope.write(":CHANnel1:SCALe 5E-4;:ACQuire:TYPE AVERage,16")
    lo, hi = sigma_interval(averaged_codes_sigma(SIGMA_IN, 5e-4 / 30, 16), POINTS)
    assert lo <= measure(scope) <= hi


def measure(scope) -> float:
    """One forced acquisition's record, as the scope's RMS sees it (the record's own precision -
    a BYTE transfer would re-round it to the sample grid, as the sample memory would)."""
    scope.write(":TRIGger:MODE FTRIG")
    assert scope.query("*OPC?") == "1"
    return float(scope.query(":MEASure:SIMPle:VALue? RMS"))


@pytest.mark.parametrize(
    ("scale", "sigma_div"),
    [(5e-3, 0.015 * 5e-3), (0.5, 0.015 * 0.5)],  # an estimated division; the data sheet's floor is below
)
def test_the_noise_of_each_division_is_its_table_row(scope, scale, sigma_div):
    """G4/D46: the noise rides the division, not the front end alone - the definition's
    noise_table holds the rms per division and the records match it."""
    scope.write(f":CHANnel1:SCALe {scale}")
    assert_sigma(read_volts(scope), quantized_sigma(sigma_div, scale / 30))


def test_a_quiet_dc_is_measured_once_the_average_resolves_it():
    """G4's headline (D46): 5 mV of DC at 0.5 V/div - 0.3 of a code - is invisible in one
    acquisition (the old model measured 0) and the average of 64 resolves it to 0.2 LSB."""
    rm = pyvisa.ResourceManager(f"{BENCHES / 'siglent_noise.bench.yaml'}@simubench")
    try:
        awg = rm.open_resource("awg", read_termination="\n", write_termination="\n")
        scope = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=30000)
        awg.write("C1:BSWV WVTP,DC,OFST,5E-3")
        awg.write("C1:OUTP ON")
        scope.write(":CHANnel1:SCALe 0.5;:ACQuire:TYPE AVERage,64")
        scope.write(":TRIGger:MODE FTRIG")
        assert scope.query("*OPC?") == "1"  # 64 acquisitions' time
        mean = float(scope.query(":MEASure:SIMPle:VALue? MEAN"))
        assert abs(mean - 5e-3) < 0.2 * 0.5 / 30, mean
        awg.write("C1:OUTP OFF")
    finally:
        rm.close()


def test_high_resolution_gains_the_effective_bits_the_manual_promises(scope):
    """ERES,b smooths 4^b codes (D46): the noise falls by 2^b, b effective bits - at 2.0 bits,
    a quarter of the one-acquisition sigma."""
    lsb = 5e-4 / 30
    sigma = quantized_sigma(SIGMA_IN, lsb)
    scope.write(":CHANnel1:SCALe 5E-4")
    scope.write(":ACQuire:TYPE ERES,2.0")
    lo, hi = sigma_interval(averaged_codes_sigma(SIGMA_IN, lsb, 16), POINTS)
    assert lo <= measure(scope) <= hi
    assert averaged_codes_sigma(SIGMA_IN, lsb, 16) < sigma / 3.5  # two bits, minus the quantization floor


def test_peak_detect_catches_the_pulse_the_record_rate_steps_over():
    """G4/D46: the record rate steps over pulses (500 ns at 1 kHz into 1 us samples - the source's
    harmonic limit sets the width); PEAK examines 64 native codes per record sample and every
    pulse's excursion is in the record."""
    rm = pyvisa.ResourceManager(f"{BENCHES / 'siglent_noise.bench.yaml'}@simubench")
    try:
        awg = rm.open_resource("awg", read_termination="\n", write_termination="\n")
        scope = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=30000)
        awg.write("C1:BSWV WVTP,SQUARE,FRQ,1000,AMP,2,DUTY,0.05")
        awg.write("C1:OUTP ON")
        scope.write(":ACQuire:MDEPth 10k;:TIMebase:SCALe 1E-3;:CHANnel1:SCALe 0.5")

        scope.write(":ACQuire:TYPE NORMal")
        normal = read_volts(scope)
        scope.write(":ACQuire:TYPE PEAK")
        peak = read_volts(scope)
        assert np.sum(normal > 0.5) < np.sum(peak > 0.5), (np.sum(normal > 0.5), np.sum(peak > 0.5))
        assert np.max(peak) > 0.8, np.max(peak)  # the pulse's excursion, kept
        awg.write("C1:OUTP OFF")
    finally:
        rm.close()


def test_the_probe_ratio_scales_the_noise_in_display_volts(scope):
    scope.write(":CHANnel1:PROBe VALue,10;:CHANnel1:SCALe 5E-3")  # 0.5 mV/div at the input
    assert_sigma(read_volts(scope), quantized_sigma(10 * SIGMA_IN, 5e-3 / 30))
