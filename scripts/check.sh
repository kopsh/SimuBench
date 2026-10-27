#!/usr/bin/env bash
# The fast checks CI runs, in the same order; run before committing.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run ruff check src tests acceptance refcalc
uv run ruff format --check src tests acceptance refcalc
uv run pytest -q
uv run simubench schemas --out schemas >/dev/null
git diff --exit-code --stat schemas || { echo "schemas changed: commit them"; exit 1; }
cd web
pnpm -s check:api
pnpm -s typecheck
pnpm -s lint
pnpm -s test
