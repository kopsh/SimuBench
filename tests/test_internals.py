"""Read-only view of an instrument's internal components (issue 20).

The view must match the definition file: every component (once, or once per channel), the internal
wiring, the panel ports; parameters as the model defines them, bindings expanded to the setting.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from simubench.api import open_bench
from simubench.library.loader import DefinitionLibrary
from simubench.models.bench import BenchDefinition
from simubench.server.app import create_app
from simubench.server.manager import ServedSession, SessionManager

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = DefinitionLibrary()


def internals(bench: dict, name: str):
    session = open_bench(BenchDefinition.model_validate({"name": "t", **bench}))
    try:
        return SessionManager.internals(ServedSession("t", session, None), name)
    finally:
        session.close()


@pytest.mark.parametrize("def_id", LIBRARY.ids())
def test_every_definition_matches_the_view(def_id):
    d = LIBRARY.get(def_id)
    view = internals({"instruments": {"x": {"model": def_id}}}, "x")
    n = view.channels
    expected = [c.name for c in d.components] + [
        f"ch{k}.{c.name}" for k in range(1, n + 1) for c in d.channel_components
    ]
    assert [c.name for c in view.components] == expected
    types = {c.name: c.type for c in view.components}
    assert all(types[f"ch{k}.{c.name}"] == c.type for k in range(1, n + 1) for c in d.channel_components)
    wiring = len(d.wiring.channel) * n + sum(
        n if "{n}" in a or "{n}" in b else 1 for a, b in d.wiring.instrument
    )
    assert len(view.wires) == wiring
    ports = {p.name: p.target for p in view.ports}
    assert len(ports) == len(d.ports.channel) * n + len(d.ports.instrument)
    # Every wire and port names a component port the view lists.
    listed = {f"{c.name}.{p.name}" for c in view.components for p in c.ports}
    assert {w.from_ for w in view.wires} | {w.to for w in view.wires} | set(ports.values()) <= listed


def test_scope_chain_and_bindings():
    view = internals(
        {"instruments": {"scope": {"model": "sb-dso-4", "state": {"ch2.coupling": "AC"}}}}, "scope"
    )
    comps = {c.name: c for c in view.components}
    assert comps["acq"].channel is None and comps["ch2.front"].channel == 2
    assert {"from": "ch2.inp.out", "to": "ch2.front.in"} in [w.model_dump(by_alias=True) for w in view.wires]
    assert {"from": "ch2.front.out", "to": "acq.in2"} in [w.model_dump(by_alias=True) for w in view.wires]
    assert ("CH2", "ch2.inp.pin") in [(p.name, p.target) for p in view.ports]
    coupling = next(p for p in comps["ch2.front"].params if p.name == "coupling")
    assert (coupling.binding, coupling.value) == ("ch2.coupling", "AC")
    other = next(p for p in comps["ch1.front"].params if p.name == "coupling")
    assert (other.binding, other.value) == ("ch1.coupling", "DC")
    bandwidth = next(p for p in comps["ch1.front"].params if p.name == "bandwidth")
    assert bandwidth.binding is None and bandwidth.value == 200e6


def test_per_unit_overrides_are_not_part_of_the_model():
    """The bench's per-unit deviation (offset_ppm) belongs to the unit, not the model (issue 34)."""
    view = internals(
        {"instruments": {"rf": {"model": "sb-sg-1", "hidden": {"core": {"offset_ppm": 2.0}}}}}, "rf"
    )
    core = next(c for c in view.components if c.name == "core")
    offset = next(p for p in core.params if p.name == "offset_ppm")
    assert offset.value == 0.0 and offset.unit == "ppm"


def test_single_channel_instrument():
    view = internals({"instruments": {"dmm": {"model": "sb-dmm-1"}}}, "dmm")
    assert view.definition_id == "sb-dmm-1"
    assert view.components and all(c.doc for c in view.components)


def test_http_endpoint():
    manager = SessionManager(ROOT / "validation", scpi_base_port=None)
    app = create_app(manager, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        sid = c.post("/api/sessions", json={"bench_path": "benches/awg_rc_scope.bench.yaml"}).json()["id"]
        r = c.get(f"/api/sessions/{sid}/instruments/scope/internals")
        assert r.status_code == 200
        body = r.json()
        assert {"from": "ch1.front.out", "to": "acq.in1"} in body["wires"]
        assert c.get(f"/api/sessions/{sid}/instruments/nope/internals").status_code == 404
