"""Components need no engine-internal knowledge (issue 628): the session's public mouth.

Every engine concept a component needs has a public name — ``session.solve()`` (bring the
operating point up to date before judging state), ``session.sync()`` (dynamic state to the
current instant), ``session.signal_changed(self)`` (one-shot-per-version refresh),
``session.all_components()`` — alongside the already-public ``unsettled`` / ``source_current`` /
``node_of`` / ``invalidate``. A component reaching into ``session._*`` would couple the model
library to engine internals, so none may: the scan below is the guard.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from simubench.engine.session import Session

pytestmark = pytest.mark.unit

COMPONENTS = Path(__file__).resolve().parent.parent / "src" / "simubench" / "library" / "components"


def private_accesses(source: str) -> list[tuple[int, str]]:
    """``(line, "session._<member>")`` for each private session member the source reads or calls:
    an attribute starting with ``_`` on ``session`` or ``….session``. Read from the syntax tree,
    so a comment or docstring that mentions one is not an access."""
    return accesses_in(list(ast.walk(ast.parse(source))))


def accesses_in(nodes: list[ast.AST]) -> list[tuple[int, str]]:
    found = []
    for node in nodes:
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            owner = node.value
            if (isinstance(owner, ast.Name) and owner.id == "session") or (
                isinstance(owner, ast.Attribute) and owner.attr == "session"
            ):
                found.append((node.lineno, f"session.{node.attr}"))
    return sorted(found)


#: Every component module's syntax-tree nodes, parsed and walked once at collection (not in the
#: 100 ms call phase, D49).
TREES = [
    (p.name, list(ast.walk(ast.parse(p.read_text(encoding="utf-8")))))
    for p in sorted(COMPONENTS.glob("*.py"))
]


def test_components_use_only_public_session():
    offenders = [f"{name}:{n}: {access}" for name, nodes in TREES for n, access in accesses_in(nodes)]
    assert offenders == []


def test_the_guard_sees_a_private_access():
    """The scan itself is live: a planted private access is caught and named."""
    source = "def sync(self):\n    if self._seen == self.session._signal_version:\n        return\n"
    assert private_accesses(source) == [(2, "session._signal_version")]
    assert private_accesses("# session._signal_version is private\nx = session.solve()\n") == []


# ---- the signal-version subscription --------------------------------------------------------------


def bare_session(version: int) -> Session:
    """A session carrying only what signal_changed touches: the version and the registry."""
    s = Session.__new__(Session)
    s._signal_version = version
    s._signal_seen = {}
    return s


def test_signal_changed_is_true_once_per_version_per_component():
    s = bare_session(3)
    comp = SimpleNamespace(uid="awg/ref")
    assert s.signal_changed(comp)  # a fresh component reads as changed
    assert not s.signal_changed(comp)  # consumed: once per version
    other = SimpleNamespace(uid="scope/ref")
    assert s.signal_changed(other)  # the subscription is per component
    s._signal_version += 1  # a signal change re-arms every subscriber
    assert s.signal_changed(comp)
    assert s.signal_changed(other)


def test_signal_changed_registry_is_per_component_not_per_call_site():
    """Two distinct components never consume each other's change, even at the same version."""
    s = bare_session(0)
    assert s.signal_changed(SimpleNamespace(uid="a"))
    assert s.signal_changed(SimpleNamespace(uid="b"))
    assert not s.signal_changed(SimpleNamespace(uid="a"))
