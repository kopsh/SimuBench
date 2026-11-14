"""The per-component boilerplate taxes stay paid to the base class, once (issue 629).

A component type declares parameters and refs; what it used to re-derive by hand now comes from
the base class and ``scpi.values``, in one spelling each:

* ``on_state(self, key, path, old, new)`` receives the parameter the change arrived through — no
  ``bound_paths()`` inversion, no per-key ``bound_paths().get(...)`` lookups (the forward lookup
  is ``bound_path(key)``; only the ``bound_paths`` overrides that feed watcher registration may
  call it, via ``super()``);
* sibling components resolve through ``peer(name)`` / ``ref(key)`` — no ``ch{self.channel}``
  name building and no ``components[str(self.param(...))]`` bracket lookups (a lookup of another
  channel by its number, as the SDG's copy of another channel's ``limits``, stays a direct one:
  ``peer`` is for the component's own channel);
* boolean-ish settings parse through ``V.flag`` — no private ``_flag`` spellings.

The scan below is the guard; the last tests keep it live.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

COMPONENTS = Path(__file__).resolve().parent.parent / "src" / "simubench" / "library" / "components"

#: A guard: from the AST nodes of one source, those that pay the tax by hand. The scans read the
#: syntax tree, so a comment or a string that only mentions a spelling is never an offender.
Guard = Callable[[list[ast.AST]], Iterator[ast.AST]]


def _is_self_attr(node: ast.AST, prefix: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        and node.attr.startswith(prefix)
    )


def channel_name(nodes: list[ast.AST]) -> Iterator[ast.AST]:
    """``f"ch{self.channel}.…"`` — building a sibling's registry name by hand instead of ``peer``."""
    for node in nodes:
        if (
            isinstance(node, ast.JoinedStr)
            and len(node.values) >= 2
            and isinstance(node.values[0], ast.Constant)
            and node.values[0].value == "ch"
            and isinstance(node.values[1], ast.FormattedValue)
            and _is_self_attr(node.values[1].value, "channel")
        ):
            yield node


def _by_number(key: ast.AST) -> bool:
    """``str(...)``, ``self.param(...)`` or ``self.params[...]`` — a registry key computed from a
    parameter."""
    if isinstance(key, ast.Subscript):
        return _is_self_attr(key.value, "param")
    if not isinstance(key, ast.Call):
        return False
    func = key.func
    return (isinstance(func, ast.Name) and func.id == "str") or _is_self_attr(func, "param")


def _is_components(node: ast.AST) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "components") or (
        isinstance(node, ast.Name) and node.id == "components"
    )


def bracket_accessor(nodes: list[ast.AST]) -> Iterator[ast.AST]:
    """``components[str(self.param("x"))]`` and its ``.get`` twin — the KeyError / None pair."""
    for node in nodes:
        if isinstance(node, ast.Subscript) and _is_components(node.value) and _by_number(node.slice):
            yield node
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and _is_components(node.func.value)
            and node.args
            and _by_number(node.args[0])
        ):
            yield node


def old_on_state(nodes: list[ast.AST]) -> Iterator[ast.AST]:
    """The old three-argument ``on_state`` hook (the engine passes the parameter name first)."""
    for node in nodes:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == "on_state":
            names = [a.arg for a in node.args.args]
            if names[:2] == ["self", "path"]:
                yield node


def private_flag(nodes: list[ast.AST]) -> Iterator[ast.AST]:
    """A private ``_flag`` parser instead of ``V.flag``."""
    for node in nodes:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == "_flag":
            yield node


def duck_probe(nodes: list[ast.AST]) -> Iterator[ast.AST]:
    """``hasattr(comp, "...")`` — probing a peer's duck interface instead of a declared Protocol
    (engine.component: Sampler, Acquisition, TraceSource; issue 630)."""
    for node in nodes:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "hasattr":
            yield node


def _lines_of(guard: Guard, nodes: list[ast.AST]) -> list[int]:
    return sorted({getattr(node, "lineno", 0) for node in guard(nodes)})


