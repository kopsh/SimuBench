"""Optical medium and the optical blocks (#1093, D71): a transmitter's light through an attenuator
into an O/E converter, compared with the analytic levels; the bench checks that keep a fibre
between optical ports."""

import copy
import math
from pathlib import Path

import numpy as np
import pytest
import yaml

from simubench.api import open_bench
from simubench.engine.signal import Signal, sine
from simubench.engine.validate import validate_data
from simubench.library.components.optical import dbm_to_watts, modulation_levels, watts_to_dbm
from simubench.library.loader import DefinitionLibrary

ROOT = Path(__file__).resolve().parents[1]
LINK = ROOT / "validation" / "benches" / "optical_link.bench.yaml"


def test_two_level_powers_follow_average_and_extinction_ratio():
    p1, p0 = modulation_levels(1e-3, 6.0)
    assert (p1 + p0) / 2 == pytest.approx(1e-3)
    assert 10 * math.log10(p1 / p0) == pytest.approx(6.0)
    assert watts_to_dbm(1e-3) == pytest.approx(0.0)
    assert dbm_to_watts(-3.0) == pytest.approx(5.011872e-4, rel=1e-6)
    assert watts_to_dbm(0.0) == -math.inf


def test_receiver_volts_are_power_times_responsivity_times_gain():
    from simubench.library.components.optical import OpticalReceiver

    rx = OpticalReceiver("rx", {"responsivity": 0.8, "transimpedance": 500.0, "response": "none"}, None, None)
    light = Signal.constant(1e-3) + sine(1e6, 2e-4)
    rx.input = lambda port: light  # type: ignore[method-assign]
    out = rx.output("out")
    assert out.dc == pytest.approx(0.4)
    assert out.peak() == pytest.approx(0.4 + 0.08)
    assert rx.output_noise_rms() == 0.0


def test_light_crosses_the_bench_and_reads_as_the_analytic_levels():
    """otx (1 mW, ER 6 dB) → 3 dB attenuator → O/E (400 V/W, 20 GHz Bessel) → scope CH1."""
    avg = 1e-3 * 10 ** (-0.3)
    p1, p0 = modulation_levels(avg, 6.0)
    with open_bench(LINK) as s:
        # average power: the transmitter's, less the attenuator's 3 dB
        assert float(s.query("oe", ":FETCh?")) == pytest.approx(avg, rel=1e-6)
        s.write("oe", b":UNIT DBM")
        assert float(s.query("oe", ":FETCh?")) == pytest.approx(-3.0, abs=1e-6)
        # the electrical copy: the one and zero levels times 400 V/W, a 1 Gb/s NRZ settled well
        # inside a bit at 20 GHz (the scope adds its own small noise)
        s.write("scope", b":TIMebase:SCALe 20E-9;:CHANnel1:SCALe 0.1;:CHANnel1:OFFSet 0.2")
        vmax = float(s.query("scope", ":MEASure:VMAX? CHANnel1"))
        vmin = float(s.query("scope", ":MEASure:VMIN? CHANnel1"))
        assert vmax == pytest.approx(400 * p1, abs=0.005)
        assert vmin == pytest.approx(400 * p0, abs=0.005)
        # laser off: darkness, an unmeasurable power
        s.write("otx", b":OUTPut 0")
        assert s.query("oe", ":FETCh?").strip() == b"9.91E+37"
        s.write("scope", b":DIGitize CHANnel1")
        assert abs(float(s.query("scope", ":MEASure:VAVerage? CHANnel1"))) < 0.005


def _link() -> dict:
    return yaml.safe_load(LINK.read_text("utf-8"))


def _codes(data: dict) -> list[str]:
    _, report = validate_data(data, DefinitionLibrary())
    return [d.code for d in report.diagnostics]


def test_the_link_validates_clean():
    assert _codes(_link()) == []


def test_a_fibre_joins_optical_ports_only():
    data = _link()
    data["wires"][0] = {"from": "otx.OUT", "to": "scope.CH2"}
    assert "E-WIRE-MEDIUM" in _codes(data)
    data = _link()
    data["wires"][1]["accessories"] = [{"type": "passive_probe"}]
    assert "E-WIRE-MEDIUM" in _codes(data)


def test_two_lights_on_one_fibre_are_refused():
    data = copy.deepcopy(_link())
    data["instruments"]["otx2"] = {"model": "sb-otx-1"}
    data["wires"].append({"from": "otx2.OUT", "to": "att.IN"})
    assert "E-NET-MULTIDRIVER" in _codes(data)


def test_an_unwired_receiver_sees_no_light(tmp_path):
    data = _link()
    data["wires"] = [w for w in data["wires"] if w["to"] != "oe.IN"]
    path = tmp_path / "dark.bench.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), "utf-8")
    with open_bench(path) as s:
        assert s.query("oe", ":FETCh?").strip() == b"9.91E+37"
        out = s.instrument("oe").components["orx"].output("out")
        assert np.all(out.amp == 0) and out.dc == 0.0
