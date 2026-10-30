"""The configurable oscilloscope acquisition (instrument-authoring.md 3.2, issue 44): record length,
trigger, run control, noise and the ADC are parameters of the definition, not code per model."""

from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from simubench.api import open_bench
from simubench.engine.session import Session
from simubench.engine.validate_definition import validate_definition_data
from simubench.library.components.scope import _measure, _top_base
from simubench.library.loader import DefinitionLibrary, load_yaml
from simubench.models.bench import BenchDefinition
from simubench.models.definition import InstrumentDefinition

ROOT = Path(__file__).resolve().parents[1]
LFCAL = ROOT / "examples" / "lowfreq-cal"
LECROY = load_yaml((LFCAL / "instruments" / "lfcal-reference-scope.yaml").read_text(encoding="utf-8"))
CALIBRATOR = load_yaml((LFCAL / "instruments" / "lfcal-scope-calibrator.yaml").read_text(encoding="utf-8"))
SIGLENT = ROOT / "examples" / "benches" / "siglent_scope_rc.bench.yaml"


def _lfcal(scope: dict):
    """The low-frequency calibration bench with ``scope`` as the reference scope's definition."""
    bench = BenchDefinition.model_validate(
        load_yaml((LFCAL / "lowfreq_cal.bench.yaml").read_text(encoding="utf-8"))
    )
    defs = {
        "lfcal-reference-scope": InstrumentDefinition.model_validate(scope),
        "lfcal-scope-calibrator": InstrumentDefinition.model_validate(CALIBRATOR),
    }
    session = Session(bench, defs.__getitem__, clock="command", seed=1, idle="off")
    for cmd in (":CHANnel1:BASE:AMPLitude 1", ":CHANnel1:BASE:FREQuency 1000", ":CHANnel1:OUTPut 1"):
        session.write("calibrator", cmd.encode() + b"\n")

    def query(cmd: str) -> str:
        session.write("scope", cmd.encode() + b"\n")
        out = session.read("scope") if "?" in cmd else None
        return out.decode().strip() if out is not None else ""

    for cmd in ("C1:TRA ON", "C1:CPL D50", "C1:VDIV 0.2", "MSIZ 10K", "TDIV 1E-3"):
        query(cmd)
    return session, query


def test_run_control_is_configuration():
    """The reference scope's TRMD behaves the same with the plain scope_acquisition component."""
    plain = copy.deepcopy(LECROY)
    acq = next(c for c in plain["components"] if c["name"] == "acq")
    acq["type"] = "scope_acquisition"
    plain["commands"] = [c for c in plain["commands"] if "acq.inspect" not in str(c.get("op"))]
    plain["commands"] = [c for c in plain["commands"] if "acq.pava" not in str(c.get("op"))]
    for definition in (LECROY, plain):
        session, query = _lfcal(definition)
        scope = session.instruments["scope"].components["acq"]
        query("C1:TRLV 10")  # above the signal: a single acquisition waits
        query("TRMD SINGLE")
        assert (query("TRMD?"), scope.acq_count) == ("TRMD SINGLE", 0)
        query("C1:TRLV 0")
        assert query("*OPC?") == "1"  # the pending acquisition is retried and completes
        assert (query("TRMD?"), scope.acq_count) == ("TRMD STOP", 1)
        query("TRMD AUTO")
        query("STOP")
        assert query("TRMD?") == "TRMD STOP"
        session.close()


def test_the_record_length_rules():
    session, _ = _lfcal(LECROY)
    scope = session.instruments["scope"].components
    acq, timebase = scope["acq"], scope["timebase"]  # the one-component form expands into modules
    assert acq._timing()[:2] == (10_000, pytest.approx(1e-6))  # exact: MSIZ points over ten divisions
    timebase._params.update(record_length="rate_ladder", points=30_000)
    points, dt, pre, _ = acq._timing()
    assert (points, dt, pre) == (20_000, pytest.approx(0.5e-6), 10_000)  # 3 MSa/s rounded down to 2
    timebase._params.update(delay=2e-3)  # the trigger 2 ms before the centre
    assert acq._timing()[2] == 6_000
    session.close()


