"""Lock-in amplifier scenario (docs/validation-lockin.md), driven through PyVISA as a script would.

Expected values come from refcalc (independent closed forms and numeric integration), with the
physics-correct circuit of D13: SINE_OUT's 50 Ω, the RC and two 10 MΩ inputs solved together.
"""

from __future__ import annotations

import math
import statistics

import numpy as np
import pytest
import pyvisa
from refcalc.circuits import rc_chain
from refcalc.lockin import enbw, settling_multiple, staircase_fundamental, unsettled

from .conftest import BENCHES

R, C = 1e3, 100e-9
LOAD = 5e6  # SIG_IN and the DMM, 10 MΩ each
TABLE = [
    0,
    0.3827,
    0.7071,
    0.9239,
    1,
    0.9239,
    0.7071,
    0.3827,
    0,
    -0.3827,
    -0.7071,
    -0.9239,
    -1,
    -0.9239,
    -0.7071,
    -0.3827,
]


def open_bench(name: str):
    rm = pyvisa.ResourceManager(f"{BENCHES / name}@simubench")
    res = {
        inst: rm.open_resource(inst, read_termination="\n", write_termination="\n")
        for inst in rm.visalib.bench.instruments
    }
    return rm, res, rm.visalib.bench


def snap(lia, *names: str) -> list[float]:
    return [float(v) for v in lia.query(":MEASure:SNAP? " + ",".join(names)).split(",")]


def drain(inst) -> list[str]:
    out = []
    while not (e := inst.query(":SYSTem:ERRor?")).startswith("0,"):
        out.append(e)
    return out


def expected(f: float, drive_rms: float) -> complex:
    """Steady-state phasor at SIG_IN (rms) for SINE_OUT at ``drive_rms`` open circuit."""
    return rc_chain(f, drive_rms, rs=50.0, r=R, c=C, load_out=LOAD)[1]


@pytest.fixture
def rc():
    rm, res, bench = open_bench("lockin_rc.bench.yaml")
    yield res["lia"], res["dmm"], bench
    rm.close()


# ---- 1. settling --------------------------------------------------------------------------------


def test_enbw_and_settling_figures(rc):
    lia, _, _ = rc
    assert float(lia.query(":FILTer:ENBW?")) == pytest.approx(2.6042, abs=1e-4)
    assert float(lia.query(":FILTer:SETTling?")) == pytest.approx(0.3014, abs=1e-4)
    for slope, poles, multiple in ((6, 1, 4.605), (12, 2, 6.638), (18, 3, 8.406), (24, 4, 10.045)):
        lia.write(f":FILTer:SLOPe {slope}")
        settle = float(lia.query(":FILTer:SETTling?"))
        assert settle / 0.03 == pytest.approx(multiple, abs=1e-3)
        assert settle / 0.03 == pytest.approx(settling_multiple(poles), abs=1e-3)
        assert float(lia.query(":FILTer:ENBW?")) == pytest.approx(enbw(poles, 0.03), rel=1e-4)


def test_settling_follows_the_order_n_exponential(rc):
    lia, _, bench = rc
    lia.write(":SOURce:VOLTage 0.5")
    lia.write(":OUTPut:STATe OFF")
    bench.advance(2.0)
    lia.write(":OUTPut:STATe ON")
    final = abs(expected(1591.55, 0.5))
    elapsed = 0.0
    for k in (0, 0.5, 1, 2, 4, 6, 10.045, 14, 20):
        bench.advance(k * 0.03 - elapsed)
        elapsed = k * 0.03
        r = float(lia.query(":MEASure:R?"))
        assert r == pytest.approx(final * (1 - unsettled(4, k)), abs=5e-5), k
    assert r == pytest.approx(final, rel=1e-5)  # 3.2e-6 of the step is left at 20 T
    assert drain(lia) == []


