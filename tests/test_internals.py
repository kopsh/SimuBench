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
    wires = [w.model_dump(by_alias=True) for w in view.wires]
    assert {"from": "ch2.front.out", "to": "ch2.probe.in"} in wires  # vertical -> probe -> acquisition
    assert {"from": "ch2.probe.out", "to": "acq.in2"} in wires
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
        {"instruments": {"rf": {"model": "sb-sg-1", "hidden": {"reference": {"ppm": 2.0}}}}}, "rf"
    )
    reference = next(c for c in view.components if c.name == "reference")
    offset = next(p for p in reference.params if p.name == "ppm")
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
        assert {"from": "ch1.probe.out", "to": "acq.in1"} in body["wires"]
        assert c.get(f"/api/sessions/{sid}/instruments/nope/internals").status_code == 404


# ---- what a component uses, beside what feeds it (#729) --------------------------------------------


def _refs(view) -> set[tuple[str, str, str]]:
    return {(r.from_, r.to, r.param) for r in view.refs}


def test_a_scope_s_acquisition_is_fed_by_its_time_base_trigger_and_adc_and_feeds_the_readers():
    """The graph's dependencies, from the components' own ``refs``: the used component feeds the one that
    uses it, so the acquisition has its stages before it and the measurements and the transfer after it."""
    view = internals({"instruments": {"scope": {"model": "keysight-dsox2024a"}}}, "scope")
    assert _refs(view) == {
        ("timebase", "acq", "horizontal"),
        ("trigger", "acq", "trigger"),
        ("adc", "acq", "adc"),
        ("acq", "meas", "record"),
        ("acq", "xfer", "record"),
    }
    # Together with the wires, the converter, the time base, the measurements and the transfer each
    # have a place in the flow: only the command and status side (system, registers) stands alone.
    comps = {c.name for c in view.components if c.channel is None}
    connected = {r.from_ for r in view.refs} | {r.to for r in view.refs}
    connected |= {w.from_.rsplit(".", 1)[0] for w in view.wires} | {
        w.to.rsplit(".", 1)[0] for w in view.wires
    }
    assert comps - connected == {"ter", "aer", "oper", "sys"}


@pytest.mark.parametrize("def_id", LIBRARY.ids())
def test_every_ref_the_view_shows_is_between_two_components_of_the_instrument(def_id):
    view = internals({"instruments": {"x": {"model": def_id}}}, "x")
    names = {c.name for c in view.components}
    for r in view.refs:
        assert r.from_ in names and r.to in names, r
        assert r.from_ != r.to, r
    assert len(view.refs) == len({(r.from_, r.to, r.param) for r in view.refs})  # each once


@pytest.mark.parametrize("def_id", LIBRARY.ids())
def test_every_ref_set_on_a_component_of_the_instrument_is_in_the_view(def_id):
    """Completeness (#740): each ``refs`` parameter that is set and names a component of this instrument
    shows up as ``(used, user, param)``, whatever the model, so a missed kind of dependency is caught."""
    session = open_bench(
        BenchDefinition.model_validate({"name": "t", "instruments": {"x": {"model": def_id}}})
    )
    try:
        inst = session.instrument("x")
        view = SessionManager.internals(ServedSession("t", session, None), "x")
        expected = set()
        for name, comp in inst.components.items():
            for key in comp.refs:
                target = str(comp.param(key) or "")
                if not target:
                    continue
                used = comp.peer(target)
                if used is None:
                    continue
                used_name = next((n for n, c in inst.components.items() if c is used), None)
                if used_name is not None:
                    expected.add((used_name, name, key))
    finally:
        session.close()
    assert expected <= _refs(view), expected - _refs(view)


def test_refs_are_not_wires():
    """A dependency is not a signal wire: the wires of the view are the definition's wiring, as before."""
    view = internals({"instruments": {"scope": {"model": "keysight-dsox2024a"}}}, "scope")
    wires = {(w.from_.rsplit(".", 1)[0], w.to.rsplit(".", 1)[0]) for w in view.wires}
    assert ("adc", "acq") not in wires and ("acq", "meas") not in wires


# ---- commands and readings per component (#739) ----------------------------------------------------


