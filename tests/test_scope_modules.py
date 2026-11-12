"""The oscilloscope built from modules (instrument-authoring.md 8.4, issue 133): timebase, trigger,
ADC group, and per channel the vertical and probe modules; the earlier one-component acquisition
expands into them when a definition loads (a macro), so both forms are the same definition."""

from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
import pytest

from simubench.engine.record import definition_hash
from simubench.engine.session import Session
from simubench.engine.validate_definition import validate_definition_data
from simubench.library.components.scope import _boxcar, peak_detect
from simubench.library.loader import load_yaml
from simubench.models.bench import BenchDefinition
from simubench.models.definition import InstrumentDefinition
from simubench.scpi.errors import ScpiError

ROOT = Path(__file__).resolve().parents[1]
DSO4 = load_yaml((ROOT / "validation" / "instruments" / "sb-dso-4.yaml").read_text(encoding="utf-8"))


def one_component(explicit: dict) -> dict:
    """The same scope written the earlier way: one acquisition, a front end, no probe module."""
    data = copy.deepcopy(explicit)
    data["components"] = [c for c in data["components"] if c["name"] not in ("timebase", "trigger", "adc")]
    next(c for c in data["components"] if c["name"] == "acq").pop("params")
    data["channel_components"] = [c for c in data["channel_components"] if c["name"] != "probe"]
    next(c for c in data["channel_components"] if c["name"] == "front")["type"] = "scope_frontend"
    data["wiring"] = {"channel": [["inp.out", "front.in"]], "instrument": [["ch{n}.front.out", "acq.in{n}"]]}
    return data


def codes(data: dict) -> list[tuple[str, str]]:
    _, report = validate_definition_data(data)
    return [(d.code, d.pointer) for d in report.diagnostics]


def test_the_one_component_form_is_the_same_definition():
    explicit = InstrumentDefinition.model_validate(DSO4)
    expanded = InstrumentDefinition.model_validate(one_component(DSO4))
    assert definition_hash(expanded) == definition_hash(explicit)
    assert codes(one_component(DSO4)) == codes(DSO4) == []


def test_expansion_is_idempotent_and_leaves_the_input_alone():
    earlier = one_component(DSO4)
    before = copy.deepcopy(earlier)
    once = InstrumentDefinition.model_validate(earlier)
    assert earlier == before  # the caller's data is not rewritten
    again = InstrumentDefinition.model_validate(once.model_dump(by_alias=True))
    assert definition_hash(again) == definition_hash(once)


def test_a_macro_that_cannot_expand_is_reported():
    taken = one_component(DSO4)
    taken["components"].append({"name": "trigger", "type": "system"})  # the trigger module's name
    assert codes(taken) == [("E-DEF-MACRO", "/components")]
    unwired = one_component(DSO4)
    unwired["wiring"]["instrument"] = []
    assert codes(unwired) == [("E-DEF-MACRO", "/components")]


def test_module_references_are_checked():
    bad = copy.deepcopy(DSO4)
    bad["components"][0]["params"]["horizontal"] = "timbase"
    assert codes(bad) == [("E-DEF-COMPONENT-REF", "/components/0/params/horizontal")]
    _, report = validate_definition_data(bad)
    assert report.diagnostics[0].suggestion == "改为 'timebase'"
    bad = copy.deepcopy(DSO4)
    bad["components"][0]["params"]["adc"] = "xfer"  # a component, but not an ADC group
    assert codes(bad) == [("E-DEF-COMPONENT-REF", "/components/0/params/adc")]
    bad = copy.deepcopy(DSO4)
    bad["components"][0]["params"]["probe"] = "front"  # a channel component of another kind
    assert codes(bad) == [("E-DEF-COMPONENT-REF", "/components/0/params/probe")]


