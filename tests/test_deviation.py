"""Individual deviations (个体偏差) drawn from the bench seed within the data sheet's ranges (#34,
metrology-authoring.md 7)."""

from __future__ import annotations

import json

import pytest

from simubench.api import open_bench
from simubench.engine.deviation import draw
from simubench.engine.edit import SetDeviation, apply
from simubench.engine.replay import replay
from simubench.engine.validate import validate_bench
from simubench.engine.validate_definition import validate_definition
from simubench.library.loader import DefinitionLibrary
from simubench.models.bench import BenchDefinition
from simubench.models.definition import InstrumentDefinition
from simubench.models.deviation import DeviationRange


def bench(seed: int = 1, deviation: str = "from_specs", **extra) -> BenchDefinition:
    data = {
        "name": "dev",
        "instruments": {
            "awg": {"model": "sb-awg-2", "deviation": deviation},
            "dmm": {"model": "sb-dmm-1", "deviation": deviation},
        },
        "duts": {
            "r1": {
                "type": "resistor",
                "params": {"r": 1000.0},
                "deviation": {"r": {"limit": 1, "unit": "%"}},
            },
        },
        "run": {"seed": seed, "clock": "command"},
    }
    for key, value in extra.items():
        data[key] = value
    return BenchDefinition.model_validate(data)


def deviations(b: BenchDefinition) -> dict:
    s = open_bench(b)
    try:
        return s.deviations()
    finally:
        s.close()


def test_same_seed_same_unit_other_seed_other_unit():
    first, again, other = deviations(bench(1)), deviations(bench(1)), deviations(bench(2))
    assert first == again
    assert set(first) == {
        "awg/ch1.out.z_out",
        "awg/ch2.out.z_out",
        "dmm/meter.gain_error_ppm",
        "dmm/meter.offset",
        "r1.r",
    }
    assert all(first[k] != other[k] for k in first)
    assert first["awg/ch1.out.z_out"] != first["awg/ch2.out.z_out"]  # one value per channel


def test_every_value_is_inside_its_range():
    for seed in range(200):
        d = deviations(bench(seed))
        assert abs(d["dmm/meter.gain_error_ppm"]) <= 20.0
        assert abs(d["dmm/meter.offset"]) <= 5e-6  # normal, truncated at the limit
        assert all(49.0 <= d[f"awg/ch{n}.out.z_out"] <= 51.0 for n in (1, 2))  # 50 Ω ± 2 %
        assert 990.0 <= d["r1.r"] <= 1010.0


def test_normal_draws_cluster_like_their_sigma():
    rng = DeviationRange(limit=2.0, distribution="normal", coverage=2)
    values = [draw(rng, 0.0, seed, "x") for seed in range(4000)]
    sigma = (sum(v * v for v in values) / len(values)) ** 0.5
    assert 0.85 < sigma < 1.0  # σ = 1, a little less after truncation at 2σ
    assert max(abs(v) for v in values) <= 2.0


def test_a_unit_does_not_depend_on_the_rest_of_the_bench():
    full = deviations(bench(7))
    alone = BenchDefinition.model_validate(
        {
            "name": "dev",
            "instruments": {"dmm": {"model": "sb-dmm-1", "deviation": "from_specs"}},
            "run": {"seed": 7},
        }
    )
    only = deviations(alone)
    assert only == {k: v for k, v in full.items() if k.startswith("dmm/")}


def test_hidden_values_win_and_nominal_draws_nothing():
    b = bench(1)
    b.instruments["dmm"].hidden = {"meter": {"gain_error_ppm": 150.0}}
    b.duts["r1"].hidden = {"r": 1002.0}
    d = deviations(b)
    assert "dmm/meter.gain_error_ppm" not in d and "r1.r" not in d
    assert "dmm/meter.offset" in d
    nominal = bench(1, deviation="nominal")
    nominal.duts["r1"].deviation = {}
    assert deviations(nominal) == {}


def test_the_drawn_error_is_what_the_meter_reads():
    b = bench(3)
    b.instruments["src"] = BenchDefinition.model_validate(
        {"name": "x", "instruments": {"src": {"model": "sb-smu-1"}}}
    ).instruments["src"]
    b.instruments["src"].state = {
        "source.voltage.level": 10.0,
        "compliance.current.limit": 0.01,
        "output.enabled": True,
    }
    b.wires = BenchDefinition.model_validate(
        {"name": "x", "wires": [{"from": "src.HI", "to": "dmm.IN"}]}
    ).wires
    s = open_bench(b)
    try:
        d = s.deviations()
        s.write("dmm", b":MEAS:VOLT?\n")
        reading = float(s.read("dmm").decode())
    finally:
        s.close()
    true_v = 10.0 * 10e6 / (10e6 + 0.0)  # the SMU holds 10 V at its terminal
    expect = true_v * (1 + d["dmm/meter.gain_error_ppm"] * 1e-6) + d["dmm/meter.offset"]
    assert reading == pytest.approx(expect, abs=2e-6)


