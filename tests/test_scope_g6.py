"""Scope measurement and transfer modules (issue 138, instrument-authoring.md 8.4, G6): what a model
needs is declared on generic modules — a float block, a points mode, a preamble, named measurements
with their rules and statistics — so no scope needs a component of its own."""

from __future__ import annotations

import copy
import struct
from pathlib import Path

import numpy as np
import pytest

from simubench.api import open_bench
from simubench.cli import main
from simubench.library.coverage import load_cases
from simubench.library.loader import load_yaml
from simubench.models.definition import InstrumentDefinition

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "tests" / "models" / "contract-float-scope"
LFCAL_SCOPE = ROOT / "examples" / "lowfreq-cal" / "instruments" / "lfcal-reference-scope.yaml"


@pytest.fixture
def scope():
    s = open_bench(CONTRACT / "bench.yaml")
    yield s
    s.close()


def q(s, command: str) -> bytes:
    s.write("scope", command.encode() + b"\n")
    return s.read("scope") or b""


def block(reply: bytes) -> bytes:
    digits = int(reply[1:2])
    size = int(reply[2 : 2 + digits])
    return reply[2 + digits : 2 + digits + size]


def test_the_contract_definition_passes_its_cases(capsys):
    path = CONTRACT / "cases.yaml"
    assert main(["cases", str(path)]) == 0
    n = len(load_cases(path).cases)
    assert f"{n}/{n} cases passed" in capsys.readouterr().out


def test_a_float_block_carries_the_record_volts_of_the_points_mode(scope):
    q(scope, "TIMebase:SCALe 1E-3")
    q(scope, "FORMat REAL,32")
    q(scope, "CHANnel1:DATA:POINts 1000")
    values = np.frombuffer(block(q(scope, "CHANnel1:DATA?")), "<f4")
    rec = scope.instruments["scope"].components["acq"].records[1]
    np.testing.assert_array_equal(values, rec.volts[::10][:1000].astype("<f4"))  # every 10th of 10 000
    start, stop, points, per = q(scope, "CHANnel1:DATA:HEADer?").decode().strip().split(",")
    assert (float(start), float(stop), int(points), per) == (-5e-3, 5e-3, 1000, "1")


@pytest.mark.parametrize(("over", "expect"), [("allow", 6000), ("truncate", 5000), ("error", None)])
def test_a_count_above_the_limit_follows_over_limit(scope, over, expect):
    xfer = scope.instruments["scope"].components["xfer"]
    xfer.overrides["over_limit"] = over
    xfer.overrides["count_mode"] = "first"
    q(scope, "FORMat REAL,32")
    q(scope, "CHANnel1:DATA:POINts 6000")
    reply = q(scope, "CHANnel1:DATA?")
    if expect is None:
        assert q(scope, "SYST:ERR?").startswith(b"-222")
    else:
        assert len(block(reply)) == 4 * expect


def test_measurement_rules_are_parameters(scope):
    meas = scope.instruments["scope"].components["meas"]
    q(scope, "TIMebase:SCALe 1E-3")
    period = float(q(scope, "MEASurement:RESult? PERiod"))
    assert period == pytest.approx(1e-3, rel=1e-3)
    # An absolute crossing level above the middle still counts one crossing per cycle; one whose
    # hysteresis band reaches past the peak (0.9 V + 0.2 V) counts none.
    meas.overrides.update(levels="absolute", mid=0.5)
    assert float(q(scope, "MEASurement:RESult? PERiod")) == pytest.approx(1e-3, rel=1e-3)
    meas.overrides["mid"] = 0.9
    assert q(scope, "MEASurement:RESult? PERiod").strip() == b"9.91E+37"
    # Extremes instead of the histogram: the same span for a clean sine.
    meas.overrides.update(levels="percent", mid=50.0, top_base="extremes")
    assert float(q(scope, "MEASurement:RESult? AMPLitude")) == pytest.approx(2.0, rel=0.02)
    # A gate of one division holds a tenth of a cycle: no period in it.
    meas.overrides.update(top_base="histogram", gate=[5, 6])
    assert q(scope, "MEASurement:RESult? FREQuency").strip() == b"9.91E+37"


def test_a_clipped_record_is_unmeasurable_when_declared(scope):
    meas = scope.instruments["scope"].components["meas"]
    q(scope, "TIMebase:SCALe 1E-3")
    q(scope, "CHANnel1:SCALe 0.1")  # 2 Vpp on +-0.5 V: off the ADC
    assert float(q(scope, "MEASurement:RESult? PEAK")) < 1.1
    meas.overrides["clipped"] = "invalid"
    assert q(scope, "MEASurement:RESult? PEAK").strip() == b"9.91E+37"


def test_measurements_written_on_the_acquisition_move_to_a_module():
    """The earlier form (measurements on scope_acquisition, op acq.measurement) loads as a
    scope_measurements module named meas, the commands pointing at it."""
    data = load_yaml(LFCAL_SCOPE.read_text(encoding="utf-8"))
    d = InstrumentDefinition.model_validate(data)
    meas = next(c for c in d.components if c.type == "scope_measurements")
    assert meas.name == "meas" and meas.params["names"]["AMPL"] == ["VAMPlitude", "V"]
    assert "measurements" not in next(c for c in d.components if c.name == "acq").params
    assert any(c.op == "meas.named" for c in d.commands)
    # A name already taken moves it along; a definition with the module written out is left alone.
    taken = copy.deepcopy(data)
    taken["components"].append({"name": "meas", "type": "system"})
    assert any(c.name == "meas2" for c in InstrumentDefinition.model_validate(taken).components)


def test_a_binary_preamble_is_declared_field_by_field():
    s = open_bench(
        ROOT / "tests" / "models" / "siglent-sds2104xplus" / "bench.yaml",
        definition_dirs=[ROOT / "validation" / "instruments"],
    )
    try:
        for c in (
            ":TIMebase:SCALe 2E-4",
            ":CHANnel1:PROBe VALue,10",
            ":WAVeform:SOURce C1",
            ":WAVeform:POINt 500",
        ):
            s.write("scope", c.encode() + b"\n")
        s.write("scope", b":WAVeform:PREamble?\n")
        d = block(s.read("scope"))
        assert len(d) == 346 and d[:8] == b"WAVEDESC"
        assert struct.unpack_from("<i", d, 116)[0] == 500  # points sent
        assert struct.unpack_from("<f", d, 328)[0] == 10.0  # probe
        assert struct.unpack_from("<h", d, 324)[0] == 18  # 200 us/div in the time/div enumeration
        assert struct.unpack_from("<h", d, 336)[0] == 0  # channel 1
    finally:
        s.close()
