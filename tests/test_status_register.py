"""The generic status register block (#248, #273): pushed events and condition edges latch, reads
clear (the whole register or one bit), the enable mask takes the SRS bit form, and the enabled
events summarize into *STB?."""

from __future__ import annotations

from simubench.engine.session import Session
from simubench.models.bench import BenchDefinition
from simubench.models.definition import InstrumentDefinition

DEF = {
    "schema": "simubench.instrument/1",
    "id": "status-demo",
    "manufacturer": "ACME",
    "model": "STAT",
    "class": "oscilloscope",
    "state": {"status.enable": {"type": "int", "min": 0, "max": 255, "default": 0}},
    "components": [
        {"name": "sys", "type": "system"},
        {
            "name": "lia",
            "type": "status_register",
            "params": {
                "bits": {"TRIG": 0, "RANGE": 3},
                "enable": "$status.enable",
                "summary_bit": 3,
                "width": 8,
            },
        },
    ],
    "commands": [
        {
            "header": "LIAS",
            "op": "lia.event",
            "set": False,
            "params": [{"name": "bit", "type": "int", "optional": True}],
        },
        {
            "header": "LIAE",
            "op": "lia.enable",
            "params": [
                {"name": "bit", "type": "int", "optional": True},
                {"name": "value", "type": "int", "optional": True},
            ],
        },
    ],
}


def open_demo():
    definition = InstrumentDefinition.model_validate(DEF)
    bench = BenchDefinition.model_validate(
        {"schema": "simubench.bench/1", "name": "d", "instruments": {"x": {"model": definition.id}}}
    )
    session = Session(bench, lambda _id: definition, clock="stepped", seed=1)

    def q(cmd: str) -> str:
        session.write("x", cmd.encode() + b"\n")
        out = session.read("x")
        return out.decode().strip() if out is not None else ""

    return session.instruments["x"], q


def test_an_event_latches_and_a_read_clears_it():
    inst, q = open_demo()
    assert q("LIAS?") == "0"
    inst.status_event("TRIG")
    inst.status_event("unknown")  # a register without that bit ignores it
    assert q("LIAS?") == "1"
    assert q("LIAS?") == "0"


def test_a_bit_read_clears_only_that_bit():
    inst, q = open_demo()
    inst.status_event("TRIG")
    inst.status_event("RANGE")
    assert q("LIAS? 3") == "1"
    assert q("LIAS? 3") == "0"
    assert q("LIAS?") == "1"  # bit 0 is still there


def test_the_enable_mask_takes_the_bit_form_and_summarizes_into_the_status_byte():
    inst, q = open_demo()
    q("LIAE 3,1")
    assert q("LIAE?") == "8"
    assert q("LIAE? 3") == "1"
    inst.status_event("TRIG")
    assert int(q("*STB?")) & 8 == 0  # bit 0 is not enabled
    inst.status_event("RANGE")
    assert int(q("*STB?")) & 8 == 8
    q("*CLS")
    assert int(q("*STB?")) & 8 == 0
    q("LIAE 0")
    assert q("LIAE?") == "0"


def test_one_look_asks_each_component_for_its_flags_once():
    """Registers read one another's summaries; within one look the other components' flags are
    computed once (#602: before, n registers asked a scope's trigger n! times per command)."""
    data = dict(DEF)
    data["components"] = [{"name": "sys", "type": "system"}] + [
        {"name": f"r{i}", "type": "status_register", "params": {"bits": {"BUSY": 0}, "enable": 1, "width": 1}}
        for i in range(5)
    ]
    data["commands"] = [{"header": "REGister", "op": "r0.event", "set": False}]
    definition = InstrumentDefinition.model_validate(data)
    bench = BenchDefinition.model_validate(
        {"schema": "simubench.bench/1", "name": "d", "instruments": {"x": {"model": definition.id}}}
    )
    session = Session(bench, lambda _id: definition, clock="stepped", seed=1)
    inst = session.instruments["x"]
    calls = []
    sys = inst.components["sys"]
    sys.status_flags = lambda: calls.append(1) or {"BUSY": True}  # type: ignore[method-assign]
    calls.clear()
    session.write("x", b"REG?\n")
    assert session.read("x") is not None
    # five registers latch at the dispatch and the read looks once more: one ask per outermost look
    assert len(calls) <= 6