def test_tables_draw_one_value_per_cell():
    rng = DeviationRange(limit={0.1: 30, 1: 20, 10: 15}, by={"function": ["DC"], "range": [0.1, 1, 10]})
    table = draw(rng, 0.0, 1, "dmm", "meter", "gain_error_ppm")
    assert set(table) == {"DC"} and set(table["DC"]) == {0.1, 1, 10}
    assert abs(table["DC"][10]) <= 15 and abs(table["DC"][0.1]) <= 30
    assert len(set(table["DC"].values())) == 3
    # Relative to a nominal table: each cell scales its own nominal value.
    rel = DeviationRange(limit=1.0, unit="%", by={"range": [1, 10]})
    scaled = draw(rel, {1: 100.0, 10: 1000.0}, 1, "x")
    assert 99.0 <= scaled[1] <= 101.0 and 990.0 <= scaled[10] <= 1010.0
    with pytest.raises(ValueError, match="no limit"):
        DeviationRange(limit={0.1: 30}, by={"range": [0.1, 1]})


def test_the_record_carries_the_draws_and_a_replay_checks_them(tmp_path):
    s = open_bench(bench(5), record_dir=tmp_path)
    s.write("dmm", b"*IDN?\n")
    s.read("dmm")
    drawn = s.deviations()
    s.close()
    [record] = tmp_path.glob("*.jsonl")
    header = json.loads(record.read_text("utf-8").splitlines()[0])
    assert header["deviations"] == pytest.approx(drawn)
    report = replay(record)
    assert report.ok and not [w for w in report.warnings if w.startswith("deviation")]
    # A record whose draws differ (other ranges when it was made) is replayed with a warning.
    lines = record.read_text("utf-8").splitlines()
    head = json.loads(lines[0])
    head["deviations"]["dmm/meter.offset"] = 1.0
    record.write_text("\n".join([json.dumps(head), *lines[1:]]) + "\n", "utf-8")
    assert any(w.startswith("deviation dmm/meter.offset") for w in replay(record).warnings)


def test_validation_names_wrong_deviations():
    lib = DefinitionLibrary()
    dmm = lib.get("sb-dmm-1").model_dump(by_alias=True)
    dmm["deviations"] = [
        {"param": "metre.offset", "limit": 1e-6},
        {"param": "meter.ofset", "limit": 1e-6},
        {"param": "meter.offset", "limit": 1, "unit": "%"},
    ]
    report = validate_definition(InstrumentDefinition.model_validate(dmm))
    codes = [(d.code, d.pointer) for d in report.diagnostics]
    assert ("E-DEF-DEVIATION", "/deviations/0/param") in codes
    assert ("E-DEF-DEVIATION", "/deviations/1/param") in codes
    assert ("W-DEF-DEVIATION-ZERO", "/deviations/2/unit") in codes  # % of a 0 V offset does nothing
    assert not validate_definition(lib.get("sb-dmm-1")).diagnostics

    b = bench(1)
    b.instruments["src"] = b.instruments["dmm"].model_copy(update={"model": "sb-smu-1"})
    b.duts["r1"].deviation["rr"] = DeviationRange(limit=1)
    found = {(d.code, d.pointer) for d in validate_bench(b, lib.get).diagnostics}
    assert ("W-DEVIATION-NONE", "/instruments/src/deviation") in found
    assert ("E-DEVIATION-PARAM", "/duts/r1/deviation/rr") in found


def test_edit_sets_the_mode_and_a_dut_range():
    b = bench(1, deviation="nominal")
    b = apply(
        b,
        [
            SetDeviation(instrument="dmm", mode="from_specs"),
            SetDeviation(dut="r1", param="r", range=DeviationRange(limit=5, unit="%")),
        ],
    )
    assert b.instruments["dmm"].deviation == "from_specs" and b.duts["r1"].deviation["r"].limit == 5
    b = apply(b, [SetDeviation(dut="r1", param="r", range=None)])
    assert b.duts["r1"].deviation == {}
    with pytest.raises(ValueError):
        apply(b, [SetDeviation(instrument="dmm")])


def test_the_session_view_hides_drawn_values_unless_revealed(tmp_path):
    from pathlib import Path

    from simubench.server.manager import ServedSession, SessionManager

    manager = SessionManager(
        Path(__file__).parents[1] / "validation", scpi_base_port=None, record_dir=tmp_path
    )
    for reveal in (False, True):
        b = bench(1)
        b.run.reveal_true_values = reveal
        session = open_bench(b)
        try:
            view = manager.describe(ServedSession("s1", session, None))
        finally:
            session.close()
        dmm = next(i for i in view.instruments if i.name == "dmm")
        awg = next(i for i in view.instruments if i.name == "awg")
        r1 = next(d for d in view.duts if d.name == "r1")
        assert dmm.deviation == "from_specs"
        assert [x.param for x in dmm.deviations] == ["meter.gain_error_ppm", "meter.offset"]
        assert [x.param for x in awg.deviations] == ["ch1.out.z_out", "ch2.out.z_out"]
        assert all(x.drawn for x in dmm.deviations + awg.deviations + r1.deviations)
        if reveal:
            assert (
                all(x.value is not None for x in dmm.deviations) and r1.params["r"] == r1.deviations[0].value
            )
            assert r1.params["r"] != 1000.0
        else:
            assert all(x.value is None for x in dmm.deviations + r1.deviations) and r1.params["r"] == 1000.0