def test_reading_without_waiting_reads_the_filter(rc):
    lia, _, bench = rc
    lia.write(":SOURce:VOLTage 0.5")
    lia.write(":OUTPut:STATe OFF")
    bench.advance(2.0)
    lia.write(":OUTPut:STATe ON")
    assert float(lia.query(":MEASure:R?")) < 1e-6  # zero plus noise at t = 0


# ---- 2. Bode sweep ------------------------------------------------------------------------------

FREQS = (100, 200, 500, 1000, 1591.55, 3000, 6000, 10000, 20000)


def sweep(lia, bench, waits: float) -> tuple[float, float]:
    lia.write(":SOURce:VOLTage 0.5")
    settle = float(lia.query(":FILTer:SETTling?"))
    worst_r = worst_theta = 0.0
    for f in FREQS:
        lia.write(f":REFerence:FREQuency {f}")
        bench.advance(waits * settle)
        r, theta = snap(lia, "R", "THETa")
        h = expected(f, 0.5)
        worst_r = max(worst_r, abs(r / abs(h) - 1))
        worst_theta = max(worst_theta, abs(theta - math.degrees(math.atan2(h.imag, h.real))))
    return worst_r, worst_theta


def test_bode_sweep_two_settling_times(rc):
    lia, _, bench = rc
    worst_r, worst_theta = sweep(lia, bench, 2)
    assert worst_r < 1e-4  # the article: 2.8e-6 plus noise of 8 nV on readings down to 38 mV
    assert worst_theta < 1e-3
    assert lia.query(":SYSTem:ERRor?").startswith("0,")


def test_bode_sweep_one_settling_time_is_not_enough(rc):
    lia, _, bench = rc
    worst_r, worst_theta = sweep(lia, bench, 1)
    assert 1e-3 < worst_r < 2e-2  # 1 % of each step left over
    assert worst_theta > 0.05


def test_bode_table_matches_circuit_theory(rc):
    """The physics-correct table of validation-lockin.md (50 Ω + 1 kΩ, corner 1515.76 Hz)."""
    lia, _, bench = rc
    lia.write(":SOURce:VOLTage 0.5")
    table = {
        100: (498.811, -3.77),
        1000: (417.295, -33.41),
        1515.76: (353.512, -44.99),
        20000: (37.786, -85.67),
    }
    for f, (r_mv, theta) in table.items():
        lia.write(f":REFerence:FREQuency {f}")
        bench.advance(1.0)
        r, th = snap(lia, "R", "THETa")
        assert r * 1e3 == pytest.approx(r_mv, abs=0.01)  # the table is rounded hand arithmetic
        assert th == pytest.approx(theta, abs=0.01)


def test_one_volt_drive_overloads_the_front_end(rc):
    lia, _, bench = rc
    lia.write(":SOURce:VOLTage 1.0")  # 1.41 V peak at 100 Hz, past the 1.4 V limit
    lia.write(":REFerence:FREQuency 100")
    bench.advance(1.0)
    assert lia.query(":STATus:OVERload:INPut?") == "1"
    assert drain(lia)[0] == '325,"Input overload"'


def test_dmm_reads_the_same_net(rc):
    lia, dmm, bench = rc
    lia.write(":SOURce:VOLTage 0.5")
    bench.advance(1.0)
    assert float(dmm.query(":MEASure:VOLTage:AC?")) == pytest.approx(abs(expected(1591.55, 0.5)), rel=1e-6)
    assert float(dmm.query(":MEASure:VOLTage:DC?")) == pytest.approx(0.0, abs=1e-12)


# ---- 3. noise -----------------------------------------------------------------------------------


@pytest.mark.parametrize("tc, slope", [(0.03, 24), (0.3, 24), (0.03, 6)])
def test_noise_follows_enbw(rc, tc, slope):
    lia, _, bench = rc
    lia.write(":OUTPut:STATe OFF")
    lia.write(f":FILTer:TCONstant {tc}")
    lia.write(f":FILTer:SLOPe {slope}")
    bandwidth = float(lia.query(":FILTer:ENBW?"))
    bench.advance(30 * tc)
    xs = []
    for _ in range(400):
        bench.advance(10 * tc)
        xs.append(snap(lia, "X")[0])
    predicted = 5e-9 * math.sqrt(bandwidth)
    assert statistics.pstdev(xs) == pytest.approx(predicted, rel=0.12)  # 400 reads: ±3.5 % (1σ)