def test_a_waiting_single_acquisition_completes_once_the_trigger_comes():
    """A read does not complete a waiting single acquisition (completion: pending, issue 134): the
    scope triggers when the signal crosses the level, and the record fills in virtual time."""
    session = open_bench(SIGLENT, clock="command", seed=1, idle="off")

    def query(cmd: str) -> str:
        session.write("scope", cmd.encode() + b"\n")
        out = session.read("scope") if "?" in cmd else None
        return out.decode(errors="replace").strip() if out is not None else ""

    session.write("awg", b"C1:OUTP ON\n")
    query(":TRIGger:EDGE:LEVel 0.15")  # above the signal
    query(":TRIGger:MODE SINGle")  # the running scope's last frame is its latest acquisition
    assert (query(":TRIGger:STATus?"), query(":ACQuire:NUMAcq?")) == ("Ready", "1")
    assert query("*OPC?") == ""  # no trigger: no reply, the client's read times out
    query(":WAVeform:DATA?")  # a read gets the previous record and does not trigger
    assert (query(":TRIGger:STATus?"), query(":ACQuire:NUMAcq?")) == ("Ready", "1")
    query(":TRIGger:EDGE:LEVel 0")  # now the signal crosses the level
    assert query("*OPC?") == "1"  # waits until the record after the trigger is full
    assert (query(":TRIGger:STATus?"), query(":ACQuire:NUMAcq?")) == ("Stop", "2")
    session.close()


def test_averaging_takes_the_time_of_its_acquisitions():
    session = open_bench(SIGLENT, clock="command", seed=1, idle="off")
    session.write("awg", b"C1:OUTP ON\n")
    acq = session.instruments["scope"].components["acq"]
    _, one, _ = acq.compute([1])
    session.write("scope", b":ACQuire:TYPE AVERage,16\n")
    _, sixteen, _ = acq.compute([1])
    overhead = round(float(acq.param("overhead_s")) * 1e9)
    assert sixteen - overhead == pytest.approx(16 * (one - overhead), rel=1e-6)
    session.close()


def test_channel_bindings_are_checked():
    bad = copy.deepcopy(LECROY)
    acq = next(i for i, c in enumerate(bad["components"]) if c["name"] == "acq")
    bad["components"][acq]["params"]["trigger_level"] = "$ch{n}.trigger_lvl"
    _, report = validate_definition_data(bad)
    # The one-component form expands: the trigger module, right after the acquisition, has the level.
    assert [(d.code, d.pointer) for d in report.diagnostics] == [
        ("E-DEF-STATE-UNKNOWN", f"/components/{acq + 2}/params/level")
    ]


def test_amplitude_falls_back_to_the_extremes_without_levels():
    """A ramp or triangle fills the histogram evenly: no top and base to tell (F2, #131), and
    AMPLitude falls back to the extremes; a flat record or too little data does the same."""
    ramp = np.linspace(-1.0, 1.0, 10_000)
    assert _top_base(ramp) is None
    assert _measure("VAMPlitude", ramp, 1e-6) == pytest.approx(2.0)
    flat = np.zeros(100)
    assert _measure("VAMPlitude", flat, 1e-6) == 0.0
    assert _measure("VAMPlitude", np.array([0.0, 1.0, 0.0]), 1e-6) == pytest.approx(1.0)
    assert math.isnan(_measure("FREQuency", flat, 1e-6))
    assert math.isnan(_measure("PERiod", ramp, 1e-6))


