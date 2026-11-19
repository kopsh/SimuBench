"""Settings an instrument does not allow together (`constraints`, #1013, D69): refused in either
order or coercing the other setting, judged on the state a whole command leaves, the same for the
panel (configure) as for SCPI, not for the instrument's own writes; and the static checks of the
declaration."""

from __future__ import annotations

import copy
from typing import Any, ClassVar

from pydantic import ValidationError

from helpers import raises_with
from simubench.engine.component import Component, OpResult, component, op
from simubench.engine.session import Session
from simubench.engine.validate_definition import validate_definition_data
from simubench.models.bench import BenchDefinition
from simubench.models.definition import InstrumentDefinition
from simubench.scpi.errors import ScpiError

#: A digitizer whose segmented mode cannot average, like the DSO-X 2000 (:ACQuire:TYPE, p. 215).
DEF = {
    "schema": "simubench.instrument/2",
    "id": "constrained",
    "manufacturer": "ACME",
    "model": "C-1",
    "class": "oscilloscope",
    "state": {
        "acquire.mode": {"type": "enum", "options": ["RTIMe", "SEGMented"], "default": "RTIMe"},
        "acquire.type": {"type": "enum", "options": ["NORMal", "AVERage", "PEAK"], "default": "NORMal"},
    },
    "commands": [
        {"header": ":ACQuire:MODE", "state": "acquire.mode"},
        {"header": ":ACQuire:TYPE", "state": "acquire.type"},
        {
            "header": ":ACQuire:BOTH",
            "params": [
                {"name": "mode", "type": "enum", "state": "acquire.mode"},
                {"name": "type", "type": "enum", "state": "acquire.type"},
            ],
        },
    ],
    "constraints": [
        {"when": {"acquire.mode": "SEGMented"}, "forbid": {"acquire.type": ["AVERage"]}, "error": -221},
    ],
}


def open_unit(data: dict = DEF):
    definition = InstrumentDefinition.model_validate(data)
    bench = BenchDefinition.model_validate(
        {"schema": "simubench.bench/1", "name": "c", "instruments": {"x": {"model": "constrained"}}}
    )
    session = Session(bench, lambda _id: definition, clock="stepped", seed=1)

    def query(cmd: str) -> str:
        session.write("x", cmd.encode() + b"\n")
        out = session.read("x")
        return out.decode().strip() if out is not None else ""

    return session, session.instruments["x"], query


def test_a_refusing_constraint_holds_in_either_order_and_changes_nothing():
    _, inst, query = open_unit()
    query(":ACQ:MODE SEGM")
    query(":ACQ:TYPE AVER")
    assert query(":SYST:ERR?").startswith("-221")
    assert (inst.state["acquire.mode"], inst.state["acquire.type"]) == ("SEGMented", "NORMal")
    query(":ACQ:MODE RTIM;:ACQ:TYPE AVER")
    query(":ACQ:MODE SEGM")
    assert query(":SYST:ERR?").startswith("-221")
    assert (inst.state["acquire.mode"], inst.state["acquire.type"]) == ("RTIMe", "AVERage")
    query(":ACQ:TYPE PEAK")  # a value the constraint does not name is free
    query(":ACQ:MODE SEGM")
    assert query(":SYST:ERR?") == '0,"No error"'
    assert inst.state["acquire.mode"] == "SEGMented"


def test_a_command_is_judged_on_the_state_it_leaves():
    """One command writing both settings: legal at its end, so not refused for the state between
    them; one that ends in the conflict is refused whole."""
    _, inst, query = open_unit()
    query(":ACQ:TYPE AVER")
    query(":ACQ:BOTH SEGM,NORM")  # mode first would conflict with AVERage; the end state is legal
    assert (inst.state["acquire.mode"], inst.state["acquire.type"]) == ("SEGMented", "NORMal")
    query(":ACQ:BOTH RTIM,AVER")
    query(":ACQ:BOTH SEGM,AVER")
    assert query(":SYST:ERR?").startswith("-221")
    assert (inst.state["acquire.mode"], inst.state["acquire.type"]) == ("RTIMe", "AVERage")