def test_noise_is_a_function_of_time(rc):
    lia, _, bench = rc
    lia.write(":OUTPut:STATe OFF")
    bench.advance(1.0)
    a, b = snap(lia, "X")[0], snap(lia, "X")[0]
    assert a == b  # same instant, same reading
    xs = []
    for _ in range(300):
        bench.advance(0.003)  # a tenth of a time constant apart
        xs.append(snap(lia, "X")[0])
    assert np.corrcoef(xs[:-1], xs[1:])[0, 1] > 0.8


# ---- 4. external reference and harmonics --------------------------------------------------------


@pytest.fixture
def chopper():
    rm, res, bench = open_bench("lockin_chopper.bench.yaml")
    yield res["lia"], res["ref"], res["sig"], bench
    rm.close()


def test_external_reference_and_harmonics(chopper):
    lia, _, _, bench = chopper
    assert lia.query(":REFerence:LOCKed?") == "1"
    assert float(lia.query(":REFerence:FREQuency:ACTual?")) == pytest.approx(1000.0)
    divider = 10e6 / (10e6 + 50)  # the square's 50 Ω into SIG_IN's 10 MΩ
    for n in (1, 3, 5):
        lia.write(f":REFerence:HARMonic {n}")
        bench.advance(1.0)
        assert float(lia.query(":REFerence:DETect:FREQuency?")) == pytest.approx(1000.0 * n)
        expected_r = 4 / (math.pi * n) / math.sqrt(2) * divider
        assert float(lia.query(":MEASure:R?")) == pytest.approx(expected_r, rel=1e-5)
    assert drain(lia) == []


def test_harmonic_limit(chopper):
    lia, _, _, _ = chopper
    lia.write(":REFerence:SOURce INTernal;:REFerence:FREQuency 10000")
    lia.write(":REFerence:HARMonic 51")  # 510 kHz > 500 kHz
    assert drain(lia) == ['-221,"Settings conflict; harmonic 51 x 10000 Hz exceeds 500000 Hz"']
    assert lia.query(":REFerence:HARMonic?") == "1"


def test_reference_lost_and_regained(chopper):
    lia, ref, _, bench = chopper
    bench.advance(1.0)
    r_locked = float(lia.query(":MEASure:R?"))
    ref.write(":OUTPut1:STATe OFF")  # the chopper stops
    assert lia.query(":REFerence:LOCKed?") == "0"
    assert drain(lia) == ['327,"Reference unlock"']
    bench.advance(1.0)
    assert float(lia.query(":MEASure:R?")) == pytest.approx(r_locked, rel=1e-6)  # free-runs on
    ref.write(":OUTPut1:STATe ON")
    assert lia.query(":REFerence:LOCKed?") == "1"
    assert drain(lia) == []


# ---- 5. ranges, coupling, auto functions --------------------------------------------------------


