#!/usr/bin/env python
"""Keep the package layers pointing one way (D87, #1507).

The top-level packages of ``src/simubench`` (and ``src/pyvisa_simubench``) sit in the layers of
:data:`LAYERS`, lowest first. Three rules, checked on every ``import`` the code runs — module level
and inside functions alike (a late import is still a dependency); imports under
``if TYPE_CHECKING:`` are annotations only and are not counted:

1. a package imports only packages of its own layer or a lower one;
2. the packages form no cycle, inside a layer either;
3. the web and MCP frameworks (``fastapi``, ``starlette``, ``mcp``) are imported only from the
   server layer up, and never from ``server/services`` (a service raises domain errors only, the
   routes and the MCP tools map them: codebase-map, D76).

A package missing from :data:`LAYERS` fails too: a new top-level package takes its place here.
Exit code 1 with one line per problem. Fast and offline: part of ``scripts/check.sh``.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Lowest first. A module directly in ``src/simubench`` is a package of its own, named by its stem.
LAYERS: list[tuple[str, frozenset[str]]] = [
    # Plain helpers every layer may use; the version is the root package's ``__init__``.
    ("base", frozenset({"__init__", "_version", "units", "jsonsafe", "errors", "paths"})),
    ("scpi", frozenset({"scpi"})),
    # File formats and their pure transforms (migration, macros, same_as, panels, classes).
    ("models", frozenset({"models"})),
    ("engine", frozenset({"engine"})),
    # Component implementations, definition loading, coverage tables, templates.
    ("library", frozenset({"library"})),
    # Opening a bench in process, and what is built on it: replay, verdicts, case runs, pytest.
    ("api", frozenset({"api", "replay", "verdicts", "cases", "pytest_plugin"})),
    # The HTTP/WS message models and the agent tools' input models (D21, D57).
    ("contracts", frozenset({"contracts"})),
    ("access", frozenset({"access", "pyvisa_simubench"})),
    ("server", frozenset({"server", "storage", "mcp_tools", "mcp_server", "service"})),
    ("clients", frozenset({"tui", "cli"})),
]
FRAMEWORKS = ("fastapi", "starlette", "mcp")
FRAMEWORK_FLOOR = "server"  # the lowest layer that may import them
NO_FRAMEWORK = ("simubench.server.services",)  # and never these, whatever their layer


@dataclass(frozen=True)
class Edge:
    source: str  # the importing package
    target: str  # the imported top-level name (a package of ours, or a third-party module)
    path: str  # the importing file, relative to the repository
    line: int
    module: str  # the importing module, dotted


def _type_checking_nodes(tree: ast.AST) -> set[int]:
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and ast.unparse(node.test) in ("TYPE_CHECKING", "typing.TYPE_CHECKING"):
            for stmt in node.body:
                skip.update(id(n) for n in ast.walk(stmt))
    return skip


def _modules(src: Path) -> Iterator[tuple[Path, list[str]]]:
    """Each Python file under ``src/simubench`` and ``src/pyvisa_simubench`` with its dotted name."""
    for top in ("simubench", "pyvisa_simubench"):
        base = src / top
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            parts = list(path.relative_to(src).with_suffix("").parts)
            yield path, parts


def package_of(parts: list[str]) -> str:
    """The layer-table name of a module: ``pyvisa_simubench``, or the first name below ``simubench``."""
    if parts[0] != "simubench":
        return parts[0]
    return parts[1] if len(parts) > 1 else "__init__"


def edges(root: Path = ROOT) -> list[Edge]:
    """Every runtime import from one top-level package to another, or to a third-party module."""
    src = root / "src"
    modules = list(_modules(src))
    ours = {package_of(parts) for _, parts in modules}
    out: list[Edge] = []
    for path, parts in modules:
        is_init = parts[-1] == "__init__"
        mod_parts = parts[:-1] if is_init else parts
        package = mod_parts if is_init else mod_parts[:-1]
        source = package_of(parts)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip = _type_checking_nodes(tree)
        for node in ast.walk(tree):
            if id(node) in skip:
                continue
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                # Each imported name may be a submodule (``from simubench import cases``), so it is
                # part of the target; a name that is not a module falls back to its package below.
                base = package[: len(package) - (node.level - 1)] if node.level else []
                module = ".".join([*base, *([node.module] if node.module else [])])
                targets = [f"{module}.{a.name}" for a in node.names]
            else:
                continue
            for target in targets:
                names = target.split(".")
                if names[0] == "simubench":
                    # simubench.<package>…, or a name of the root package itself (__version__)
                    name = names[1] if len(names) > 1 and names[1] in ours else "__init__"
                elif names[0] == "pyvisa_simubench":
                    name = names[0]
                else:
                    name = names[0]
                if name == source:
                    continue
                out.append(
                    Edge(source, name, path.relative_to(root).as_posix(), node.lineno, ".".join(mod_parts))
                )
    return out


def layer_of(name: str) -> int | None:
    for i, (_, members) in enumerate(LAYERS):
        if name in members:
            return i
    return None


def _cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """The strongly connected components with more than one package (Tarjan)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on: set[str] = set()
    found: list[list[str]] = []
    counter = [0]

    def visit(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in sorted(graph.get(v, ())):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                found.append(sorted(comp))

    for v in sorted(graph):
        if v not in index:
            visit(v)
    return found


def problems(root: Path = ROOT) -> list[str]:
    all_edges = edges(root)
    src = root / "src"
    ours = {package_of(parts) for _, parts in _modules(src)}
    out: list[str] = []
    for name in sorted(ours):
        if layer_of(name) is None:
            out.append(f"{name}: not in LAYERS (scripts/check_layers.py): give the new package its layer")
    floor = next(i for i, (layer, _) in enumerate(LAYERS) if layer == FRAMEWORK_FLOOR)
    graph: dict[str, set[str]] = {}
    for e in all_edges:
        src_layer = layer_of(e.source)
        if e.target in ours:
            graph.setdefault(e.source, set()).add(e.target)
            dst_layer = layer_of(e.target)
            if src_layer is not None and dst_layer is not None and dst_layer > src_layer:
                out.append(
                    f"{e.path}:{e.line}: {e.source} ({LAYERS[src_layer][0]}) imports {e.target} "
                    f"({LAYERS[dst_layer][0]}), a higher layer"
                )
        elif e.target in FRAMEWORKS:
            low_layer = src_layer is not None and src_layer < floor
            banned = any(e.module == m or e.module.startswith(m + ".") for m in NO_FRAMEWORK)
            if low_layer or banned:
                out.append(f"{e.path}:{e.line}: {e.module} imports {e.target}: frameworks stay at the seams")
    for cycle in _cycles(graph):
        sites = list(
            dict.fromkeys(f"{e.path}:{e.line}" for e in all_edges if e.source in cycle and e.target in cycle)
        )
        out.append(f"cycle between {', '.join(cycle)}: {'; '.join(sites[:6])}")
    return list(dict.fromkeys(out))  # one line per problem: ``from x import a, b`` is one import


def main() -> int:
    found = problems()
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