def test_a_coercing_constraint_changes_the_other_setting():
    data = copy.deepcopy(DEF)
    data["constraints"][0] |= {"action": "coerce", "coerce": {"acquire.type": "NORMal"}}
    _, inst, query = open_unit(data)
    query(":ACQ:TYPE AVER")
    query(":ACQ:MODE SEGM")  # the instrument's coupling: averaging gives way to segmented
    assert (inst.state["acquire.mode"], inst.state["acquire.type"]) == ("SEGMented", "NORMal")
    assert query(":SYST:ERR?") == '0,"No error"'


def test_the_panel_is_held_to_the_same_constraints():
    session, inst, _ = open_unit()
    session.configure("x", "acquire.mode", "SEGMented")
    with raises_with(ScpiError, "not available"):
        session.configure("x", "acquire.type", "AVERage")
    assert inst.state["acquire.type"] == "NORMal"


@component("t_narrower")
class Narrower(Component):
    """A component that narrows a numeric setting's bounds now (#437): a floor of 2 on ``count``."""

    defaults: ClassVar[dict[str, Any]] = {"count": None}  # the setting it narrows (a state path)

    def state_limits(self, path: str) -> tuple[float | None, float | None] | None:
        if self.bound_path("count") == path:
            return (2.0, None)
        return None


def _with_count(data: dict) -> dict:
    data = copy.deepcopy(data)
    data["state"]["acquire.count"] = {"type": "float", "min": 0.0, "max": 100.0, "default": 5.0}
    data["commands"] += [{"header": ":ACQuire:COUNt", "state": "acquire.count"}]
    data["components"] = [{"name": "n", "type": "t_narrower", "params": {"count": "$acquire.count"}}]
    return data


def test_the_panel_is_held_to_the_narrowed_limits():
    """A write that bypasses the SCPI parser (the panel, MCP) is held to the dynamic limits too
    (#437), and a plain SCPI number with them at the batch's end, not only MIN/MAX (#1379); the
    instrument's own write is not judged."""
    session, inst, query = open_unit(_with_count(DEF))
    query(":ACQ:COUN 1")  # below the narrowed floor: refused like any out-of-range value
    assert query(":SYST:ERR?").startswith("-222")
    assert inst.state["acquire.count"] == 5.0
    query(":ACQ:COUN 5")
    with raises_with(ScpiError, "Data out of range"):
        session.configure("x", "acquire.count", 1.0)
    assert inst.state["acquire.count"] == 5.0
    inst.set_state("acquire.count", 1.0, source="probe")  # the instrument's own write: not judged
    assert inst.state["acquire.count"] == 1.0


@component("t_combination_guard")
class Guard(Component):
    """Refuses any batch that leaves the segmented mode on (a component's combination check, #580)."""

    defaults: ClassVar[dict[str, Any]] = {"mode": None}  # the setting it watches (a state path)

    def check_settings(self) -> None:
        if str(self.param("mode")) == "SEGMented":
            raise ScpiError(-221, "the combination is not allowed")


def test_a_refused_batch_sends_no_events_for_the_way_back():
    """A write a component refuses (#580) takes the whole batch back (#550), and the rollback is
    not visible (#1379): no state event for what was applied before the refusal, none for the way
    back -- the refused command leaves the run record as if it had not been sent."""
    data = copy.deepcopy(DEF)
    data["components"] = [{"name": "c", "type": "t_combination_guard", "params": {"mode": "$acquire.mode"}}]
    session, inst, query = open_unit(data)
    before = [e for e in session.events.history if e["kind"] == "state"]
    query(":ACQ:BOTH SEGM,NORM")  # both settings applied, then the combination refused
    assert query(":SYST:ERR?").startswith("-221")
    assert (inst.state["acquire.mode"], inst.state["acquire.type"]) == ("RTIMe", "NORMal")
    assert [e for e in session.events.history if e["kind"] == "state"] == before  # nothing became visible
    query(":ACQ:MODE SEGM;:ACQ:TYPE NORM")  # a single-write command is judged as its own batch too
    assert query(":SYST:ERR?").startswith("-221")
    assert inst.state["acquire.mode"] == "RTIMe"