def test_one_look_computes_each_registers_condition_a_small_multiple_of_once(monkeypatch):
    """Six registers that read one another's summaries (r0 reads r1..r5, r1 reads r2..r5, ...):
    one sample and one status byte call ``condition`` at most twice per register (#1025: before,
    every register asked every other one, which asked the rest -- 3260 calls per command)."""
    from simubench.library.components.status import StatusRegister

    n = 6
    data = dict(DEF)
    data["components"] = [{"name": "sys", "type": "system"}] + [
        {
            "name": f"r{i}",
            "type": "status_register",
            "params": {
                "bits": {"BUSY": 0} | {f"R{j}": j for j in range(i + 1, n)},
                "enable": 255,
                "summary_bit": i,
                "width": 8,
            },
        }
        for i in range(n)
    ]
    data["commands"] = []
    definition = InstrumentDefinition.model_validate(data)
    bench = BenchDefinition.model_validate(
        {"schema": "simubench.bench/1", "name": "d", "instruments": {"x": {"model": definition.id}}}
    )
    session = Session(bench, lambda _id: definition, clock="stepped", seed=1)
    inst = session.instruments["x"]
    inst.status.sample()  # the baseline: BUSY is not a transition yet
    inst.components["sys"].status_flags = lambda: {"BUSY": True}  # type: ignore[method-assign]
    calls = {"sample": 0, "stb": 0}
    phase = "sample"
    condition = StatusRegister.condition

    def counted(self):
        calls[phase] += 1
        return condition(self)

    monkeypatch.setattr(StatusRegister, "condition", counted)
    inst.status.sample()
    phase = "stb"
    stb = inst.stb()
    # BUSY rose in every register, and every summary rose in the registers that read it
    assert (stb & 0x3F, calls["sample"] <= 2 * n, calls["stb"] <= 2 * n) == (0x3F, True, True), calls


def test_registers_that_read_each_other_in_a_circle_answer_as_if_recomputed_each_time():
    """Three registers that read one another's summaries in a circle (a reads b and c, b reads a
    and c, c reads a and b): a register's condition, and so its transitions, depend on which of
    the others are busy, so once one commits its event register the look's kept conditions and
    summaries no longer hold and are dropped (#1025). The replies are the ones every register
    gives when nothing is kept at all; keeping them past a commit makes c's event register 7
    (#1033)."""
    data = dict(DEF)
    params = {
        "a": {"bits": {"BUSY": 0, "B": 2, "C": 3}, "enable": 4, "summary_bit": 0, "ntr": 14, "ptr": 1},
        "b": {"bits": {"BUSY": 0, "A": 1, "C": 3}, "enable": 14, "summary_bit": 1, "ntr": 0, "ptr": 15},
        "c": {"bits": {"BUSY": 0, "A": 1, "B": 2}, "enable": 15, "summary_bit": 2, "ntr": 0},
    }
    data["components"] = [{"name": "sys", "type": "system"}] + [
        {"name": name, "type": "status_register", "params": p | {"width": 8}} for name, p in params.items()
    ]
    data["commands"] = [
        {"header": f"{name.upper()}{kind}", "op": f"{name}.{op}", "set": False}
        for name in params
        for kind, op in (("EV", "event"), ("CO", "condition"))
    ]
    definition = InstrumentDefinition.model_validate(data)
    bench = BenchDefinition.model_validate(
        {"schema": "simubench.bench/1", "name": "d", "instruments": {"x": {"model": definition.id}}}
    )
    session = Session(bench, lambda _id: definition, clock="stepped", seed=1)
    busy = {"BUSY": False}
    session.instruments["x"].components["sys"].status_flags = lambda: dict(busy)  # type: ignore[method-assign]

    def q(on: bool, cmd: str) -> str:
        busy["BUSY"] = on
        session.write("x", cmd.encode() + b"\n")
        out = session.read("x")
        return out.decode().strip() if out is not None else ""

    replies = [
        q(True, "*STB?"),
        q(False, "BCO?"),
        q(True, "*STB?"),
        q(True, "*STB?"),
        q(True, "BCO?"),
        q(False, "CEV?"),
    ]
    assert replies == ["0", "0", "6", "6", "11", "5"]
