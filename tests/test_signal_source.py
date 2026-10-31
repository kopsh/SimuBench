"""The signal source as modules and its template (issue 144): waveform_synth -> amplitude_level ->
output_stage with a reference_clock; amplitude in Vpp, Vpk, Vrms and dBm for the load setting; the duty
as a ratio or a percentage by declaration."""

from __future__ import annotations

import copy
import math
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

from simubench.api import open_bench
from simubench.engine.signal import MAX_LINES
from simubench.library.coverage import run_cases
from simubench.library.templates import INPUTS, get_template
from simubench.scpi.errors import ScpiError
from simubench.server.catalog import components

ROOT = Path(__file__).resolve().parents[1]
SOURCE = get_template("signal_source")

AWG2_INPUTS = {
    "identity": {
        "manufacturer": "SimuBench", "model": "SB-AWG-2", "serial": "SBAWG000001", "firmware": "1.0.0",
        "description": "双通道函数发生器，输出阻抗 50 Ω，幅度按设定负载定义（D13）。",
    },
    "channels": 2,
    "waveforms": [
        {"name": "SINusoid", "shape": "sine"}, {"name": "SQUare", "shape": "square"},
        {"name": "RAMP", "shape": "ramp"}, {"name": "DC", "shape": "dc"},
    ],
    "frequency": {"min": 1.0e-6, "max": 60.0e6, "default": 1000.0, "square_max": 25.0e6, "bandwidth_hz": 300.0e6},
    "amplitude": {"min": 1.0e-3, "max": 10.0, "default": 0.1, "label": "幅度 (Vpp)"},
    "offset": {"min": -5.0, "max": 5.0, "default": 0.0},
    "phase": {"min": -360.0, "max": 360.0, "default": 0.0},
    "load": {"min": 1.0, "max": 10000.0, "default": 50.0, "high_z_keywords": ["INF", "INFinity"]},
    "output": {"impedance_ohms": 50.0, "impedance_error_pct": 2.0},
}  # fmt: skip

LFCAL_INPUTS = {
    "identity": {
        "manufacturer": "SimuBench", "model": "LFCAL-CALIBRATOR", "serial": "CAL0000001", "firmware": "1.0.0",
        "idn": "{manufacturer},{model},{serial},{firmware}",
    },
    "channels": 4,
    "waveforms": [
        {"name": "SQUare", "shape": "square"}, {"name": "PULSe", "shape": "pulse"}, {"name": "SINe", "shape": "sine"},
        {"name": "DC", "shape": "dc"}, {"name": "TRIangle", "shape": "triangle"}, {"name": "RAMP", "shape": "ramp"},
    ],
    "frequency": {"min": 0.01, "max": 1.1e9, "default": 1000.0, "bandwidth_hz": 1.0e9},
    "amplitude": {"min": 0.001, "max": 200.0, "default": 0.1, "units": ["VPP", "VPK", "VRMS", "DBM"]},
    "offset": {"min": -100.0, "max": 100.0, "default": 0.0},
    "duty": {"min": 1.0e-6, "max": 1.0, "default": 0.5, "unit": "ratio"},
    "load": {"min": 1.0, "max": 1.0e9, "default": 50.0, "hiz_from": 10000.0},
    "clock": {"sources": ["INTernal", "EXTernal"]},
    "dialect": {"command_set": "channel-base", "unknown_commands": "ignore"},
}  # fmt: skip


def _expand(inputs: dict, def_id: str) -> dict:
    out = SOURCE.expand(inputs, def_id)
    assert out.ok, [d.as_dict() for d in out.diagnostics]
    assert out.definition is not None
    return out.definition


