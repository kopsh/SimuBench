"""Bad numbers are never accepted as a boolean, a ladder rung or a numeric raw parameter (#1450,
#1495, #1586): every built-in model's boolean and ladder setting commands and its cursor-position,
tracked-marker and math-horizontal operations, on its example bench, refuse them with a parameter
error — never the -300 of an internal error — and leave every setting as it was (the marker mode
included). One sweep over the example benches, each opened once and reused for all of its commands
(D49's flow form, not one bench per command)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simubench.api import open_bench
from simubench.scpi.parse import Pattern

ROOT = Path(__file__).resolve().parents[1]
BENCHES = sorted((ROOT / "examples" / "benches").glob("*.bench.yaml"))


def _message(header: str) -> str:
    """A header pattern as a concrete message: short forms, optional nodes dropped, suffix 1."""
    p = Pattern.compile(header)
    if p.common:
        return f"*{p.nodes[0].long}"
    body = ":".join(n.short + ("1" if n.suffix else "") for n in p.nodes if not n.optional)
    return (":" if p.text.lstrip().startswith(":") else "") + body


class _Ones(dict):
    """Every ``{placeholder}`` of a state path as 1: the first channel or slot."""

    def __missing__(self, key: str) -> str:
        return "1"


def _bool_and_ladder_commands(inst) -> list[str]:
    """The instrument's setting commands bound to a boolean or a ladder setting."""
    specs = inst.settings.specs
    out = []
    for c in inst.definition.commands:
        if not c.state or not c.set:
            continue
        candidates = (c.state, c.state.replace("ch.", "ch1.", 1), c.state.format_map(_Ones()))
        path = next((p for p in candidates if p in specs), None)
        assert path is not None, f"{inst.definition.id}: {c.header} names no setting ({c.state})"
        if specs[path].type == "bool" or specs[path].ladder is not None:
            out.append(_message(c.header))
    return out


#: The operations whose raw parameter is a number (#1586): a cursor position, a tracked marker
#: level, or a math function's horizontal position/range.
_NUMERIC_RAW_OPS = ("meas.x_position", "meas.y_position", "meas.tracked_y", "math.h_field")


def _numeric_raw_commands(inst) -> list[str]:
    """The instrument's operation commands that parse their raw parameter as a number."""
    return [
        _message(c.header)
        for c in inst.definition.commands
        if c.op in _NUMERIC_RAW_OPS and any(p.type == "raw" for p in c.params)
    ]


@pytest.mark.parametrize("bench", BENCHES, ids=lambda p: p.name)
def test_bad_numbers_are_refused_without_an_internal_error(bench):
    s = open_bench(bench, seed=1, idle="off")
    try:
        sent = 0
        for name, inst in s.instruments.items():
            for message in _bool_and_ladder_commands(inst):
                for word in ("INF", "-INF", "NAN"):
                    before = dict(inst.settings.values)
                    codes = [e.code for e in s.command(name, f"{message} {word}").errors]
                    assert codes and -300 not in codes, f"{name}: {message} {word} -> {codes}"
                    assert inst.settings.values == before, f"{name}: {message} {word} changed a setting"
                    sent += 1
            for message in _numeric_raw_commands(inst):
                for word in ("MAX", "INF", "NAN", '""', "ON"):
                    before = dict(inst.settings.values)
                    codes = [e.code for e in s.command(name, f"{message} {word}").errors]
                    assert codes and -300 not in codes, f"{name}: {message} {word} -> {codes}"
                    assert inst.settings.values == before, f"{name}: {message} {word} changed a setting"
                    sent += 1
        assert sent > 0
    finally:
        s.close()


def test_every_built_in_model_is_on_an_example_bench():
    """The sweep reaches every built-in model only through the example benches: none is left out."""
    shipped = ROOT / "src" / "simubench" / "library" / "instruments"
    built_in = {p.stem for p in shipped.glob("*.yaml") if not p.name.endswith(".coverage.yaml")}
    on_benches = {
        i["model"]
        for bench in BENCHES
        for i in yaml.safe_load(bench.read_text(encoding="utf-8"))["instruments"].values()
    }
    assert built_in and built_in <= on_benches, sorted(built_in - on_benches)