@component("t_acquire_coupling")
class AcquireCoupling(Component):
    """A component that writes the acquisition type itself: on a command (``average``) and as the
    coupling of another setting (a preset that averages)."""

    defaults: ClassVar[dict[str, Any]] = {
        "type": None,  # the acquisition type it writes (a setting, e.g. $acquire.type)
        "preset": None,  # the preset whose SLOW value makes it average (a setting)
    }

    @op("average")
    def op_average(self, **_: Any) -> OpResult:
        assert self.owner is not None
        self.owner.set_state(str(self.bound_path("type")), "AVERage")
        return OpResult()

    def on_state(self, key: str | None, path: str, old: Any, new: Any) -> None:
        if key == "preset" and new == "SLOW":
            assert self.owner is not None
            self.owner.set_state(str(self.bound_path("type")), "AVERage")


def test_the_instruments_own_writes_are_not_judged():
    """A component's own write (an operation's, or the coupling of another setting) is the
    instrument's doing, not a change from outside: the constraints do not refuse it (#1030)."""
    data = copy.deepcopy(DEF)
    data["state"]["acquire.preset"] = {"type": "enum", "options": ["NONE", "SLOW"], "default": "NONE"}
    data["components"] = [
        {"name": "sys", "type": "system"},
        {
            "name": "coupling",
            "type": "t_acquire_coupling",
            "params": {"type": "$acquire.type", "preset": "$acquire.preset"},
        },
    ]
    data["commands"] += [
        {"header": ":ACQuire:AVERage", "op": "coupling.average", "query": False},
        {"header": ":ACQuire:PRESet", "state": "acquire.preset"},
    ]
    _, inst, query = open_unit(data)
    query(":ACQ:MODE SEGM")
    query(":ACQ:AVER")  # the operation's own write
    assert query(":SYST:ERR?") == '0,"No error"'
    assert (inst.state["acquire.mode"], inst.state["acquire.type"]) == ("SEGMented", "AVERage")
    query(":ACQ:TYPE NORM")
    query(":ACQ:PRES SLOW")  # the command's setting is free; the coupling it starts is not judged
    assert query(":SYST:ERR?") == '0,"No error"'
    assert (inst.state["acquire.preset"], inst.state["acquire.type"]) == ("SLOW", "AVERage")
    query(":ACQ:TYPE NORM;:ACQ:TYPE AVER")  # the same value from outside is still refused
    assert query(":SYST:ERR?").startswith("-221")
    assert inst.state["acquire.type"] == "NORMal"


def _codes(data: dict) -> set[tuple[str, str]]:
    report = validate_definition_data(data)[1]
    return {(d.code, d.pointer) for d in report.diagnostics if d.severity == "error"}


def test_the_declaration_is_checked():
    assert not _codes(DEF)
    data = copy.deepcopy(DEF)
    data["constraints"] = [
        {"when": {"acquire.modes": "SEGMented"}, "forbid": {"acquire.type": ["AVERage"]}},
        {"when": {"acquire.mode": "BURST"}, "forbid": {"acquire.type": ["AVERage"]}},
        {
            "when": {"acquire.mode": "SEGMented"},
            "forbid": {"acquire.type": ["AVERage"]},
            "action": "coerce",
            "coerce": {"acquire.type": "AVERage"},
        },
    ]
    assert _codes(data) == {
        ("E-DEF-CONSTRAINT", "/constraints/0/when/acquire.modes"),
        ("E-DEF-CONSTRAINT", "/constraints/1/when/acquire.mode"),
        ("E-DEF-CONSTRAINT", "/constraints/2/coerce/acquire.type"),
    }
    # the power-on state may not break a constraint
    data = copy.deepcopy(DEF)
    data["state"]["acquire.type"]["default"] = "AVERage"
    data["state"]["acquire.mode"]["default"] = "SEGMented"
    assert ("E-DEF-CONSTRAINT", "/constraints/0") in _codes(data)
    # coerce values go with the coerce action, one for each forbidden setting
    data = copy.deepcopy(DEF)
    data["constraints"][0]["coerce"] = {"acquire.type": "NORMal"}
    with raises_with(ValidationError, "coerce"):
        InstrumentDefinition.model_validate(data)