def _bench(tmp_path: Path, definition: dict, port: str, *, load: float | None = 50.0, seed: int = 1) -> Path:
    """The source's channel 1 into a load resistor (or open)."""
    inst = tmp_path / "instruments"
    inst.mkdir(exist_ok=True)
    (inst / f"{definition['id']}.yaml").write_text(
        yaml.safe_dump(definition, allow_unicode=True, sort_keys=False), "utf-8"
    )
    bench: dict = {
        "schema": "simubench.bench/2", "name": "src",
        "instruments": {"gen": {"model": definition["id"], "deviation": "nominal"}},
        "run": {"seed": seed, "clock": "command", "idle": "off"},
    }  # fmt: skip
    if load is not None:
        bench["parts"] = {"rl": {"type": "load_resistor", "params": {"r": load}}}
        bench["wires"] = [{"from": f"gen.{port}", "to": "rl.A"}]
    path = tmp_path / f"{definition['id']}-{load}.bench.yaml"
    path.write_text(yaml.safe_dump(bench, allow_unicode=True), "utf-8")
    return path


def _net(session, port: str):
    return session.net_signal(session.port_net[f"gen.{port}"])


# ---- the template --------------------------------------------------------------------------------


def test_the_example_expands_clean_from_generic_modules():
    definition = _expand(SOURCE.example(), "acme-cal-4")
    generic = {c.type: c.generic for c in components()}
    used = [c["type"] for c in definition.get("components", []) + definition["channel_components"]]
    assert all(generic[t] for t in used), used
    assert {"waveform_synth", "amplitude_level", "output_stage", "reference_clock"} <= set(used)


SEQUENCE_AWG = [
    "*IDN?", ":SOUR1:FUNC SQU", ":SOUR1:FREQ 12345", ":SOUR1:VOLT 2.5", ":SOUR1:VOLT:OFFS 0.25",
    ":OUTP1:LOAD INF", ":OUTP1 ON", ":SOUR1:FUNC?", ":SOUR1:FREQ?", ":SOUR1:VOLT?", ":OUTP1:LOAD?",
    ":SOUR2:FUNC RAMP", ":SOUR2:FREQ 40E6", ":SYST:ERR?", ":SOUR2:FREQ 1E6", ":SOUR2:PHAS 90", ":OUTP2 ON",
    ":SOUR1:VOLT 20", ":SYST:ERR?", ":FOO?", ":SYST:ERR?",
]  # fmt: skip


def _run(path: Path, sequence: list[str], port: str):
    s = open_bench(path)
    try:
        replies = []
        for c in sequence:
            s.write("gen", c.encode() + b"\n")
            replies.append(s.read("gen") if "?" in c else None)
        return replies, _net(s, port) if f"gen.{port}" in s.port_net else None
    finally:
        s.close()


@pytest.mark.parametrize("load", [50.0, None])
def test_a_template_built_sb_awg_2_matches_sb_awg_2(tmp_path, load):
    built = _expand(AWG2_INPUTS, "tpl-awg-2")
    reference = yaml.safe_load((ROOT / "validation" / "instruments" / "sb-awg-2.yaml").read_text("utf-8"))
    ours, sig = _run(_bench(tmp_path, built, "OUT1", load=load), SEQUENCE_AWG, "OUT1")
    theirs, ref = _run(
        _bench(tmp_path, {**reference, "id": "copy-awg-2"}, "OUT1", load=load), SEQUENCE_AWG, "OUT1"
    )
    assert ours == theirs
    if load is not None:
        t = np.linspace(0, 2 / 12345, 2001)
        np.testing.assert_allclose(sig.sample_rel(0, t), ref.sample_rel(0, t), atol=1e-12)


def test_a_template_built_calibrator_passes_the_low_frequency_contract(tmp_path):
    work = tmp_path / "lowfreq-cal"
    shutil.copytree(ROOT / "examples" / "lowfreq-cal", work, ignore=shutil.ignore_patterns("__pycache__"))
    built = _expand(LFCAL_INPUTS, "lfcal-scope-calibrator")
    (work / "instruments" / "lfcal-scope-calibrator.yaml").write_text(
        yaml.safe_dump(built, allow_unicode=True, sort_keys=False), "utf-8"
    )
    results = run_cases(work / "cases.yaml")
    failed = [(r.name, r.command, r.expected, r.actual, r.error) for r in results if not r.passed]
    assert results and not failed, failed