def _modules_definition(**params: Any) -> InstrumentDefinition:
    """``sb-dso-4`` (explicit modules) with the metrology parameters a test sets.

    ``ref``: add a 50 Ω ``input_port`` on ``timebase.ref`` and expose it as REF, the way the
    spectrum analyzer's and lock-in's REF_IN are written; ``timebase`` / ``vertical``: parameters
    of those components (hidden values: a test writes them in the definition)."""
    data = load_yaml(DefinitionLibrary().path("sb-dso-4").read_text(encoding="utf-8"))
    timebase = next(c for c in data["components"] if c["name"] == "timebase")
    timebase["params"] = {**(timebase.get("params") or {}), **params.get("timebase", {})}
    front = next(c for c in data["channel_components"] if c["name"] == "front")
    front["params"] = {**(front.get("params") or {}), **params.get("vertical", {})}
    if params.get("ref"):
        data["components"].append({"name": "ref_in", "type": "input_port", "params": {"z_in": 50.0}})
        data["wiring"]["instrument"].append(["ref_in.out", "timebase.ref"])
        ports = data.setdefault("ports", {})
        ports.setdefault("instrument", {})["REF"] = "ref_in.pin"
        ports.setdefault("media", {})["REF"] = "reference"
    if "deviations" in params:
        data["deviations"] = params["deviations"]
    return InstrumentDefinition.model_validate(data)


def _bench(**params: Any) -> dict:
    state = {
        "ch1.frequency": 1000.0,
        "ch1.amplitude": 2.0,
        "ch1.load": math.inf,
        "ch1.output": True,
        **params.get("awg", {}),
    }
    instruments = {
        "awg": {"model": "sb-awg-2", "state": state},
        "scope": {
            "model": "sb-dso-4",
            "deviation": params.get("deviation", "nominal"),
            "hidden": params.get("hidden", {}),
        },
    }
    wires = [{"from": "awg.OUT1", "to": "scope.CH1"}]
    if params.get("ref"):
        state.update(
            {
                "ch2.frequency": 10.00005e6,  # 10 MHz + 0.5 ppm: a fast reference
                "ch2.amplitude": 1.0,
                "ch2.load": 50.0,
                "ch2.output": True,
            }
        )
        wires.append({"from": "awg.OUT2", "to": "scope.REF"})
    return {
        "schema": "simubench.bench/1",
        "name": "metrology",
        "instruments": instruments,
        "wires": wires,
        "run": {"seed": 3, "clock": "command"},
    }


def _session(bench: dict, definition: InstrumentDefinition) -> Session:
    lib = DefinitionLibrary()
    return Session(
        BenchDefinition.model_validate(bench),
        lambda model: definition if model == "sb-dso-4" else lib.get(model),
        clock="command",
        seed=3,
        idle="off",
    )


def test_the_clock_follows_ref_in_and_falls_back_to_the_internal_one():
    """G3 (#135): with ``rosc`` EXTernal the sampling clock is the 10 MHz on REF_IN — the
    reference's error replaces the unit's own ppm. A fast clock takes samples closer together,
    so the frequency counter reads low (the spectrum analyzer's reference convention); without a
    usable reference the internal clock stays in charge."""
    definition = _modules_definition(ref=True, timebase={"rosc": "EXTernal", "clock_ppm": 400.0})
    session = _session(_bench(ref=True), definition)
    scope = session.instruments["scope"]
    tb, acq = scope.components["timebase"], scope.components["acq"]
    assert tb.clock_error() == pytest.approx(5e-6)  # the reference's 0.5 ppm, not the unit's 400
    session.write("scope", b":DIGitize\n")
    record = acq.records[1]
    assert _measure("FREQuency", record.volts, record.dt_s) == pytest.approx(1000.0 / (1 + 5e-6), abs=0.01)
    session.write("awg", b":OUTPut2:STATe OFF\n")  # the reference dies: the internal clock takes over
    assert tb.clock_error() == pytest.approx(400e-6)
    session.close()


