"""One value in one place (#1426, D82): ``{same_as: <path>}`` resolved when a definition loads."""

from __future__ import annotations

import pytest
import yaml

from helpers import raises_with
from simubench.api import open_bench
from simubench.library.loader import DefinitionLibrary
from simubench.library.same_as import SameAsError, resolve_same_as
from simubench.models.bench import BenchDefinition

# ---- the resolution itself ---------------------------------------------------------------------------


def test_a_reference_takes_the_value_it_names():
    data = {
        "model": "X1",
        "specs": {"bandwidth_hz": 1.0e9, "rates": [1.0e9, 2.0e9]},
        "state": {
            "acq.bw": {"max": {"same_as": "specs.bandwidth_hz"}, "default": {"same_as": "state.acq.bw.max"}}
        },
        "components": [{"name": "adc", "params": {"rate": {"same_as": "specs.rates.1"}}}],
        "persona": {"same_as": "model"},
    }
    out = resolve_same_as(data)
    assert out["state"]["acq.bw"] == {"max": 1.0e9, "default": 1.0e9}  # a dotted key, a chain
    assert out["components"][0]["params"]["rate"] == 2.0e9  # a list item by index
    assert out["persona"] == "X1"
    assert data["persona"] == {"same_as": "model"}  # the input is not changed


def test_the_template_record_and_runtime_references_are_left_as_written():
    data = {"template": {"inputs": {"x": {"same_as": "nope"}}}, "params": {"bw": "$acquire.bandwidth"}}
    assert resolve_same_as(data) is data


@pytest.mark.parametrize(
    ("value", "words", "pointer"),
    [
        ({"same_as": "specs.nothing"}, "不存在", "/a"),
        ({"same_as": "a"}, "成环", "/a"),
        ({"same_as": "specs.x", "scale": 2}, "要单独写", "/a"),
        ({"same_as": 3}, "应是定义里的路径", "/a"),
    ],
)
def test_what_cannot_be_resolved_is_refused(value, words, pointer):
    with raises_with(SameAsError, words) as info:
        resolve_same_as({"specs": {"x": 1}, "a": value})
    assert info.value.code == "E-DEF-SAME-AS" and info.value.pointer == pointer


# ---- the issue's acceptance: a child changes one place ----------------------------------------------


@pytest.fixture
def next_gen(tmp_path):
    """A UXR1104B child that changes the bandwidth, the top sample rate and the identity, each in
    one place only."""
    child = {
        "schema": "simubench.instrument/2",
        "id": "next-gen",
        "extends": "keysight-uxr1104b",
        "manufacturer": "MyLab",
        "model": "NG-RT160",
        "class": "oscilloscope",
        "specs": {"bandwidth_hz": 1.6e11, "sample_rate": 5.12e11},
    }
    (tmp_path / "next-gen.yaml").write_text(yaml.safe_dump(child, allow_unicode=True), "utf-8")
    bench = BenchDefinition.model_validate(
        {"schema": "simubench.bench/2", "name": "b", "instruments": {"s": {"model": "next-gen"}}}
    )
    session = open_bench(bench, definition_dirs=[tmp_path])

    def q(text: str) -> str:
        session.write("s", (text + "\n").encode())
        reply = session.read("s")
        return reply.decode().strip() if reply else ""

    yield q
    session.close()


def test_a_child_changes_the_bandwidth_the_sample_rate_and_the_identity_in_one_place(next_gen):
    assert next_gen("*IDN?").startswith("MyLab,NG-RT160,")
    assert float(next_gen(":ACQuire:BANDwidth?")) == 1.6e11
    next_gen(":ACQuire:BANDwidth 1.5e11")
    assert next_gen(":SYSTem:ERRor?").startswith("0")  # above the parent's 113 GHz: accepted
    next_gen(":ACQuire:SRATe MAX")
    assert float(next_gen(":ACQuire:SRATe?")) == 5.12e11
    next_gen(':SYSTem:PERSona "Other","M1"')
    assert next_gen("*IDN?").startswith("Other,M1,")
    next_gen(":SYSTem:PERSona DEF")
    assert next_gen("*IDN?").startswith("MyLab,NG-RT160,")  # DEF restores the child's identity


def test_the_built_in_uxr_models_resolve_to_their_data_sheet_values():
    lib = DefinitionLibrary([])
    for model, bw, rate in (("keysight-uxr1104b", 1.13e11, 2.56e11), ("keysight-uxr0334b", 3.3e10, 1.28e11)):
        d = lib.get(model)
        assert d.state["acquire.bandwidth"].max == bw and d.state["acquire.bandwidth"].default == bw
        assert d.state["acquire.srate"].max == rate
        adc = next(c for c in d.components if c.name == "adc")
        assert adc.params["max_rate"] == rate