# ---- amplitude units ------------------------------------------------------------------------------


def _level(tmp_path, unit: str, amplitude: float, function: str, load_setting: str, load: float | None):
    definition = _expand(SOURCE.example(), "acme-cal-4")
    s = open_bench(_bench(tmp_path, definition, "CH1", load=load))
    try:
        for c in (
            f":CHANnel1:BASE:WAVe {function}", ":CHANnel1:BASE:FREQuency 1000", f":CHANnel1:AMPLitude:UNIT {unit}",
            f":CHANnel1:BASE:AMPLitude {amplitude}", f":CHANnel1:LOAD {load_setting}", ":CHANnel1:OUTPut ON",
        ):  # fmt: skip
            s.write("gen", c.encode() + b"\n")
        sig = (
            _net(s, "CH1") if load is not None else s.instruments["gen"].components["ch1.level"].output("out")
        )
        t = np.linspace(0, 1e-3, 20001)
        v = sig.sample_rel(0, t)
        return float(np.ptp(v)), float(np.sqrt(np.mean(v**2)))
    finally:
        s.close()


@pytest.mark.parametrize(
    ("unit", "amplitude", "function", "vpp"),
    [
        ("VPP", 1.0, "SINe", 1.0),
        ("VRMS", 1.0, "SINe", 2 * math.sqrt(2)),
        ("VRMS", 1.0, "SQUare", 2.0),
        ("DBM", 0.0, "SINe", 2 * math.sqrt(2) * math.sqrt(1e-3 * 50)),  # 0 dBm into 50 Ω: 0.2236 Vrms
        ("DBM", 10.0, "SINe", 2 * math.sqrt(2) * math.sqrt(1e-2 * 50)),
    ],
)
def test_amplitude_units_into_the_load_they_are_defined_for(tmp_path, unit, amplitude, function, vpp):
    # LOAD 50 into a real 50 Ω load: the load sees the amplitude as set.
    seen, rms = _level(tmp_path, unit, amplitude, function, "50", 50.0)
    if unit == "VRMS":  # the RMS the load sees (a square's peak-to-peak carries its Gibbs overshoot)
        assert rms == pytest.approx(amplitude, rel=2e-3)
    else:
        assert seen == pytest.approx(vpp, rel=2e-3)
    if unit == "DBM":
        power_dbm = 10 * math.log10(rms**2 / 50 / 1e-3)
        assert power_dbm == pytest.approx(amplitude, abs=0.02)


def test_dbm_with_a_high_z_setting_is_referred_to_50_ohm(tmp_path):
    # High-Z setting (10 kΩ and up for this source): the level is the open-circuit voltage, and dBm is
    # referred to 50 Ω, so 0 dBm reads 0.632 Vpp open circuit (twice what a 50 Ω load would see).
    open_vpp, _ = _level(tmp_path, "DBM", 0.0, "SINe", "INF", None)
    assert open_vpp == pytest.approx(2 * math.sqrt(2) * math.sqrt(1e-3 * 50), rel=2e-3)
    loaded_vpp, _ = _level(tmp_path, "DBM", 0.0, "SINe", "INF", 50.0)
    assert loaded_vpp == pytest.approx(open_vpp / 2, rel=2e-3)


# ---- the range in Vpp and the unit switch (D48) ------------------------------------------------------


def _tx(s, cmd: str) -> str | None:
    """One program message; the decoded reply for a query."""
    s.write("gen", cmd.encode() + b"\n")
    return s.read("gen").decode().strip() if "?" in cmd else None


