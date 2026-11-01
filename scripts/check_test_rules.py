#!/usr/bin/env python
"""The test rules of D49, checked mechanically over the test sources.

- e2e (``tests/test_*e2e*.py``): every test makes **at least 5 assertions** — the environment is
  the expensive part of an e2e run, so one setup must pay for enough verification.
- environment reuse (D49): a fixture that starts a server (``Served(``/``uvicorn.``) must be
  module- or session-scoped; function-scoped server fixtures are rejected.

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


def main() -> int:
    failures: list[str] = []
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
