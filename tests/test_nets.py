"""The nets derivation (nets.py, issue 1381) and the channel expansion it sits beside.

nets.py is the one walk of the wires for both seams: the session builds the running bench's nets
from it and the validation groups the same ends; the lock test opens a bench (not unit tier), the
rest is pure logic. The overrides test pins the gap it closed: the validation judges a bench's
initial state against the spec the runtime enforces (channels.overrides applied).
"""

from __future__ import annotations

from pathlib import Path

from simubench.api import open_bench
from simubench.engine.nets import UnionFind, accessory_name, nets_of, union_wire
from simubench.engine.validate import validate_data
from simubench.library.loader import DefinitionLibrary
from simubench.models.bench import Wire

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "validation" / "benches" / "awg_rc_scope.bench.yaml"

LIB = DefinitionLibrary()


def _wire(accessories: list[dict] | None = None) -> Wire:
    return Wire.model_validate({"from": "a.OUT1", "to": "b.IN1", "accessories": accessories or []})


def test_accessory_name_is_the_runtime_name():
    assert accessory_name(1, 0, "cable") == "wire1.cable0"
    assert accessory_name(12, 3, "passive_probe") == "wire12.passive_probe3"


def test_chain_hops_join_end_to_terminal():
    uf = UnionFind()
    union_wire(uf, 0, _wire([{"type": "cable"}]))
    assert sorted(sorted(g) for g in uf.groups()) == [
        ["a.OUT1", "wire0.cable0.IN"],
        ["b.IN1", "wire0.cable0.OUT"],
    ]


def test_through_merges_only_the_named_types():
    uf = UnionFind()
    union_wire(uf, 0, _wire([{"type": "cable"}, {"type": "passive_probe"}]), through=frozenset({"cable"}))
    groups = sorted(sorted(g) for g in uf.groups())
    # the cable's two terminals are one node: both its sides, and the hop into the probe's IN,
    # land in that one group; the probe stays two nodes, its OUT paired with the far end
    assert ["a.OUT1", "wire0.cable0.IN", "wire0.cable0.OUT", "wire0.passive_probe1.IN"] in groups
    assert ["b.IN1", "wire0.passive_probe1.OUT"] in groups


def test_two_wires_on_one_port_are_one_net():
    uf = UnionFind()
    union_wire(uf, 0, Wire.model_validate({"from": "a.OUT1", "to": "d.RC"}))
    union_wire(uf, 1, Wire.model_validate({"from": "d.RC", "to": "b.IN1"}))
    (members,) = uf.groups()
    assert sorted(members) == ["a.OUT1", "b.IN1", "d.RC"]


def test_session_and_validation_share_one_derivation():
    """The lock (issue 1381): the running bench's nets are the same grouping the validation's
    derivation gives for the same wires, accessories named as the runtime names them."""
    s = open_bench(EXAMPLE)
    derived = [frozenset(members) for members in nets_of(s.bench)]
    assert {frozenset(n.members) for n in s.nets.values()} == set(derived)
    assert len(s.port_net) == sum(len(m) for m in derived)
    probe = "wire1.passive_probe0"
    assert probe in s.components
    assert s.port_net[f"{probe}.OUT"] == s.port_net["scope.CH1"]


def test_initial_state_judged_against_the_overridden_range():
    """channels.overrides was the runtime's alone; the validation now judges the initial state
    against the same patched spec (issue 1381)."""
    bench = {
        "schema": "simubench.bench/2",
        "name": "t",
        "instruments": {
            "psu": {"model": "keysight-e36312a", "state": {"ch1.voltage": 10, "ch2.voltage": 10}}
        },
        "wires": [],
    }
    _, report = validate_data(bench, LIB)
    assert [(d.code, d.pointer) for d in report.diagnostics] == [
        ("E-STATE-RANGE", "/instruments/psu/state/ch1.voltage")
    ]
    assert "6.18" in report.diagnostics[0].message  # channel 1's override, not the base 25.75