def _err(s) -> str:
    reply = _tx(s, ":SYSTem:ERRor?") or ""
    return reply


def test_negative_dbm_is_writable_and_matches_the_formula(tmp_path):
    """The one limit is Vpp into the load setting: any dBm whose Vpp fits, negative included."""
    seen, rms = _level(tmp_path, "DBM", -10.0, "SINe", "50", 50.0)
    assert seen == pytest.approx(2 * math.sqrt(2) * math.sqrt(1e-4 * 50), rel=2e-3)
    assert 10 * math.log10(rms**2 / 50 / 1e-3) == pytest.approx(-10.0, abs=0.02)
    open_vpp, _ = _level(tmp_path, "DBM", -10.0, "SINe", "INF", None)  # high-Z: still referred to 50 Ω
    assert open_vpp == pytest.approx(2 * math.sqrt(2) * math.sqrt(1e-4 * 50), rel=2e-3)


@pytest.mark.parametrize(
    ("unit", "amplitude"),
    [("VPP", 11.0), ("VRMS", 6.0), ("DBM", -60.0)],  # 11 Vpp; 12 Vpp of a square; 0.63 mVpp
)
def test_an_out_of_range_write_says_the_bounds_in_its_unit(tmp_path, unit, amplitude):
    definition = _expand(SOURCE.example(), "acme-cal-4")
    s = open_bench(_bench(tmp_path, definition, "CH1", load=None))
    gen = s.instruments["gen"]
    try:
        _tx(s, f":CHANnel1:AMPLitude:UNIT {unit}")
        before = gen.get_state("ch1.amplitude")  # the switch already converted the power-on value
        _tx(s, f":CHANnel1:BASE:AMPLitude {amplitude}")
        err = _err(s)
        state = gen.get_state("ch1.amplitude")
    finally:
        s.close()
    per = 2.0  # the bounds go through the power-on shape's peak-to-RMS ratio (the example's square)
    # The bounds the message names are the Vpp range expressed in the written unit.
    if unit == "VPP":
        lo, hi = 0.001, 10.0
    elif unit == "VRMS":
        lo, hi = 0.001 / per, 10.0 / per
    else:
        lo = 10 * math.log10((0.001 / per) ** 2 / (1e-3 * 50))
        hi = 10 * math.log10((10.0 / per) ** 2 / (1e-3 * 50))
    assert err.startswith("-222") and "out of range" in err, err
    assert f"{lo:.6g}" in err and f"{hi:.6g}" in err and unit in err, err
    assert state == pytest.approx(before)  # the write was refused whole


def test_a_shape_or_load_change_that_breaks_the_vpp_range_is_refused(tmp_path):
    definition = _expand(SOURCE.example(), "acme-cal-4")
    s = open_bench(_bench(tmp_path, definition, "CH1", load=None))
    gen = s.instruments["gen"]
    try:
        _tx(s, ":CHANnel1:AMPLitude:UNIT VRMS")
        _tx(s, ":CHANnel1:BASE:AMPLitude 4.5")  # a square of 9 Vpp: in range
        _tx(s, ":CHANnel1:BASE:WAVe SINe")  # a sine of 12.7 Vpp: not
        assert "-221" in _err(s)
        assert gen.get_state("ch1.function") == "SQUare"
        _tx(s, ":CHANnel1:AMPLitude:UNIT DBM")
        _tx(s, ":CHANnel1:BASE:AMPLitude 15")  # 2.5 Vpp into 50 Ω
        _tx(s, ":CHANnel1:LOAD 1000")  # 11.2 Vpp into 1 kΩ: not
        assert "-221" in _err(s)
        assert gen.get_state("ch1.load") == 50.0
        _tx(s, ":CHANnel1:AMPLitude:UNIT VRMS")
        _tx(s, ":CHANnel1:BASE:WAVe SINe")  # 3.5 Vpp of a sine: in range
        _tx(s, ":CHANnel1:BASE:AMPLitude 0.0004")  # a sine of 1.13 mVpp: just above the floor
        _tx(s, ":CHANnel1:BASE:WAVe SQUare")  # 0.8 mVpp: below it
        assert "-221" in _err(s)
        assert gen.get_state("ch1.function") == "SINe"  # the shape is what the host wrote
    finally:
        s.close()


