"""Tunable laser, optical power meter and variable attenuator (#1103): the wavelength travels
with the light, the laser sweeps it in virtual time, the meter and the attenuator apply the
calibration of the wavelength they are set to, and the meter's logger sees each instant's
wavelength during a sweep."""

import math
from pathlib import Path

import pytest
import yaml

from simubench.api import open_bench
from simubench.engine.signal import Signal
from simubench.library.components.optical import (
    C_M_S,
    OpticalPowerMeter,
    TunableLaser,
    VariableOpticalAttenuator,
    watts_to_dbm,
    wavelength_table,
)

ROOT = Path(__file__).resolve().parents[1]
SWEEP = ROOT / "validation" / "benches" / "optical_sweep.bench.yaml"
DEFS = ROOT / "validation" / "instruments"


def _params(model: str, component: str) -> dict:
    data = yaml.safe_load((DEFS / f"{model}.yaml").read_text("utf-8"))
    return next(c["params"] for c in data["components"] if c["name"] == component)


RESPONSE = _params("sb-opm-1", "det")["spectral_response"]
LOSS = _params("sb-voa-1", "voa")["spectral_loss"]
IL = _params("sb-voa-1", "voa")["insertion_loss"]
OFFSET = _params("sb-tls-1", "laser")["wavelength_offset"]


def _expected_dbm(laser_dbm: float, att_db: float, wavelength: float, meter_at: float = 1.55e-6) -> float:
    """What the meter reads: the laser, less the attenuator (its wavelength-dependent loss
    against its 1550 nm calibration), times the detector's response ratio."""
    wdl = wavelength_table(LOSS, wavelength) - wavelength_table(LOSS, 1.55e-6)
    ratio = wavelength_table(RESPONSE, wavelength) / wavelength_table(RESPONSE, meter_at)
    return laser_dbm - IL - att_db - wdl + 10 * math.log10(ratio)


def test_wavelength_table_interpolates_and_holds_its_ends():
    table = [[1.5e-6, 1.0], [1.6e-6, 2.0]]
    assert wavelength_table(table, 1.55e-6) == pytest.approx(1.5)
    assert wavelength_table(table, 1.4e-6) == 1.0
    assert wavelength_table(table, 1.7e-6) == 2.0
    assert wavelength_table([], 1.55e-6) == 0.0


def test_stepped_sweep_holds_each_step_for_its_dwell():
    laser = TunableLaser(
        "l",
        {
            "sweep_mode": "STEPped",
            "sweep_start": 1.50e-6,
            "sweep_stop": 1.51e-6,
            "sweep_step": 2e-9,
            "sweep_dwell": 0.5,
        },
        None,
        None,
    )
    assert laser.cycle_time() == pytest.approx(6 * 0.5)  # 1500, 1502, … 1510 nm: six steps


def test_attenuator_in_power_mode_holds_the_target_within_reach():
    voa = VariableOpticalAttenuator(
        "v",
        {"mode": "POWer", "power_target": 1e-4, "insertion_loss": 1.0, "attenuation_max": 30.0},
        None,
        None,
    )
    voa.input = lambda port: Signal.constant(1e-3)  # type: ignore[method-assign]
    voa.input_carrier = lambda port: None  # type: ignore[method-assign]
    att, reachable = voa._target_attenuation()
    assert att == pytest.approx(9.0) and reachable  # 10 dB down from 1 mW, 1 dB of it the insertion loss
    voa.input = lambda port: Signal.constant(5e-5)  # type: ignore[method-assign]
    assert voa._target_attenuation() == (0.0, False)  # less light than the target: all the way open


def test_meter_reading_is_off_by_the_response_ratio_when_told_the_wrong_wavelength():
    meter = OpticalPowerMeter("m", {"spectral_response": RESPONSE, "wavelength": 1.31e-6}, None, None)
    meter.input_carrier = lambda port: 1.55e-6  # type: ignore[method-assign]
    assert meter.response_factor() == pytest.approx(1.0 / 0.9)


