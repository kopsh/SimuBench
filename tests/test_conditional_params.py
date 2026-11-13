"""Several settings decide one parameter (D70, #1020): nested parameter mappings and individual
deviations for some settings (``when``), and the N9042B's preamplifier built on them."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from helpers import session_query
from simubench.api import open_bench
from simubench.engine.deviation import case_label
from simubench.engine.validate_definition import validate_definition_data
from simubench.library.loader import DefinitionLibrary
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
N9042B = ROOT / "src" / "simubench" / "library" / "instruments" / "keysight-n9042b.yaml"
DATA = yaml.safe_load(N9042B.read_text("utf-8"))
#: NF = DANL (1 Hz, typical) + 176.24 dB, the conversion of the definition's tables.
TO_NF = 176.24


def data() -> dict:
    return copy.deepcopy(DATA)


def codes(d: dict) -> list[tuple[str, str]]:
    _, report = validate_definition_data(d)
    return [(x.code, x.pointer) for x in report.diagnostics if x.severity == "error"]


def bench(options: list[str], deviation: str = "nominal", seed: int = 3, **sa) -> BenchDefinition:
    unit = {"model": "keysight-n9042b", "options": options, "deviation": deviation, **sa}
    return BenchDefinition.model_validate({"name": "t", "instruments": {"sa": unit}, "run": {"seed": seed}})


# ---- the definition language ----------------------------------------------------------------------


def test_a_reference_inside_a_nested_mapping_is_checked_where_it_is_written():
    d = data()
    params = d["options"][0]["params"]
    params["input"]["preamp_gain"]["map"]["True"]["map"]["LOW"]["from"] = "$freq.centre"
    params["mixer"]["iip3_dbm"]["map"]["True"]["map"]["LOW"]["table"][3.6e9] = "$power.gian"
    assert codes(d) == [
        ("E-DEF-STATE-UNKNOWN", "/options/0/params/input/preamp_gain/map/True/map/LOW/from"),
        ("E-DEF-STATE-UNKNOWN", "/options/0/params/mixer/iip3_dbm/map/True/map/LOW/table/3600000000.0"),
    ]


def test_a_nested_mapping_must_be_well_formed():
    d = data()
    d["options"][0]["params"]["input"]["preamp_gain"]["map"]["True"]["map"]["FULL"] = {
        "from": "$freq.center",
        "tabel": {},
    }
    assert [c for c, _ in codes(d)] == ["E-SCHEMA"]


def test_the_settings_inside_a_nested_table_reach_the_component():
    s = open_bench(bench(["P26"]))
    try:
        sa = s.instruments["sa"]
        for path in ("power.gain", "power.gain_band", "freq.center"):
            assert sa.components["input"] in sa._watchers[path]
    finally:
        s.close()


def test_a_deviation_for_some_settings_must_name_settings_and_values_that_exist():
    d = data()
    d["deviations"][1]["when"] = {"power.gian": True}
    assert ("E-DEF-DEVIATION-WHEN", "/deviations/1/when/power.gian") in codes(d)
    d = data()
    d["deviations"][1]["when"] = {"power.gain_band": "MID"}
    assert ("E-DEF-DEVIATION-WHEN", "/deviations/1/when/power.gain_band") in codes(d)
    d = data()
    d["deviations"][2]["when"] = dict(d["deviations"][1]["when"])
    assert ("E-DEF-DEVIATION-WHEN", "/deviations/2/when") in codes(d)
    d = data()
    d["deviations"].append(copy.deepcopy(d["deviations"][0]))
    assert ("E-DEF-DEVIATION-WHEN", "/deviations/3/param") in codes(d)
    d = data()
    d["deviations"][1]["option"] = "P07"
    assert ("E-DEF-OPTION", "/deviations/1/option") in codes(d)


# ---- individual deviations for some settings --------------------------------------------------------


def _curves(options: list[str], seed: int = 3, **sa) -> tuple[dict, object]:
    s = open_bench(bench(options, "from_specs", seed, **sa))
    return s.deviations(), s


def test_each_settings_case_draws_its_own_curve_and_the_parameter_follows_the_settings():
    """Option P26 adds two responses with the PA on (bands FULL and LOW); the parameter is the
    first case whose settings hold, else the standard path's curve. A unit without the option
    draws only the standard path's — the same curve the P26 unit draws for it (its stream did
    not move)."""
    drawn, s = _curves(["P26"])
    try:
        flat = s.instruments["sa"].components["input"]
        off, full, low = (
            drawn["sa/input.flatness"],
            drawn["sa/input.flatness[power.gain=ON,power.gain_band=FULL]"],
            drawn["sa/input.flatness[power.gain=ON,power.gain_band=LOW]"],
        )
        assert len({str(off), str(full), str(low)}) == 3
        assert flat.param("flatness") == off
        session_query(s, "sa", ":POW:GAIN ON")
        assert flat.param("flatness") == low  # the preset band
        session_query(s, "sa", ":POW:GAIN:BAND FULL")
        assert flat.param("flatness") == full
        session_query(s, "sa", ":POW:GAIN OFF")
        assert flat.param("flatness") == off
        assert "flatness" in flat.unit_values
    finally:
        s.close()
    plain, s = _curves([])
    s.close()
    assert plain == {"sa/input.flatness": off}
    again, s = _curves(["P26"])
    s.close()
    assert again == drawn
    other, s = _curves(["P26"], seed=4)
    s.close()
    assert other["sa/input.flatness[power.gain=ON,power.gain_band=FULL]"] != full


def test_each_case_stays_inside_its_own_data_sheet_bands():
    drawn, s = _curves(["P26"], seed=11)
    s.close()
    for dev in DefinitionLibrary().get("keysight-n9042b").deviations:
        key = f"sa/input.flatness[{case_label(dev.when)}]" if dev.when else "sa/input.flatness"
        assert isinstance(dev.limit, list) and dev.bands is not None
        for f, d in drawn[key]:
            held = [lim for (lo, hi), lim in zip(dev.bands, dev.limit, strict=True) if lo <= f <= hi]
            assert held and abs(d) <= min(held) + 1e-12, (key, f, d)
        assert dict((f, d) for f, d in drawn[key])[dev.reference] == 0.0


def test_a_hidden_value_beats_every_case():
    hidden = [[1.0e9, 0.3]]
    drawn, s = _curves(["P26"], hidden={"input": {"flatness": hidden}})
    try:
        assert drawn == {}
        flat = s.instruments["sa"].components["input"]
        assert flat.param("flatness") == hidden
        session_query(s, "sa", ":POW:GAIN ON")
        assert flat.param("flatness") == hidden
    finally:
        s.close()


def test_the_session_view_lists_each_case_and_hides_its_value(tmp_path):
    from simubench.server.manager import SessionManager

    s = open_bench(bench(["P26"], "from_specs"))
    try:
        views = SessionManager(tmp_path, scpi_base_port=None).views
        info = views.describe_instrument(s, s.instruments["sa"], 0, None)
    finally:
        s.close()
    assert [d.param for d in info.deviations] == [
        "input.flatness",
        "input.flatness[power.gain=ON,power.gain_band=FULL]",
        "input.flatness[power.gain=ON,power.gain_band=LOW]",
    ]
    assert all(d.drawn and d.value is None for d in info.deviations)


# ---- the N9042B's preamplifier (PA, option P26) ------------------------------------------------------


@pytest.mark.parametrize(
    ("commands", "center", "danl"),
    [
        ([], 1e9, -155.0),  # 1a, p.21
        ([":POW:GAIN ON"], 1e9, -166.0),  # 1c, p.22; the preset band LOW covers 1 GHz
        ([":POW:GAIN ON"], 10e9, -149.0),  # band LOW: no preamp above 3.6 GHz, 1a
        ([":POW:GAIN ON", ":POW:GAIN:BAND FULL"], 10e9, -166.0),  # 1c
        ([":POW:GAIN ON", ":POW:GAIN:BAND FULL"], 20e9, -163.0),  # 1c > 20 to 26.5 GHz
        ([":POW:GAIN ON"], 150e3, -160.0),  # 1c > 100 to 200 kHz
    ],
)
def test_the_noise_figure_follows_the_path_the_preamp_settings_choose(commands, center, danl):
    s = open_bench(bench(["P26"]))
    try:
        for c in [*commands, f":FREQ:CENT {center}", ":POW:ATT 0"]:
            session_query(s, "sa", c)
        assert s.instruments["sa"].components["sa"].noise_figure() == pytest.approx(danl + TO_NF)
    finally:
        s.close()


def test_with_the_preamp_on_the_path_figures_are_the_data_sheets_1c_ones():
    """SHI, TOI and 1 dB compression of path 1c (p.19, 26, 27), at the analyzer input with 0 dB
    attenuation; the mixer's 1a figures are not in the path while the PA amplifies."""
    s = open_bench(bench(["P26"]))
    try:
        trace = s.instruments["sa"].components["sa"]
        for c in (":FREQ:CENT 1 GHz", ":POW:ATT 0"):
            session_query(s, "sa", c)
        off = trace._cascade()
        assert off is not None and (off.iip3_dbm, off.p1db_dbm) == (19.0, 5.0)
        session_query(s, "sa", ":POW:GAIN ON")
        on = trace._cascade()
        assert on is not None
        assert on.iip2_dbm == pytest.approx(28.0 - 6.02)
        assert (on.iip3_dbm, on.p1db_dbm, on.gain_db) == (2.0, -14.0, 30.0)
    finally:
        s.close()