def test_switching_units_converts_the_value_and_keeps_the_wave(tmp_path):
    definition = _expand(SOURCE.example(), "acme-cal-4")
    s = open_bench(_bench(tmp_path, definition, "CH1", load=50.0))
    gen = s.instruments["gen"]
    try:
        _tx(s, ":CHANnel1:BASE:WAVe SINe")
        _tx(s, ":CHANnel1:BASE:FREQuency 1000")
        _tx(s, ":CHANnel1:BASE:AMPLitude 1.0")
        _tx(s, ":CHANnel1:OUTPut ON")
        t = np.linspace(0, 1e-3, 20001)

        def seen():
            return float(np.ptp(_net(s, "CH1").sample_rel(0, t)))

        assert seen() == pytest.approx(1.0, rel=2e-3)
        per = 2 * math.sqrt(2)
        for unit, value in [
            ("VRMS", 1 / per),
            ("DBM", 10 * math.log10((1 / per) ** 2 / (1e-3 * 50))),
            ("VPP", 1.0),
        ]:  # fmt: skip
            _tx(s, f":CHANnel1:AMPLitude:UNIT {unit}")
            # The state is the exact conversion; the reply carries the format's digits.
            assert gen.get_state("ch1.amplitude") == pytest.approx(value, rel=1e-9)
            reply = float(_tx(s, ":CHANnel1:BASE:AMPLitude?"))
            assert reply == pytest.approx(value, rel=1e-6)
            assert seen() == pytest.approx(1.0, rel=2e-3)  # the output did not move
    finally:
        s.close()


def test_a_refusal_after_the_conversion_rolls_the_value_back_too(tmp_path):
    """The unit watcher writes the converted value as a side effect; a later watcher's refusal must
    restore it together with the unit, not leave a value in the wrong unit (D48)."""
    definition = _expand(SOURCE.example(), "acme-cal-4")
    s = open_bench(_bench(tmp_path, definition, "CH1", load=None))
    gen = s.instruments["gen"]
    try:
        _tx(s, ":CHANnel1:BASE:AMPLitude 1.0")
        before = gen.get_state("ch1.amplitude")

        class Refuse:
            def on_state(self, path: str, old: Any, new: Any) -> None:
                if str(new).upper() == "DBM":
                    raise ScpiError(-221, "no dBm here")

        gen._watchers["ch1.amplitude_unit"].append(Refuse())
        _tx(s, ":CHANnel1:AMPLitude:UNIT DBM")
        assert "-221" in _err(s)
        assert gen.get_state("ch1.amplitude_unit") == "VPP"
        assert gen.get_state("ch1.amplitude") == pytest.approx(before)  # not the converted value
    finally:
        s.close()


def test_zero_vpp_has_no_dbm_expression(tmp_path):
    """The component's own guard, for definitions that let the amplitude reach 0."""
    definition = _expand(LFCAL_INPUTS, "tpl-lfcal")
    for comp in definition["channel_components"]:
        if comp["name"] == "level":
            comp["params"].pop("vpp_min")
            comp["params"].pop("vpp_max")
    s = open_bench(_bench(tmp_path, definition, "CH1", load=None))
    gen = s.instruments["gen"]
    try:
        _tx(s, ":CHANnel1:BASE:AMPLitude 0")
        _tx(s, ":CHANnel1:AMPLitude:UNIT DBM")
        assert "-221" in _err(s)
        assert gen.get_state("ch1.amplitude_unit") == "VPP"
        assert gen.get_state("ch1.amplitude") == 0.0
    finally:
        s.close()