@pytest.mark.parametrize("def_id", LIBRARY.ids())
def test_every_command_is_found_from_the_component_it_operates(def_id):
    """Every command that runs a component's operation, or writes a setting a component's
    parameter follows, is listed on that component — whatever the binding form (``state:``,
    ``op:``, a parameter's ``state``, ``keys``, ``writes``) — with the channel filled in and the
    setting it writes as the component resolves it; every reading's path leads from the component
    to the provider over the view's own links."""
    from simubench.server.internals_links import command_settings, concrete, norm

    d = LIBRARY.get(def_id)
    view = internals({"instruments": {"x": {"model": def_id}}}, "x")
    by_norm: dict[str, list] = {}
    for c in view.components:
        by_norm.setdefault(norm(c.name), []).append(c)
    for cmd in d.commands:
        if cmd.op:
            comp_name, _, op = cmd.op.rpartition(".")
            owners = by_norm.get(norm(comp_name))
            assert owners, f"{cmd.header}: op {cmd.op} names no component"
            for c in owners:
                assert any(x.header == concrete(cmd.header, c.channel) and x.op == op for x in c.commands)
        for path in command_settings(cmd):
            followers = [c for c in view.components if norm(path) in {norm(s) for s in c.settings}]
            for c in followers:
                hit = [x for x in c.commands if x.header == concrete(cmd.header, c.channel)]
                assert hit, (cmd.header, c.name)
                assert all(x.setting in c.settings or x.op for x in hit), (cmd.header, c.name)
    names = {c.name for c in view.components}
    links = {(w.from_.rpartition(".")[0], w.to.rpartition(".")[0]) for w in view.wires}
    links |= {(r.from_, r.to) for r in view.refs}
    selecting = {s.component for s in view.selects}
    for c in view.components:
        for r in c.readings:
            assert r.provider in names, r  # a real component, not 'ch.probe'
            assert r.path[0] == c.name and r.path[-1] == r.provider, r
            for a, b in zip(r.path, r.path[1:], strict=False):
                assert (a, b) in links or b in selecting, (c.name, r)


def test_the_scope_adc_lists_the_waveform_commands_it_changes():
    """The ADC's bit depth reaches the waveform codes and the measurements through the acquisition;
    the acquisition's own queries (sample rate, status) are on the way, not results."""
    view = internals({"instruments": {"scope": {"model": "siglent-sds2104xplus"}}}, "scope")
    adc = next(c for c in view.components if c.type == "scope_adc")
    assert adc.settings == ["acquire.resolution"]
    assert [(c.header, c.setting) for c in adc.commands] == [(":ACQuire:RESolution", "acquire.resolution")]
    heads = {r.header: r for r in adc.readings}
    assert {":WAVeform:DATA", ":WAVeform:PREamble", ":MEASure:SIMPle:VALue"} <= set(heads)
    data = heads[":WAVeform:DATA"]
    assert (data.provider, data.via, data.path, data.terminal) == ("xfer", "ref", ["adc", "acq", "xfer"], True)
    assert {r.header for r in adc.readings if r.terminal} == {
        ":WAVeform:DATA",
        ":WAVeform:PREamble",
        ":MEASure:SIMPle:VALue",
    }
    assert not heads[":ACQuire:SRATe"].terminal and heads[":ACQuire:SRATe"].provider == "acq"


def test_a_channel_component_names_its_own_channel():
    """A channel's vertical lists ``:CHANnel2:SCALe`` writing ``ch2.scale``, and the probe after it by
    its own name; its waveform reaches the transfer by the source setting."""
    view = internals({"instruments": {"scope": {"model": "siglent-sds2104xplus"}}}, "scope")
    comps = {c.name: c for c in view.components}
    front = comps["ch2.front"]
    assert (":CHANnel2:SCALe", "ch2.scale") in {(c.header, c.setting) for c in front.commands}
    assert all("<ch>" not in c.header for c in front.commands)
    assert ":CHANnel2:PROBe" in {r.header for r in front.readings if r.provider == "ch2.probe"}
    data = next(r for r in front.readings if r.header == ":WAVeform:DATA")
    assert data.via == "select" and data.path[-1] == "xfer"


def test_a_source_setting_selects_the_channel_without_a_ref():
    """``waveform_transfer`` / ``scope_measurements`` / the trigger pick their channel with a ``source`` setting:
    the view names them and the setting, and the setting is among their drivers."""
    view = internals({"instruments": {"scope": {"model": "siglent-sds2104xplus"}}}, "scope")
    comps = {c.name: c for c in view.components}
    assert {(s.component, s.setting) for s in view.selects} == {
        ("xfer", "waveform.source"),
        ("meas", "measure.source"),
        ("trigger", "trigger.source"),  # the trigger picks its channel the same way
    }
    assert ":MEASure:SIMPle:VALue" in {r.header for r in comps["ch2.front"].readings}
    assert ":WAVeform:SOURce" in {c.header for c in comps["xfer"].commands}
    own = next(r for r in comps["xfer"].readings if r.header == ":WAVeform:DATA")
    assert (own.via, own.path) == ("self", ["xfer"])


def test_a_command_only_component_lists_the_commands_that_operate_it():
    """A system block has no parameters bound to settings: its commands are its reason to exist."""
    view = internals({"instruments": {"x": {"model": "keysight-34465a"}}}, "x")
    ops = [c for c in view.components if not c.settings and c.commands]
    assert ops, "some component is driven by operations only"
    assert all(cmd.op and cmd.setting is None for c in ops for cmd in c.commands)
