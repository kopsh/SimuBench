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

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

COMPONENTS = Path(__file__).resolve().parent.parent / "src" / "simubench" / "library" / "components"

#: ``f"ch{self.channel}.`` — building a sibling's registry name by hand instead of ``peer``.
CHANNEL_NAME = re.compile(r'f"ch\{self\.channel\}')
#: ``components[str(self.param("x"))]`` and its ``.get`` twin — the KeyError / None accessor pair.
BRACKET_ACCESSOR = re.compile(r"components\[(?:str\(|self\.param)|components\.get\((?:str\(|self\.param)")
#: The old three-argument ``on_state`` hook (the engine passes the parameter name first).
OLD_ON_STATE = re.compile(r"def on_state\(self, path")


def _offenders(pattern: re.Pattern[str]) -> list[str]:
    return [
        f"{p.name}:{n}: {line.strip()}"
        for p in sorted(COMPONENTS.glob("*.py"))
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]


def test_no_bound_paths_lookups_in_components():
    offenders = [
        f"{p.name}:{n}: {line.strip()}"
        for p in sorted(COMPONENTS.glob("*.py"))
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "bound_paths()" in line and "def bound_paths" not in line and "super().bound_paths()" not in line
    ]
    assert offenders == []


def test_no_handbuilt_channel_sibling_names():
    assert _offenders(CHANNEL_NAME) == []


def test_no_bracket_sibling_accessors():
    assert _offenders(BRACKET_ACCESSOR) == []


def test_no_private_flag_parsers():
    assert _offenders(re.compile(r"def _flag\b")) == []


def test_on_state_hooks_take_the_parameter_name():
    assert _offenders(OLD_ON_STATE) == []


@pytest.mark.parametrize(
    ("pattern", "planted"),
    [
        ("bound_paths()", "key = {v: k for k, v in self.bound_paths().items()}"),
        ("bound_paths()", "path = comp.bound_paths().get('enable')"),
        (CHANNEL_NAME.pattern, 'full = f"ch{self.channel}.{name}"'),
        (BRACKET_ACCESSOR.pattern, 'stage = self.owner.components[str(self.param("stage"))]'),
        (r"def _flag\b", "def _flag(value):\n    return bool(value)"),
        (OLD_ON_STATE.pattern, "def on_state(self, path, old, new):\n    return None"),
    ],
)
def test_the_guard_sees_the_boilerplate(pattern: str, planted: str):
    """The scans are live: each planted line is caught and named."""
    found = [line for line in planted.splitlines() if re.search(pattern, line)]
    assert len(found) == 1
