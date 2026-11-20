"""The scope family's seam is closed (issue #1724): a block reads its component only through the
members its family host protocol declares.

Each scope block takes one host, the ``*_host.py`` protocol its constructor annotates; the 71
``host.owner.*`` penetrations the family split left open are gone — the four protocols name the
state, formatting and pending-operation members the blocks consume, and only the acquisition
family keeps an ``owner`` (identity only: the recorder's acquisition event is emitted with the
instrument as its subject). The scan reads the syntax tree, so a comment or docstring that
mentions a member is not an access."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

COMPONENTS = Path(__file__).resolve().parent.parent / "src" / "simubench" / "library" / "components"

#: The family host protocols, by the module each lives in.
PROTOCOLS = {
    "AcquisitionHost": "scope_acquisition_host.py",
    "MeasurementsHost": "scope_measurements_host.py",
    "MathHost": "scope_math_host.py",
    "TriggerHost": "scope_trigger_host.py",
}


def protocol_members(name: str) -> set[str]:
    """The members a family host protocol declares: its methods (properties and static methods
    included) and annotated attributes."""
    tree = ast.parse((COMPONENTS / PROTOCOLS[name]).read_text(encoding="utf-8"))
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == name)
    members = set()
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            members.add(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            members.add(node.target.id)
    return members


def _is_host(expr: ast.expr) -> bool:
    """The block's seam: ``self.host`` (a bare ``host`` name is the constructor's argument, not
    an access)."""
    return (
        isinstance(expr, ast.Attribute)
        and expr.attr == "host"
        and isinstance(expr.value, ast.Name)
        and expr.value.id == "self"
    )


def accesses_in(nodes: list[ast.AST]) -> list[tuple[int, str]]:
    """``(line, "host.<attr>")`` for each attribute the source reads on its host, and
    ``host.owner.<attr>`` for each read *through* the owner — the penetration shape."""
    found = []
    for node in nodes:
        if isinstance(node, ast.Attribute):
            if _is_host(node.value):
                found.append((node.lineno, f"host.{node.attr}"))
            elif (
                isinstance(node.value, ast.Attribute)
                and node.value.attr == "owner"
                and _is_host(node.value.value)
            ):
                found.append((node.lineno, f"host.owner.{node.attr}"))
    return sorted(found)


def _imported_protocol(path: Path) -> str | None:
    """The family host protocol a block's constructor annotates, None when the file only borrows
    helpers from a ``*_host`` module (scope_math.py, scope_trigger.py)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.startswith("scope_")
            and node.module.endswith("_host")
        ):
            for alias in node.names:
                if alias.name in PROTOCOLS:
                    return alias.name
    return None


#: Every scope module's syntax-tree attribute nodes and its family protocol, read once at
#: collection so the guard's call phase stays inside the unit budget as the family grows.
BLOCKS = [
    (
        p.name,
        _imported_protocol(p),
        [n for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))) if isinstance(n, ast.Attribute)],
    )
    for p in sorted(COMPONENTS.glob("scope_*.py"))
]


def test_blocks_read_only_protocol_members():
    offenders = []
    for name, protocol, nodes in BLOCKS:
        if protocol is None:
            continue
        members = protocol_members(protocol)
        offenders += [
            f"{name}:{line}: {access}"
            for line, access in accesses_in(nodes)
            if access.removeprefix("host.").split(".")[0] not in members
        ]
    assert offenders == []


def test_no_block_reads_through_the_owner():
    """The owner is no state or service seam: even the acquisition family's identity use must
    stay a bare ``host.owner`` (the event subject), never a read or call past it."""
    through = [
        f"{name}:{line}: {access}"
        for name, protocol, nodes in BLOCKS
        if protocol is not None
        for line, access in accesses_in(nodes)
        if access.startswith("host.owner.")
    ]
    assert through == []


def test_the_guard_sees_a_penetration():
    """The scan itself is live: the old shapes are caught and named."""
    old_owner = (
        "def op(self, path):\n"
        "    assert self.host.owner is not None\n"
        "    return self.host.owner.format_number(path)\n"
    )
    assert accesses_in(list(ast.walk(ast.parse(old_owner)))) == [
        (2, "host.owner"),
        (3, "host.owner"),
        (3, "host.owner.format_number"),
    ]
    old_private = "def run(self, mode):\n    if self.host._is(mode):\n        return self.host._timing()\n"
    assert accesses_in(list(ast.walk(ast.parse(old_private)))) == [
        (2, "host._is"),
        (3, "host._timing"),
    ]
    clean = (
        "def op(self, path):\n"
        "    value = self.host.get_state(path)\n"
        "    self.host.recorder.store({}, 0)\n"
        "    return self.host.on_panel\n"
    )
    assert accesses_in(list(ast.walk(ast.parse(clean)))) == [
        (2, "host.get_state"),
        (3, "host.recorder"),
        (4, "host.on_panel"),
    ]
