"""classify (static, commands.py) and Instrument._dispatch must route the same message to the
same place: the MCP's dangerous-command confirmation and the script scanner ask classify before
anything runs, so a divergence would confirm a command the instrument refuses — or miss one it
runs (issue 594). One test per built-in model; the corpus is every command header of the
definition, rendered both with and without its optional nodes, plus the common and built-in
commands in both directions. Routing needs no running bench: the instrument is built on a stub
session (no circuit, no solves), and every handler is replaced by a recorder.

The sweep is one batch over every model, not a one-behaviour unit test: the environments are
module-scoped (D49) and a chunk of the corpus runs per test, so the D49 unit-budget marker does
not apply — the module stays in the fast gate and a chunk costs ~30 ms locally."""

from __future__ import annotations

import datetime as dt
import gc

import pytest

from simubench.engine import commands
from simubench.engine.commands import classify
from simubench.engine.instrument import Instrument
from simubench.library.loader import DefinitionLibrary
from simubench.models.bench import InstrumentInstance
from simubench.scpi.errors import ScpiError
from simubench.scpi.parse import Pattern, parse_unit, resolve_tokens, split_units

LIB = DefinitionLibrary()


class _Routed(Exception):
    """A routing recorder fired: dispatch reached a handler."""


class _StubSession:
    """Just what building an Instrument and routing through it touch: no circuit, no time."""

    def __init__(self) -> None:
        self.seed = 1
        self.clock_mode = "stepped"
        self.idle_mode = "off"
        self.now_ns = 0
        self.port_net: dict[str, str] = {}

    def calendar(self) -> dt.datetime:
        return dt.datetime(2026, 1, 1)

    def port_signal(self, comp, port):  # pragma: no cover - construction-time wiring only
        raise AssertionError("routing must not ask for signals")

    def cached_output(self, comp, port):  # pragma: no cover - ditto
        raise AssertionError("routing must not read outputs")

    def port_ref(self, comp, port):
        return None

    def _solve(self):
        pass  # a status register's condition may read the circuit; routing never changes it

    def _sync(self):
        pass  # ditto, via a lockin's status flags

    def instrument_event(self, inst, kind, data):
        pass


_INSTRUMENTS: dict[str, tuple[Instrument, object]] = {}
_CORPUS: dict[str, list[str]] = {}


def _instrument(model: str) -> tuple[Instrument, object]:
    hit = _INSTRUMENTS.get(model)
    if hit is None:
        definition = LIB.get(model)
        instance = InstrumentInstance(model=model)
        hit = _INSTRUMENTS[model] = Instrument("x", definition, instance, _StubSession()), definition
    return hit


def _render(p: Pattern, suffix: str, *, optional: bool) -> str:
    """A header pattern as a concrete message: short forms, the first channel as the suffix,
    optional nodes dropped (or kept, for the second variant)."""
    if p.common:
        return f"*{p.nodes[0].long}"
    nodes = p.nodes if optional else [n for n in p.nodes if not n.optional]
    body = ":".join((n.short + (suffix if n.suffix else "")) for n in nodes)
    return (":" if p.text.lstrip().startswith(":") else "") + body


#: A chunk of messages per test: ~10 × 0.5 ms of routing leaves the D49 unit budget (100 ms)
#: a wide margin, on a loaded runner too — an unmatched message pays the -113's closest-header
#: suggestion, so the worst chunks sit an order above the average.
CHUNK = 10


def _corpus(definition, suffix: str) -> list[str]:
    """Every message the two implementations must agree on: the definition's own commands (each
    in its required form, and again with optional nodes and, for queries, as a query), then the
    common and built-in commands in both directions."""
    out: list[str] = []
    for c in definition.commands:
        p = Pattern.compile(c.header)
        out.append(_render(p, suffix, optional=False))
        if any(n.optional for n in p.nodes):
            out.append(_render(p, suffix, optional=True))
        if c.query:
            out.append(_render(p, suffix, optional=False) + "?")
    for word in sorted(commands.COMMON):
        out += [f"*{word}", f"*{word}?"]
    out += [
        ":SYSTem:ERRor?",
        ":SYSTem:ERR?",
        ":SYSTem:ERRor:COUNt?",
        ":SYSTem:ERRor:ALL?",
        ":SYST:ERR:NEXT?",
        ":SYSTem:ERRor",  # set: nothing to set — refused on both sides
        ":SYSTem:DATE?",
        ":SYSTem:TIME?",
        ":SYSTem:DATE 2026,1,1",
        ":SYSTem:TIME 12,0,0",
    ]
    return out


def _route(inst: Instrument, message: str) -> tuple[list[tuple[str, str | None]], ScpiError | None]:
    """Send the message through dispatch with every handler replaced by a recorder: nothing
    executes, only the routing decision is observable."""
    calls: list[tuple[str, str | None]] = []

    def record(name: str, with_header: bool = False):
        def handler(*args, **kwargs):
            calls.append((name, args[0].spec.header if with_header else None))
            raise _Routed

        return handler

    def run_recorder(compiled, unit, channel, suffixes):
        # The direction gate of the real _run stands before anything the command does; keep it so
        # a wrong-direction message refuses here exactly as it would at runtime.
        if (unit.query and not compiled.spec.query) or (not unit.query and not compiled.spec.set):
            raise ScpiError(-113, f"{unit.raw_header} is {'not a query' if unit.query else 'a query only'}")
        calls.append(("_run", compiled.spec.header))
        raise _Routed

    def builtin_recorder(unit, tokens):
        # The real _builtin answers a query of the error family and either direction of the clock
        # family; anything else returns None and dispatch falls through to undefined.
        named = commands.named_builtin(tokens)
        if named is not None and commands.builtin_runs(named, unit.query):
            calls.append(("_builtin", None))
            raise _Routed
        return None

    inst.runner._run = run_recorder  # the command layer lives in CommandRunner (#895)
    inst.common._common = record("_common")
    inst.opc_query = record("opc_query")
    inst.wait_pending = record("wait_pending")
    inst.arm_opc = record("arm_opc")
    inst.common._bus_trigger = record("_bus_trigger")
    inst.common._builtin = builtin_recorder
    # The dispatch prelude settles waits and samples status registers (#575) — real instrument
    # behaviour, not routing; a component's register condition may read the circuit, which this
    # harness does not build. Routing equivalence is about the command, not the sampling.
    inst.settle_pending = lambda: None
    inst.sample_status = lambda: None
    error: ScpiError | None = None
    try:
        for raw in split_units(message.encode()):
            unit = parse_unit(raw)
            tokens = resolve_tokens(unit, ())
            inst._execute(unit, tokens)
    except _Routed:
        pass
    except ScpiError as e:
        error = e
    return calls, error


