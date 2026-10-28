"""Siglent SDG2042X (issue 10): what reaches the load, read with the SDS2104X Plus.

The manual cases (tests/models/siglent-sdg2042x/cases.yaml) check commands and replies; these tests
check the physics behind them: the amplitude is defined for the load setting (D13), so the voltage
at the scope follows the actual load, and the waveform shapes (duty, symmetry, polarity) arrive as set.
"""

import math
import shutil
from pathlib import Path

import pytest
import pyvisa

from simubench.scpi.errors import ScpiError

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "tests" / "models" / "siglent-sdg2042x" / "bench.yaml"
Z_OUT = 50.0


@pytest.fixture
def bench(tmp_path):
    path = tmp_path / "bench.yaml"
    shutil.copy(BENCH, path)
    rm = pyvisa.ResourceManager(f"{path}@simubench")
    sdg = rm.open_resource("sdg", read_termination="\n", write_termination="\n")
    sds = rm.open_resource("scope", read_termination="\n", write_termination="\n")
    yield sdg, sds, rm.visalib.bench
    rm.close()


def measure(sds, what: str) -> float:
    sds.write(":TRIGger:MODE SINGle")
    return float(sds.query(f":MEASure:SIMPle:VALue? {what}"))


@pytest.mark.parametrize(
    ("load_setting", "scope_input", "r_load"),
    [("HZ", "ONEMeg", 1e6), ("50", "ONEMeg", 1e6), ("HZ", "FIFTy", 50.0), ("50", "FIFTy", 50.0)],
)
def test_amplitude_at_the_scope_follows_the_load(bench, load_setting, scope_input, r_load):
    sdg, sds, _ = bench
    sdg.write(f"C1:OUTP LOAD,{load_setting}")
    sdg.write("C1:BSWV WVTP,SINE,FRQ,1000,AMP,2")
    sdg.write("C1:OUTP ON")
    sds.write(f":CHANnel1:IMPedance {scope_input};:CHANnel1:SCALe 0.5")
    # Hand calculation: the EMF makes AMP appear across the load setting; the real load divides it.
    r_set = math.inf if load_setting == "HZ" else float(load_setting)
    emf = 2.0 if math.isinf(r_set) else 2.0 * (r_set + Z_OUT) / r_set
    expected = emf * r_load / (r_load + Z_OUT)
    assert measure(sds, "PKPK") == pytest.approx(expected, rel=0.01)


def test_duty_symmetry_and_polarity_shape_the_waveform(bench):
    sdg, sds, _ = bench
    sds.write(":CHANnel1:SCALe 0.5")
    sdg.write("C1:BSWV WVTP,SQUARE,FRQ,1000,AMP,2,DUTY,20")
    sdg.write("C1:OUTP ON")
    assert measure(sds, "MEAN") == pytest.approx(2 * 0.2 - 1, abs=0.02)  # +1 V for 20 %, -1 V else
    sdg.write("C1:INVT ON")
    assert measure(sds, "MEAN") == pytest.approx(1 - 2 * 0.2, abs=0.02)
    sdg.write("C1:INVT OFF")
    sdg.write("C1:BSWV WVTP,RAMP,SYM,50")
    assert measure(sds, "RMS") == pytest.approx(1 / math.sqrt(3), rel=0.01)  # triangle: peak / sqrt 3
    assert measure(sds, "FREQ") == pytest.approx(1000, rel=0.005)


def test_settings_outside_the_data_sheet_are_refused(bench):
    sdg, _, session = bench
    sdg.write("C1:BSWV AMP,25")  # HiZ: at most 20 Vpp
    sdg.write("C1:OUTP LOAD,50")  # 4 Vpp fits 50 Ohm (at most 10 Vpp)
    sdg.write("C1:BSWV AMP,15")  # but 15 Vpp does not
    sdg.write("C1:BSWV OFST,3")  # 3 V + 4 Vpp / 2 = 5 V: the 50 Ohm limit, fine
    sdg.write("C1:BSWV OFST,3.5")  # 5.5 V: refused
    sdg.write("C1:BSWV WVTP,RAMP,FRQ,2E6")  # ramp: at most 1 MHz; none of the command applies
    assert sdg.query("C1:BSWV?").strip() == (
        "C1:BSWV WVTP,SINE,FRQ,1000HZ,PERI,0.001S,AMP,4V,OFST,3V,HLEV,5V,LLEV,1V,PHSE,0"
    )
    codes = [e["code"] for e in session.history if e.get("kind") == "error"]
    assert codes == [-222, -222, -221, -222]


def test_load_change_that_would_exceed_the_range_is_refused(bench):
    sdg, _, _ = bench
    sdg.write("C1:BSWV AMP,15")  # fine at HiZ
    sdg.write("C1:OUTP LOAD,50")  # 15 Vpp cannot be defined for 50 Ohm: the load stays HZ
    assert sdg.query("C1:OUTP?").strip() == "C1:OUTP OFF,LOAD,HZ,PLRT,NOR"


def test_unmodeled_waveforms_are_marked_in_the_event_history(bench):
    sdg, _, session = bench
    sdg.write("C1:BSWV WVTP,PULSE")
    errors = [e for e in session.history if e.get("kind") == "error"]
    assert errors[-1]["code"] == -224 and errors[-1]["reason"] == "not_modeled"
    assert sdg.query("C1:BSWV?").startswith("C1:BSWV WVTP,SINE")


def test_the_ui_path_checks_the_same_ranges(bench):
    """Settings changed outside a command (the UI's configure, the bench file) are checked too."""
    _, _, session = bench
    inst = session.instruments["sdg"]
    inst.set_state("ch1.load", 50.0)
    with pytest.raises(ScpiError) as exc:
        inst.set_state("ch1.amplitude", 15.0)  # 50 Ohm: at most 10 Vpp
    assert exc.value.code == -222
    assert inst.get_state("ch1.amplitude") == 4.0
