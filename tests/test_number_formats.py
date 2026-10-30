"""Pieces a manual's replies and settings need, declared rather than coded (issue 151): replies
with a fixed number of significant digits, a command whose number is a multiple of its setting
(a full-screen range for a per-division scale), a setting a module adjusts (clamped to its range)
and the transfer details of a byte-per-sample scope (a reserved hole code, conditional header
fields, the reference position of the time axis)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from simubench.api import open_bench
from simubench.engine.session import Session
from simubench.models.bench import BenchDefinition
from simubench.models.definition import InstrumentDefinition
from simubench.scpi.values import format_engineering_digits

ROOT = Path(__file__).resolve().parents[1]
DSOX_BENCH = ROOT / "tests" / "models" / "keysight-dsox2024a" / "bench.yaml"


@pytest.mark.parametrize(
    ("value", "digits", "kwargs", "text"),
    [
        (40.0, 3, {}, "+40.0E+00"),
        (0.0, 6, {}, "+0.00000E+00"),
        (60e-9, 15, {}, "+60.0000000000000E-09"),
        (10.0, 2, {}, "+10E+00"),
        (1e-3, 3, {}, "+1.00E-03"),
        (-2.5e6, 4, {}, "-2.500E+06"),
        (0.25, 3, {"sign": False, "exponent_digits": 3}, "250E-003"),
        (float("inf"), 3, {}, "+9.9E+37"),
    ],
)
def test_engineering_numbers_keep_their_significant_digits(value, digits, kwargs, text):
    assert format_engineering_digits(value, digits, **kwargs) == text.encode()


DEMO = {
    "schema": "simubench.instrument/1",
    "id": "demo",
    "manufacturer": "ACME",
    "model": "DEMO",
    "class": "oscilloscope",
    "format": {"number": "nr3", "digits": 5, "sign": True, "exponent_digits": 2},
    "state": {"timebase.scale": {"type": "float", "min": 1e-9, "max": 10.0, "default": 1e-3}},
    "commands": [
        {
            "header": "TIMebase:SCALe",
            "state": "timebase.scale",
            "format": {"number": "engineering", "significant": 3},
        },
        {
            "header": "TIMebase:RANGe",
            "state": "timebase.scale",
            "factor": 10,
            "format": {"number": "engineering", "significant": 3},
        },
    ],
}


def open_demo():
    definition = InstrumentDefinition.model_validate(DEMO)
    bench = BenchDefinition.model_validate(
        {"schema": "simubench.bench/1", "name": "d", "instruments": {"x": {"model": "demo"}}}
    )
    session = Session(bench, lambda _id: definition, clock="stepped", seed=1)

    def query(cmd: str) -> str:
        session.write("x", cmd.encode() + b"\n")
        out = session.read("x")
        return out.decode().strip() if out is not None else ""

    return session, query


def test_a_factor_command_sets_and_reads_a_multiple_of_its_setting():
    session, query = open_demo()
    query("TIM:RANG 2E-2")
    assert session.instruments["x"].get_state("timebase.scale") == pytest.approx(2e-3)
    assert query("TIM:SCAL?") == "+2.00E-03"
    assert query("TIM:RANG?") == "+20.0E-03"
    # The setting's range applies to the setting, not to the command's number.
    query("TIM:RANG 1000")
    assert query("SYST:ERR?").startswith("-222")
    assert query("TIM:RANG?") == "+20.0E-03"


@pytest.fixture
def dsox():
    s = open_bench(DSOX_BENCH)

    def q(cmd: str) -> bytes:
        s.write("scope", cmd.encode() + b"\n")
        return s.read("scope") or b""

    yield s, q
    s.close()


def _codes(reply: bytes) -> np.ndarray:
    digits = int(reply[1:2])
    size = int(reply[2 : 2 + digits])
    return np.frombuffer(reply[2 + digits : 2 + digits + size], "u1")


def test_a_reserved_hole_code_is_never_data(dsox):
    s, q = dsox
    # 2 Vpp at 50 mV/div clips at both ends: the bottom reads 1, never the hole code 0.
    q(":CHANnel1:PROBe 1;:CHANnel1:SCALe 0.05;:DIGitize CHANnel1;:WAVeform:FORMat BYTE")
    codes = _codes(q(":WAVeform:DATA?"))
    assert codes.min() == 1 and codes.max() == 255


def test_a_header_field_follows_a_setting_with_a_fallback(dsox):
    s, q = dsox
    q(":DIGitize CHANnel1")
    assert q(":WAVeform:PREamble?").split(b",")[3] == b"1"
    q(":ACQuire:TYPE AVERage;:ACQuire:COUNt 16;:DIGitize CHANnel1")
    fields = q(":WAVeform:PREamble?").split(b",")
    assert fields[1] == b"2" and fields[3] == b"16"


@pytest.mark.parametrize(("reference", "divs"), [("LEFT", 1), ("CENTer", 5), ("RIGHt", 9)])
def test_the_reference_position_sets_how_much_of_the_record_precedes_the_trigger(dsox, reference, divs):
    s, q = dsox
    q(f":TIMebase:REFerence {reference};:DIGitize CHANnel1")
    xorigin = float(q(":WAVeform:XORigin?"))
    assert xorigin == pytest.approx(-divs * 1e-4, abs=1e-6)  # plus the trigger's fraction of a sample
    assert float(q(":WAVeform:XINCrement?")) == pytest.approx(1e-6)