def test_the_amplitude_range_lives_on_the_level_component():
    definition = _expand(SOURCE.example(), "acme-cal-4")
    state = definition["channel_state"]["amplitude"]
    assert "min" not in state and "max" not in state  # several units: no static range
    level = next(c for c in definition["channel_components"] if c["name"] == "level")
    assert level["params"]["vpp_min"] == 0.001 and level["params"]["vpp_max"] == 10.0
    vpp_only = copy.deepcopy(SOURCE.example())
    vpp_only["amplitude"]["units"] = ["VPP"]
    built = _expand(vpp_only, "vpp-only")
    assert built["channel_state"]["amplitude"]["min"] == 0.001  # one unit: the static range stays
    assert built["channel_state"]["amplitude"]["max"] == 10.0
    level = next(c for c in built["channel_components"] if c["name"] == "level")
    assert level["params"]["vpp_min"] == 0.001 and level["params"]["vpp_max"] == 10.0


# ---- the duty as declared ---------------------------------------------------------------------------


@pytest.mark.parametrize(("freq", "truncated"), [(10e3, True), (5.5e6, False)])
def test_a_0_1_percent_pulse(tmp_path, freq, truncated):
    """0.1 % written as a percentage is 0.1 %, at both ends of the calibration's frequency range."""
    definition = _expand(SOURCE.example(), "acme-cal-4")  # duty in percent
    s = open_bench(_bench(tmp_path, definition, "CH1", load=None))
    try:
        for c in (
            ":CHANnel1:BASE:WAVe PULSe", f":CHANnel1:BASE:FREQuency {freq}", ":CHANnel1:BASE:DUTY 0.1",
            ":CHANnel1:BASE:AMPLitude 0.01", ":CHANnel1:LOAD INF", ":CHANnel1:OUTPut ON",
        ):  # fmt: skip
            s.write("gen", c.encode() + b"\n")
        assert s.instruments["gen"].get_state("ch1.duty") == pytest.approx(0.1)
        sig = s.instruments["gen"].components["ch1.level"].output("out")
    finally:
        s.close()
    # The mean is exact whatever the bandwidth: ±5 mV for 0.1 % of the period, -5 mV otherwise.
    assert sig.mean() == pytest.approx(0.005 * (2 * 0.001 - 1), abs=1e-12)
    notes = {n.kind for n in sig.notes}
    if truncated:
        # 1 GHz of harmonics at 10 kHz is 100 000 lines: the line limit cuts it, and the signal says so.
        assert sig.freq.size <= MAX_LINES and "truncated" in notes
        period = 1 / freq
        t = np.linspace(0, period, 200001)
        high = np.mean(sig.sample_rel(0, t) > 0)
        assert high == pytest.approx(0.001, rel=0.05)  # 100 ns of 100 µs
    else:
        # 5.5 MHz: harmonics stop at the source's own 1 GHz bandwidth: the spectrum is exact, no note.
        assert "truncated" not in notes
        k = np.arange(1, int(1e9 // freq) + 1)
        x = 2 * np.pi * k * 0.001
        expected = (2 * 0.005 / (np.pi * k)) * np.abs(np.sin(x) - 1j * (1 - np.cos(x)))
        lines = dict(zip(np.round(sig.freq / freq).astype(int), np.abs(sig.amp), strict=True))
        got = np.array([lines.get(int(n), 0.0) for n in k])
        np.testing.assert_allclose(got, expected, rtol=1e-9, atol=1e-15)


def test_a_ratio_source_keeps_0_001_as_0_1_percent(tmp_path):
    built = _expand(LFCAL_INPUTS, "tpl-lfcal")
    s = open_bench(_bench(tmp_path, built, "CH1", load=None))
    try:
        s.write("gen", b":CHANnel1:BASE:DUTY 0.001\n")
        synth = s.instruments["gen"].components["ch1.synth"]
        assert synth.duty_ratio() == pytest.approx(0.001)
        s.write("gen", b":CHANnel1:BASE:DUTY 40\n")
        s.write("gen", b":SYSTem:ERRor?\n")
        assert synth.duty_ratio() == pytest.approx(0.001)  # 40 is out of the ratio's range, not 0.4
    finally:
        s.close()


# ---- reference clock ------------------------------------------------------------------------------


def test_the_reference_clock_offsets_the_frequency_and_deviates_by_seed(tmp_path):
    definition = _expand(SOURCE.example(), "acme-cal-4")
    path = _bench(tmp_path, definition, "CH1", load=None)
    data = yaml.safe_load(path.read_text("utf-8"))
    data["instruments"]["gen"]["deviation"] = "from_specs"

    def drawn(seed: int) -> tuple[float, float]:
        data["run"]["seed"] = seed
        path.write_text(yaml.safe_dump(data), "utf-8")
        s = open_bench(path)
        try:
            s.write("gen", b":CHANnel1:BASE:WAVe SINe;:CHANnel1:BASE:FREQuency 1E6\n")
            sig = s.instruments["gen"].components["ch1.synth"].output("out")
            return s.deviations()["gen/clock.ppm"], float(sig.freq[0])
        finally:
            s.close()

    (ppm, f), (again, _), (other, _) = drawn(3), drawn(3), drawn(4)
    assert ppm == again != other and abs(ppm) <= 2.0
    assert f == pytest.approx(1e6 * (1 + ppm * 1e-6), rel=1e-12)


# ---- wrong inputs ------------------------------------------------------------------------------------


def _errors(change) -> list[tuple[str, str]]:
    inputs = copy.deepcopy(SOURCE.example())
    change(inputs)
    out = SOURCE.expand(inputs, "bad")
    assert out.definition is None
    return [(d.code, d.pointer) for d in out.diagnostics]


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        pytest.param(lambda x: x["duty"].update(unit="ratio"), ("E-TPL-RANGE", "/duty/max"), id="percent-range-as-ratio"),
        pytest.param(lambda x: x.pop("duty"), ("E-TPL-MISSING", "/duty"), id="pulse-without-duty"),
        pytest.param(lambda x: x["frequency"].update(default=1e9), ("E-TPL-DEFAULT", "/frequency/default"), id="default-out-of-range"),
        pytest.param(lambda x: x["amplitude"].update(min=20.0), ("E-TPL-RANGE", "/amplitude/max"), id="min-above-max"),
        pytest.param(lambda x: x["waveforms"][1].update(name="SQUare"), ("E-TPL-DUPLICATE", "/waveforms/1/name"), id="duplicate-keyword"),
        pytest.param(lambda x: x["clock"].update(sources=["INTernal"]), ("E-TPL-MISSING", "/clock/sources"), id="ref-input-one-source"),
        pytest.param(lambda x: x["amplitude"].update(units=["VPP", "MW"]), ("E-TPL-INPUT", "/amplitude/units/1"), id="unknown-unit"),
        pytest.param(lambda x: x["amplitude"].update(min=0.0), ("E-TPL-RANGE", "/amplitude/min"), id="dbm-needs-positive-min"),
        pytest.param(
            lambda x: x["amplitude"].update(units=["VRMS", "VPP", "DBM"], default=6.0),
            ("E-TPL-DEFAULT", "/amplitude/default"), id="power-on-default-over-the-vpp-range",
        ),  # 6 Vrms of the power-on square is 12 Vpp
        pytest.param(lambda x: x["waveforms"][0].update(shape="noise"), ("E-TPL-INPUT", "/waveforms/0/shape"), id="unknown-shape"),
    ],
)  # fmt: skip
def test_a_wrong_input_is_named_by_its_pointer(change, expected):
    code, path = expected
    assert (code, INPUTS + path) in _errors(change)