def _scope(vertical: dict) -> Session:
    """A one-scope bench whose channels' vertical modules take ``vertical`` as parameters."""
    data = copy.deepcopy(DSO4)
    data["id"] = "dso-ranges"
    front = next(c for c in data["channel_components"] if c["name"] == "front")
    front["params"].update(vertical)
    definition = InstrumentDefinition.model_validate(data)
    bench = BenchDefinition.model_validate(
        {"schema": "simubench.bench/2", "name": "ranges", "instruments": {"scope": {"model": "dso-ranges"}}}
    )
    return Session(bench, {"dso-ranges": definition}.__getitem__, clock="command", seed=1, idle="off")


def test_scale_and_offset_ranges_at_the_input():
    session = _scope(
        {
            "probe_ratio": "$ch.probe",
            "scale_range": [0.001, 10.0],
            "offset_ranges": [[0.1, 1.0], [10.0, 100.0]],
        }
    )
    scope = session.instruments["scope"]
    scope.set_state("ch1.scale", 0.05)
    with pytest.raises(ScpiError) as err:
        scope.set_state("ch1.scale", 20.0)  # above 10 V/div at the input
    assert err.value.code == -222 and scope.get_state("ch1.scale") == 0.05  # the old value stays
    with pytest.raises(ScpiError):
        scope.set_state("ch1.offset", 2.0)  # 1 V at 0.05 V/div
    scope.set_state("ch1.probe", 10.0)  # the display values include the probe: not checked
    scope.set_state("ch1.scale", 50.0)  # 5 V/div at the input
    scope.set_state("ch1.offset", 20.0)  # 2 V at the input, within 100 V at 5 V/div
    assert (scope.get_state("ch1.scale"), scope.get_state("ch1.offset")) == (50.0, 20.0)
    session.close()


def test_without_ranges_any_scale_is_taken():
    session = _scope({})
    scope = session.instruments["scope"]
    scope.set_state("ch1.scale", 100.0)
    assert scope.get_state("ch1.scale") == 100.0
    session.close()


@pytest.mark.unit
def test_the_boxcar_keeps_the_sample_count_and_is_centered():
    """High resolution's smoother (F4/D46): the mean of ``w`` samples at every sample — the length
    does not shrink (#971), the window is centered (a ramp's w-point mean sits on its window's
    center), at the edges the edge sample is repeated to fill the window."""
    ramp = np.arange(10.0)
    for w in (2, 3, 4, 7):
        assert len(_boxcar(ramp, w)) == len(ramp)
    assert _boxcar(ramp, 1) is ramp  # no smoothing, nothing to compute
    assert _boxcar(ramp, 3)[1:-1] == pytest.approx(ramp[1:-1])  # odd window: centered on the sample
    assert _boxcar(ramp, 4)[2:-2] == pytest.approx(ramp[2:-2] - 0.5)  # even window: half a step left
    assert _boxcar(ramp, 3)[0] == pytest.approx((0.0 + 0.0 + 1.0) / 3)  # the edge sample repeated
    spike = np.zeros(9)
    spike[4] = 1.0
    out = _boxcar(spike, 3)
    assert out[3] == out[5] == pytest.approx(1.0 / 3)  # a symmetric input, symmetric output


@pytest.mark.unit
def test_peak_detect_keeps_a_pulse_the_record_rate_would_step_over():
    """Peak detect examines each record interval at the ADC's native rate: of the interval's
    smallest and largest value the one that departs farther from its ends — a one-sample pulse
    inside the interval is kept, a flat interval keeps its end value (#971)."""
    m = 8
    grid = np.zeros(4 * m)
    grid[2 * m + 3] = 1.0  # one native sample of interval 2: a pulse the record rate steps over
    assert peak_detect(grid, m)[2] == 1.0
    assert peak_detect(grid, m)[[0, 1, 3]] == pytest.approx(0.0)
    grid[2 * m + 3] = -1.0  # the same pulse downward
    assert peak_detect(grid, m)[2] == -1.0
