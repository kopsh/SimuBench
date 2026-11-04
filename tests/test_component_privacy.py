"""Components need no engine-internal knowledge (issue 628): the session's public mouth.

Every engine concept a component needs has a public name — ``session.solve()`` (bring the
operating point up to date before judging state), ``session.sync()`` (dynamic state to the
current instant), ``session.signal_changed(self)`` (one-shot-per-version refresh),
``session.all_components()`` — alongside the already-public ``unsettled`` / ``source_current`` /
``node_of`` / ``invalidate``. A component reaching into ``session._*`` would couple the model
library to engine internals, so none may: the scan below is the guard.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from simubench.engine.session import Session

pytestmark = pytest.mark.unit

COMPONENTS = Path(__file__).resolve().parent.parent / "src" / "simubench" / "library" / "components"

#: ``session._<member>`` — a private session member a component reads or calls.
PRIVATE_ACCESS = re.compile(r"session\._\w+")


def test_components_use_only_public_session():
    offenders = [
        f"{p.name}:{n}: {line.strip()}"
        for p in sorted(COMPONENTS.glob("*.py"))
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if PRIVATE_ACCESS.search(line)
    ]
    assert offenders == []


def test_the_guard_sees_a_private_access():
    """The scan itself is live: a planted private access is caught and named."""
    source = "def sync(self):\n    if self._seen == self.session._signal_version:\n        return\n"
    assert [
        PRIVATE_ACCESS.search(line).group() for line in source.splitlines() if PRIVATE_ACCESS.search(line)
    ] == ["session._signal_version"]


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