def _assert_consistent(
    model: str, definition, message: str, calls: list[tuple[str, str | None]], error: ScpiError | None
) -> None:
    m = classify(definition, message)[0]
    where = (model, message, calls, error and error.code)
    if m.kind == "command":
        runs = [c for c in calls if c[0] == "_run"]
        if runs:
            assert runs[0][1] == m.spec.header, where
        else:
            # routed, but the channel/suffix value was refused before the handler ran: the same
            # spec matched (its -114 is a value error, not a routing refusal)
            assert error is not None and error.code == -114, where
    elif m.kind == "common":
        assert calls and error is None, where
    elif m.kind == "builtin":
        assert any(name == "_builtin" for name, _ in calls), where
    else:  # unsupported or invalid: nothing may route (unknown_commands: ignore swallows silently)
        assert not calls, where


@pytest.fixture(scope="module", autouse=True)
def _warm_component_registry():
    """Instrument construction hits the lazy component-registry import (D47) and builds every
    model's component tree; pay both in setup so a test's call phase only routes (D49: the
    environment is module-scoped and reused, the tests themselves stay fast)."""
    from simubench.engine.component import component_types

    component_types()
    for model in sorted(LIB.ids()):
        inst, definition = _instrument(model)
        # A first miss loads the coverage table and builds the suggestion index (D18); warm both.
        inst.coverage.entries()
        inst.coverage.closest(type("U", (), {"raw_header": ":NOSUCH"})())
        # Rendering the corpus compiles every command's pattern; pay it in setup too.
        _CORPUS[model] = _corpus(definition, str(inst.channel_number(1)))
    # The built environment is permanent: keep it out of every later GC scan, so a generation-2
    # pass mid-test cannot cost a 100 ms pause — the unit budget's whole margin.
    gc.collect()
    gc.freeze()


def _chunks() -> list[tuple[str, int]]:
    """(model, chunk index) for every model's corpus, at CHUNK messages per test. The count only
    depends on the definition's shape, so it is computed here from a placeholder suffix; the
    test renders the same corpus with the model's real channel numbering."""
    plan: list[tuple[str, int]] = []
    for model in sorted(LIB.ids()):
        total = len(_corpus(LIB.get(model), "1"))
        plan += [(model, i) for i in range((total + CHUNK - 1) // CHUNK)]
    return plan


@pytest.mark.parametrize(
    "model,chunk",
    _chunks(),
    ids=[f"{m}-{i}" for m, i in _chunks()],
)
def test_classify_routes_like_dispatch(model: str, chunk: int):
    inst, definition = _instrument(model)
    messages = _CORPUS[model][chunk * CHUNK : (chunk + 1) * CHUNK]
    for message in messages:
        calls, error = _route(inst, message)
        _assert_consistent(model, definition, message, calls, error)


def _told_apart_pair(when_a, when_b):
    """Two commands on one header, differing only in their ``when``: the validator's
    ``told_apart`` must agree with what classify actually routes (issue 641 — the validator's
    #602 semantics were an unsynced hand copy of the dispatch)."""
    from simubench.engine.commands import told_apart
    from simubench.models.definition import CommandSpec, InstrumentDefinition, ParamSpec

    def spec(when):
        return CommandSpec(
            header=":VOLT:MODE",
            state="ch.mode",
            params=[ParamSpec(name="mode", type="string")],
            when=when or None,
        )

    definition = InstrumentDefinition.model_validate(
        {
            "id": "pair",
            "model": "DMM",
            "manufacturer": "SimuBench",
            "class": "multimeter",
            "components": [{"name": "ch", "type": "channel"}],
            "commands": [spec(when_a).model_dump(), spec(when_b).model_dump()],
            "state": {"ch.mode": {"type": "string", "options": ["A", "B"]}},
        }
    )
    a, b = definition.commands[0], definition.commands[1]
    # told_apart predicts "b still runs for SOME value": quantify over the parameter space. A
    # refused pick (kind "unsupported") carries the refused spec — that is not b running.
    runs_b = any(
        (m := classify(definition, f":VOLT:MODE {v}" if v else ":VOLT:MODE"))
        and m[0].kind == "command"
        and m[0].spec is b
        for v in ("A", "B", "")
    )
    assert told_apart(a, b) == runs_b, (when_a, when_b)


def test_when_fallback_routing_agrees_with_told_apart():
    _told_apart_pair({"mode": "A"}, None)  # b is the fallback: b runs for any other value
    _told_apart_pair(None, {"mode": "B"})  # a matches everything: b never runs
    _told_apart_pair({"mode": "A"}, {"mode": "B"})  # disjoint values: each runs on its own
    _told_apart_pair({"mode": "A"}, {"mode": "A"})  # same value: the later never runs