def test_gain_offset_and_dac_errors_follow_the_input_v_div():
    """G3 (#135): ``gain_error_ppm`` and ``offset_error`` are per V/div at the input connector,
    the offset DAC's error scales with the display offset setting, and a probe ratio only moves
    the display (the errors stay with the input range). The record shows all three."""
    definition = _modules_definition(
        vertical={
            "gain_error_ppm": {1e-3: 10_000.0, 1e-2: -20_000.0},
            "offset_error": {1e-3: 5e-4},
            "dac_error_ppm": 10_000.0,
        }
    )
    session = _session(_bench(awg={"ch1.function": "DC", "ch1.offset": 0.0025}), definition)
    acq = session.instruments["scope"].components["acq"]
    loaded = 1e6 / (1e6 + 50)  # the scope's 1 MΩ input across the AWG's 50 Ω source

    def record(scale: float, offset: float = 0.0) -> np.ndarray:
        session.write("scope", f":CHANnel1:SCALe {scale:g};:CHANnel1:OFFSet {offset:g}\n".encode())
        session.write("scope", b":DIGitize\n")
        return acq.records[1].volts

    # 1 mV/div: +10000 ppm and 0.5 mV of input offset; the 0.2 V offset setting adds its DAC error
    assert float(np.mean(record(1e-3, 0.2))) == pytest.approx(
        0.0025 * loaded * 1.01 + 0.5e-3 + 0.2 * 10_000e-6, rel=1e-12
    )
    # 10 mV/div: -20000 ppm, no offset error (the table has none at this scale), no offset setting
    assert float(np.mean(record(1e-2))) == pytest.approx(0.0025 * loaded * 0.98, rel=1e-12)
    # A scale the table does not list has no characterized error.
    assert float(np.mean(record(2e-2))) == pytest.approx(0.0025 * loaded, rel=1e-12)
    # 10X probe, 10 mV/div displayed: the input range is 1 mV/div, so the 1 mV/div errors apply in
    # display volts: the offset error is 0.5 mV at the input, 5 mV after the probe ratio.
    session.write("scope", b":CHANnel1:PROBe 10\n")
    assert float(np.mean(record(1e-2))) == pytest.approx((0.0025 * loaded * 1.01 + 5e-4) * 10, rel=1e-9)
    session.close()


def test_skew_and_clock_deviations_are_drawn():
    """G3 (#135): a definition declares the probe's skew error and the timebase's clock ppm as
    individual deviations; each unit draws them from the bench seed."""
    definition = _modules_definition(
        deviations=[
            {"param": "front.gain_error_ppm", "limit": 1000.0, "distribution": "uniform"},
            {"param": "front.offset_error", "limit": 1e-3, "distribution": "uniform"},
            {"param": "probe.skew_error", "limit": 1e-7, "distribution": "uniform"},
            {"param": "timebase.clock_ppm", "limit": 10.0, "distribution": "uniform"},
        ]
    )
    session = _session(_bench(deviation="from_specs"), definition)
    scope = session.instruments["scope"]
    drawn = scope.deviations
    assert {k.rsplit(".", 1)[-1] for k in drawn} == {
        "gain_error_ppm",
        "offset_error",
        "skew_error",
        "clock_ppm",
    }
    assert all(abs(v) <= 1000.0 for k, v in drawn.items() if k.endswith("gain_error_ppm"))
    assert all(abs(v) <= 1e-3 for k, v in drawn.items() if k.endswith("offset_error"))
    assert all(abs(v) <= 1e-7 for k, v in drawn.items() if k.endswith("skew_error"))
    assert all(abs(v) <= 10.0 for k, v in drawn.items() if k.endswith("clock_ppm"))
    timebase = scope.components["timebase"]
    assert timebase.clock_error() == pytest.approx(drawn["timebase.clock_ppm"] * 1e-6)
    for n in range(1, 5):
        front = scope.components[f"ch{n}.front"]
        probe = scope.components[f"ch{n}.probe"]
        assert front._error("gain_error_ppm") == drawn[f"ch{n}.front.gain_error_ppm"]
        assert front._error("offset_error") == drawn[f"ch{n}.front.offset_error"]
        assert probe.delay() == drawn[f"ch{n}.probe.skew_error"]
    session.close()