def test_the_automatic_attenuation_counts_the_preamp_gain():
    """Atten = RefLevel + PreAmpGain − MaxMixerLevel (reference p.550): 0 dBm and the −10 dBm
    mixer level give 10 dB, with the 30 dB PA 40 dB."""
    s = open_bench(bench(["P26"]))
    try:
        session_query(s, "sa", ":FREQ:CENT 1 GHz")  # inside the preset band LOW
        assert float(session_query(s, "sa", ":POW:ATT?")) == 10.0
        session_query(s, "sa", ":POW:GAIN ON")
        assert float(session_query(s, "sa", ":POW:ATT?")) == 40.0
        session_query(s, "sa", ":FREQ:CENT 10 GHz")  # band LOW: the PA is out of the path
        assert float(session_query(s, "sa", ":POW:ATT?")) == 10.0
    finally:
        s.close()


def test_opt_lists_the_preamp_and_a_unit_without_it_refuses_its_commands():
    s = open_bench(bench(["P26"]))
    try:
        assert session_query(s, "sa", "*OPT?") == "526,P26"
    finally:
        s.close()
    s = open_bench(bench([]))
    try:
        assert session_query(s, "sa", "*OPT?") == "526"
        for command in (":POW:GAIN ON", ":POW:GAIN?", ":POW:GAIN:BAND FULL", ":POW:GAIN:BAND?"):
            s.write("sa", (command + "\n").encode())
            assert session_query(s, "sa", ":SYST:ERR?").startswith("-241"), command
    finally:
        s.close()