def test_sweep_through_attenuator_into_logging_meter():
    with open_bench(SWEEP, definition_dirs=[DEFS]) as s:
        # 1. still light: 1 mW at 1550 nm (+2 pm), 1.2 dB insertion loss + 10 dB, meter at 1550 nm
        assert float(s.query("tls", ":FETCh:WAVelength?")) == pytest.approx(1.55e-6 + OFFSET, abs=1e-15), (
            "emitted"
        )
        reading = float(s.query("opm", ":FETCh?"))
        assert reading == pytest.approx(_expected_dbm(0.0, 10.0, 1.55e-6 + OFFSET), abs=1e-3), (
            "static reading"
        )
        assert float(s.query("opm", ":SENS:POW:RANG:USED?")) == -10.0, "auto range"
        # 2. the meter told 1310 nm reads the 1550 nm light off by its response ratio
        s.write("opm", b":SENS:POW:WAV 1310NM")
        wrong = float(s.query("opm", ":FETCh?"))
        assert wrong == pytest.approx(_expected_dbm(0.0, 10.0, 1.55e-6 + OFFSET, 1.31e-6), abs=1e-3), (
            "wrong λ"
        )
        assert wrong - reading == pytest.approx(10 * math.log10(1 / 0.9), abs=1e-3), "response ratio"
        s.write("opm", b":SENS:POW:WAV 1550NM")
        # 3. an attenuation change reaches the output after the settling time
        s.write("voa", b":INP:ATT 20")
        assert float(s.query("voa", ":INP:ATT:ACT?")) == 10.0, "still settling"
        assert s.query("voa", ":STAT:SETT?").strip() == b"0", "busy"
        s.advance(0.06)
        assert float(s.query("voa", ":INP:ATT:ACT?")) == 20.0, "settled value"
        assert float(s.query("opm", ":FETCh?")) == pytest.approx(reading - 10.0, abs=1e-3), "10 dB more"
        s.write("voa", b":INP:ATT 10")
        s.advance(0.1)
        # 4. frequency and wavelength are one setting
        s.write("tls", b":WAV:FREQ 193.4E12")
        assert float(s.query("tls", ":WAV?")) == pytest.approx(C_M_S / 193.4e12, rel=1e-9), "c/f"
        # 5. a 10 s sweep 1500 → 1600 nm logged at 100 Hz: each point sees its instant's wavelength
        s.write("tls", b":INIT")
        s.write("opm", b":SENS:LOGG:RATE 100;:SENS:LOGG:POIN 1000;:SENS:LOGG:STAR")
        assert s.query("tls", ":WAV:SWE:STAT?").strip() == b"1", "sweeping"
        assert float(s.query("tls", ":WAV:SWE:TIME?")) == pytest.approx(10.0), "sweep time"
        s.advance(10.5)
        assert s.query("tls", ":WAV:SWE:STAT?").strip() == b"0", "sweep over"
        assert int(s.query("opm", ":SENS:LOGG:COUN?")) == 1000, "points logged"
        data = s.query("opm", ":SENS:LOGG:DATA? 0,1000").decode().strip().rstrip(",").split(",")
        logged = [watts_to_dbm(float(v)) for v in data]
        for k in (0, 1, 250, 500, 999):
            wavelength = 1.50e-6 + 10e-9 * k * 0.01 + OFFSET
            assert logged[k] == pytest.approx(_expected_dbm(0.0, 10.0, wavelength), abs=1e-3), f"point {k}"
        # 6. after the sweep the laser is back at its setting; the shutter darkens the fibre
        assert float(s.query("tls", ":FETCh:WAV?")) == pytest.approx(C_M_S / 193.4e12 + OFFSET, rel=1e-9), (
            "parked"
        )
        s.write("tls", b":POW:SHUT 1")
        dark_offset = _params("sb-opm-1", "det")["offset_w"]
        s.write("opm", b":SENS:POW:UNIT W")
        assert float(s.query("opm", ":FETCh?")) == pytest.approx(dark_offset, rel=1e-3), "dark offset"
        # 7. zeroing in the dark takes the offset out (what is left is the -60 dBm range's noise)
        s.write("opm", b":SENS:CORR:COLL:ZERO")
        assert abs(float(s.query("opm", ":FETCh?"))) < dark_offset / 100, "zeroed"