def test_overloads_ladder_and_auto_functions(rc):
    lia, _, bench = rc
    lia.write(":SOURce:VOLTage 0.5")
    lia.write(":REFerence:FREQuency 1515.76")  # the physics-correct corner: R ≈ 353.5 mV
    bench.advance(1.0)
    assert float(lia.query(":INPut:SENSitivity?")) == 1.0
    lia.write(":INPut:SENSitivity 0.3")  # snaps to the nearest 1-2-5 step
    assert float(lia.query(":INPut:SENSitivity?")) == 0.2
    assert lia.query(":STATus:OVERload?") == "2"
    assert drain(lia) == ['326,"Output overload"']
    lia.write(":INPut:SENSitivity 0.01")
    assert lia.query(":STATus:OVERload?") == "3"
    assert lia.query(":STATus:OVERload:INPut?") == "1"
    assert drain(lia) == ['325,"Input overload"']
    lia.write(":AUTO:GAIN")
    assert float(lia.query(":INPut:SENSitivity?")) == 0.5
    assert lia.query(":STATus:OVERload?") == "0"

    lia.write(":REFerence:PHASe 30")
    bench.advance(1.0)
    theta, y = snap(lia, "THETa", "Y")
    assert theta == pytest.approx(-75.0, abs=0.02)
    lia.write(":AUTO:PHASe")
    assert float(lia.query(":REFerence:PHASe?")) == pytest.approx(-45.0, abs=0.02)
    assert snap(lia, "THETa")[0] == pytest.approx(-75.0, abs=0.02)  # the output has not moved yet
    bench.advance(2 * float(lia.query(":FILTer:SETTling?")))
    theta, y, x = snap(lia, "THETa", "Y", "X")
    assert abs(theta) < 1e-3 and abs(y) < 5e-6  # the article: θ = −0.0002°, Y = −0.0010 mV
    assert x == pytest.approx(abs(expected(1515.76, 0.5)), rel=1e-5)


def test_auto_gain_refused_with_a_slow_filter(rc):
    lia, _, _ = rc
    lia.write(":FILTer:TCONstant 3")
    lia.write(":AUTO:GAIN")
    assert drain(lia)[0].startswith("-221,")


def test_ac_coupling_removes_the_background(chopper):
    lia, _, sig, bench = chopper
    sig.write(":SOURce1:FUNCtion SINusoid")
    sig.write(":SOURce1:VOLTage 0.002")  # 2 mVpp: 0.707 mV rms
    sig.write(":SOURce1:VOLTage:OFFSet 0.5")  # on 0.5 V of background
    lia.write(":INPut:SENSitivity 0.002")
    drain(lia)
    readings = {}
    for coupling in ("DC", "AC"):
        lia.write(f":INPut:COUPling {coupling}")
        bench.advance(1.0)
        readings[coupling] = (float(lia.query(":MEASure:R?")), lia.query(":STATus:OVERload?"), drain(lia))
    assert readings["DC"][1:] == ("1", ['325,"Input overload"'])
    assert readings["AC"][1:] == ("0", [])
    for r, _, _ in readings.values():
        assert r == pytest.approx(0.002 / 2 / math.sqrt(2) * 10e6 / (10e6 + 50), rel=1e-4)


# ---- 6. model limits ----------------------------------------------------------------------------


def test_truncated_square_with_a_wide_filter(chopper):
    lia, _, _, bench = chopper
    drain(lia)
    lia.write(":FILTer:SLOPe 24;:FILTer:TCONstant 0.03")
    bench.advance(1.0)
    assert drain(lia) == []
    lia.write(":FILTer:SLOPe 6;:FILTer:TCONstant 1e-4")
    bench.advance(1.0)
    assert float(lia.query(":FILTer:ENBW?")) == pytest.approx(2500.0)
    assert drain(lia) == ['329,"Input beyond the model\'s limits"']


def test_arbitrary_table_spectrum_is_exact():
    """validation-lockin.md 6: 698.07 µV on the reference. D13: the table's spectrum is exact, so a
    table at 1001 Hz is not a model limit: R is the filter's true (beating) output and no 329."""
    rm, res, bench = open_bench("lockin_arb.bench.yaml")
    lia, arb = res["lia"], res["arb"]
    divider = 10e6 / (10e6 + 50)
    bench.advance(1.0)
    r = float(lia.query(":MEASure:R?"))
    assert r * 1e6 == pytest.approx(698.07, abs=0.01)
    # within the front end's 8 nV of noise
    assert r == pytest.approx(staircase_fundamental(TABLE, 1e-3) / math.sqrt(2) * divider, abs=3e-8)
    arb.write(":SOURce:RATE 16016")
    bench.advance(1.0)
    assert lia.query(":SYSTem:ERRor?").startswith("0,")
    assert float(lia.query(":MEASure:R?")) < r  # 1 Hz from the reference: attenuated and beating
    rm.close()
