#!/usr/bin/env python
"""The test rules of D49, checked mechanically over the test sources.

- e2e (``tests/test_*e2e*.py``): every test makes **at least 5 assertions** — the environment is
  the expensive part of an e2e run, so one setup must pay for enough verification.
- environment reuse (D49): a fixture that starts a server (``Served(``/``uvicorn.``) must be
  module- or session-scoped; function-scoped server fixtures are rejected.
- no regular expressions (D49, #926): a test judges a value by parsing it and comparing it with
  the expected value ± a tolerance, and text by plain substrings or equality — never by a pattern
  of how it is printed. ``tests/`` and ``acceptance/`` import no ``re`` and pass no ``match=`` to
  ``pytest.raises`` / ``pytest.warns`` (``helpers.raises_with`` checks a message by substrings,
  ``helpers.Near`` a reading against its band).

Exit code 1 with one line per violation. Purely syntactic (ast), no imports of the package.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIN_E2E_ASSERTS = 5

Func = ast.FunctionDef | ast.AsyncFunctionDef


def functions(tree: ast.Module) -> list[Func]:
    return [n for n in ast.walk(tree) if isinstance(n, Func)]


def count_asserts(fn: Func) -> int:
    return sum(1 for n in ast.walk(fn) if isinstance(n, ast.Assert))


def is_fixture(fn: Func) -> bool:
    return any(getattr(d, "attr", "") == "fixture" for d in fn.decorator_list)


def fixture_scope(fn: Func) -> str:
    for deco in fn.decorator_list:
        if isinstance(deco, ast.Call):
            name = getattr(deco.func, "attr", "")
        else:
            name = getattr(deco, "attr", "")
        if name == "fixture":
            if isinstance(deco, ast.Call):
                for kw in deco.keywords:
                    if kw.arg == "scope" and isinstance(kw.value, ast.Constant):
                        return str(kw.value.value)
            return "function"  # @pytest.fixture without scope=
    return "none"


def starts_server(fn: Func) -> bool:
    return any(
        isinstance(n, ast.Call)
        and (
            getattr(n.func, "id", "") == "Served"
            or getattr(n.func, "attr", "") == "Config"  # uvicorn.Config
            or getattr(n.func, "attr", "") == "run"  # uvicorn server.run / app.run_test
        )
        for n in ast.walk(fn)
    )


def regex_uses(tree: ast.Module) -> list[tuple[int, str]]:
    """``(line, what)`` for each use of regular expressions: an import of ``re`` or a ``match=``
    pattern given to ``raises`` / ``warns``."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node.lineno, "import re") for a in node.names if a.name.split(".")[0] == "re"]
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "re" and not node.level:
            found.append((node.lineno, "from re import"))
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "attr", "") or getattr(node.func, "id", "")
            if name in ("raises", "warns") and any(kw.arg == "match" for kw in node.keywords):
                found.append((node.lineno, f"{name}(match=...)"))
    return found


def main() -> int:
    failures: list[str] = []
    for path in sorted([*(ROOT / "tests").rglob("*.py"), *(ROOT / "acceptance").rglob("*.py")]):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, what in regex_uses(tree):
            failures.append(
                f"{path.relative_to(ROOT).as_posix()}:{line} {what}: a test judges values and text"
                " without regular expressions — parse and compare (helpers.Near, helpers.raises_with; D49)"
            )
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in functions(tree):
            if "e2e" in path.name and fn.name.startswith("test_"):
                count = count_asserts(fn)
                if count < MIN_E2E_ASSERTS:
                    failures.append(
                        f"{path.name}:{fn.lineno} {fn.name}: {count} asserts < {MIN_E2E_ASSERTS}"
                        " — an e2e run's cost is its environment; assert enough per run (D49)"
                    )
            if is_fixture(fn) and starts_server(fn) and fixture_scope(fn) == "function":
                failures.append(
                    f"{path.name}:{fn.lineno} {fn.name}: a server fixture must be module- or"
                    ' session-scoped (@pytest.fixture(scope="module")) — reuse the environment (D49)'
                )
    for line in failures:
        print(line)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