def found_in(guard: Guard, source: str) -> list[int]:
    """The line numbers of ``source`` where ``guard`` finds the tax paid by hand."""
    return _lines_of(guard, list(ast.walk(ast.parse(source))))


#: Every component module's name, lines and syntax-tree nodes, parsed and walked once at
#: collection (not in the 100 ms call phase, D49): the six scans then only filter lists in memory.
SOURCES = [
    (p.name, text.splitlines(), list(ast.walk(ast.parse(text))))
    for p in sorted(COMPONENTS.glob("*.py"))
    for text in [p.read_text(encoding="utf-8")]
]


def _offenders(guard: Guard) -> list[str]:
    return [
        f"{name}:{n}: {lines[n - 1].strip()}"
        for name, lines, nodes in SOURCES
        for n in _lines_of(guard, nodes)
    ]


def bound_paths_lookup(line: str) -> bool:
    """A ``bound_paths()`` call that is not the override feeding watcher registration."""
    return "bound_paths()" in line and "def bound_paths" not in line and "super().bound_paths()" not in line


def test_no_bound_paths_lookups_in_components():
    offenders = [
        f"{name}:{n}: {line.strip()}"
        for name, lines, _ in SOURCES
        for n, line in enumerate(lines, 1)
        if bound_paths_lookup(line)
    ]
    assert offenders == []


#: The scans read only SOURCES (nothing changes at call time), so they run once at collection,
#: not in the 100 ms call phase (D49).
HAND_BUILT = _offenders(channel_name)
BRACKET = _offenders(bracket_accessor)
PRIVATE_FLAGS = _offenders(private_flag)
OLD_ON_STATE = _offenders(old_on_state)
DUCK_PROBES = _offenders(duck_probe)


def test_no_handbuilt_channel_sibling_names():
    assert HAND_BUILT == []


def test_no_bracket_sibling_accessors():
    assert BRACKET == []


def test_no_private_flag_parsers():
    assert PRIVATE_FLAGS == []


def test_on_state_hooks_take_the_parameter_name():
    assert OLD_ON_STATE == []


def test_no_duck_probing_of_peers():
    assert DUCK_PROBES == []


@pytest.mark.parametrize(
    ("guard", "planted"),
    [
        (channel_name, 'full = f"ch{self.channel}.{name}"'),
        (bracket_accessor, 'stage = self.owner.components[str(self.param("stage"))]'),
        (bracket_accessor, 'stage = self.owner.components.get(self.param("stage"))'),
        (bracket_accessor, 'stage = components[self.params["stage"]]'),
        (private_flag, "def _flag(value):\n    return bool(value)"),
        (old_on_state, "def on_state(self, path, old, new):\n    return None"),
        (duck_probe, 'if hasattr(comp, "trace_value"):\n    x = comp'),
    ],
)
def test_the_guard_sees_the_boilerplate(guard: Guard, planted: str):
    """The scans are live: each planted line is caught and named."""
    assert found_in(guard, planted) == [1]


@pytest.mark.parametrize(
    "planted",
    [
        "key = {v: k for k, v in self.bound_paths().items()}",
        "path = comp.bound_paths().get('enable')",
    ],
)
def test_the_bound_paths_guard_sees_a_lookup(planted: str):
    assert bound_paths_lookup(planted)
    assert not bound_paths_lookup("paths = super().bound_paths()")  # the override's own call


@pytest.mark.parametrize(
    ("guard", "harmless"),
    [
        (channel_name, "note = 'peer() replaces f\"ch{self.channel}\"'  # a string mentions it"),
        (duck_probe, "# hasattr(comp, 'x') is not called here"),
        (old_on_state, "def on_state(self, key, path, old, new):\n    return None"),
    ],
)
def test_the_guard_ignores_what_only_mentions_a_spelling(guard: Guard, harmless: str):
    assert found_in(guard, harmless) == []
