"""The instrument seam (#1380): the InstrumentHost protocol the instrument layer needs of the
session, named at Instrument construction (D62's roll-call, at the instrument's side), and the
façade's one-name rule — the collaborators (ops, errs, common) and the registers' state are
private, the façade attributes (esr, pending, push_error, …) are the only public route, and
nothing outside engine/ reaches into the privates — a private name, a retired double on a hub
chain, or any ``owner._…`` from a library component (the scan below is the guard, as
test_component_privacy guards the component seam's side)."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from typing_extensions import get_protocol_members

from helpers import FakeHost, seam_instrument
from simubench.engine.instrument import InstrumentHost, _host_gaps
from simubench.engine.session import Session
from simubench.models.bench import BenchDefinition
from simubench.models.definition import InstrumentDefinition

pytestmark = pytest.mark.unit

DEFINITION = InstrumentDefinition.model_validate(
    {
        "schema": "simubench.instrument/2",
        "id": "host-demo",
        "manufacturer": "ACME",
        "model": "HOS",
        "class": "multimeter",
        "state": {"out.level": {"type": "float", "unit": "V", "min": 0.0, "max": 10.0, "default": 1.0}},
        "commands": [{"header": ":LEVel", "state": "out.level"}],
    }
)

SRC = Path(__file__).resolve().parent.parent / "src" / "simubench"
ENGINE = SRC / "engine"

#: The collaborators the façade keeps private; a member of this set reached from outside engine/
#: is a poke at the instrument's internals that must go through the façade instead (#1380).
PRIVATE = ("_status", "_ops", "_errs", "_common")
#: The names an instrument is reached through; ``.ops``/``.errs``/``.common`` on one of them is a
#: poke at a retired double (the façade has push_error, pending, sample_status, …).
HUBS = ("inst", "instrument", "owner", "instruments")
#: A library component holds the instrument as ``owner``: the roll-call's spirit covers it too, so
#: any ``owner._…`` outside engine/ is a poke at an internals data member (the façade has
#: has_output, take_output, …).
OWNER = "owner"


def _chain_names(node: ast.expr) -> set[str]:
    """The attribute and name pieces of a receiver chain, e.g. ``self.inst.ops`` -> self, inst."""
    names: set[str] = set()
    while isinstance(node, ast.Attribute | ast.Subscript):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        names.add(node.id)
    return names


def accesses_in(nodes: list[ast.AST]) -> list[tuple[int, str]]:
    found = []
    for node in nodes:
        if not isinstance(node, ast.Attribute):
            continue
        hub = _chain_names(node.value)
        retired = node.attr in ("ops", "errs", "common") and hub & set(HUBS)
        owner_private = node.attr.startswith("_") and OWNER in hub
        if node.attr in PRIVATE or retired or owner_private:
            found.append((node.lineno, f"….{node.attr}"))
    return sorted(found)


#: Every non-engine module's attribute nodes, walked once at collection (D49: the scan must not
#: cost the call phase anything as the package grows).
TREES = [
    (
        p.relative_to(SRC).as_posix(),
        [n for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))) if isinstance(n, ast.Attribute)],
    )
    for p in sorted(SRC.rglob("*.py"))
    if ENGINE not in p.parents and p.name != "_version.py"
]
OFFENDERS = [f"{name}:{n}: {access}" for name, nodes in TREES for n, access in accesses_in(nodes)]

#: The guard's specimen: what it must see, and what it must leave alone (a queue's ``ops`` that is
#: no instrument's, an attribute named ``inst`` that carries neither).
SPECIMEN = """
def probe(inst, owner, queue, session):
    inst.ops.pending.clear()
    inst._errs._push(1)
    owner.status.esr = 0
    owner._output.clear()
    session.instruments["x"].common.stb()
    return queue.ops, inst.common.sense_of_humour
"""


def test_the_roll_call_names_every_missing_member():
    host = SimpleNamespace(**vars(FakeHost()))  # the instance attrs only: the methods are gone
    host.port_ref = lambda comp, port: None
    host.sync = lambda: None
    host.state_changed = lambda *a, **k: None
    with pytest.raises(ValueError) as caught:
        seam_instrument(DEFINITION, host=host)
    message = str(caught.value)
    for member in ("cached_frames", "net_feed", "port_signal", "solve"):
        assert member in message  # the roll-call names what is missing, sorted
    for provided in ("now_ns", "port_net", "seed", "clock", "events"):
        assert provided not in message  # and only what is missing


def test_the_seam_names_no_medium():
    """A new one-way medium needs no new host member (#1531): the seam reaches a bench-wired
    input of any medium through the one ``net_feed(comp, port, medium)`` entry."""
    members = get_protocol_members(InstrumentHost)
    assert "net_feed" in members
    assert [m for m in members if "optical" in m] == []


def test_the_fake_host_satisfies_the_seam():
    assert isinstance(FakeHost(), InstrumentHost)


def test_the_real_session_satisfies_the_seam():
    bench = BenchDefinition.model_validate(
        {"schema": "simubench.bench/1", "name": "d", "instruments": {"x": {"model": DEFINITION.id}}}
    )
    session = Session(bench, lambda _id: DEFINITION, clock="stepped", seed=1)
    assert isinstance(session, InstrumentHost)
    assert _host_gaps(session) == []


def test_the_registers_answer_to_the_facade_s_names_only():
    inst = seam_instrument(DEFINITION)
    assert not hasattr(inst.status, "esr") and not hasattr(inst.status, "sre")
    inst.esr = 0x20
    assert inst.esr == 0x20
    inst.esr |= 1
    assert inst.esr == 0x21
    inst.ese, inst.sre = 2, 4
    assert (inst.ese, inst.sre) == (2, 4)


def test_the_collaborators_answer_to_the_facade_only():
    inst = seam_instrument(DEFINITION)
    for retired in ("ops", "errs", "common"):
        assert not hasattr(inst, retired), retired
    assert inst.pending == {}  # the façade route (inst.ops.pending is retired)
    inst.push_error(-241, "no such option")  # the façade route (inst.errs.push_error is retired)
    assert [e.code for e in inst.errors.peek_all()] == [-241]
    assert inst.esr == 1 << 4  # latched through the reporter, onto the façade's registers


def test_nothing_outside_engine_pokes_the_collaborators():
    assert OFFENDERS == []


def test_the_guard_sees_a_poke_and_leaves_the_rest():
    found = accesses_in([n for n in ast.walk(ast.parse(SPECIMEN)) if isinstance(n, ast.Attribute)])
    hits = [access for _line, access in found]
    assert hits.count("….ops") == 1  # inst.ops; not queue.ops (no hub in its chain)
    assert hits.count("…._errs") == 1
    assert hits.count("…._output") == 1  # owner._output: a private reached through owner
    assert hits.count("….common") == 2  # inst.common and session.instruments["x"].common
    assert not any("_status" in h for h in hits)  # owner.status.esr: status is the façade's to keep


def test_the_guard_walks_subscripted_hubs_too():

    tree = ast.parse(SPECIMEN)
    [subscripted] = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and n.attr == "common" and isinstance(n.value, ast.Subscript)
    ]
    assert _chain_names(subscripted.value) == {"session", "instruments"}
