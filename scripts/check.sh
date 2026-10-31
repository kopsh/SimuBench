#!/usr/bin/env bash
# The fast checks CI runs, in the same order; run before committing.
set -euo pipefail
cd "$(dirname "$0")/.."
# Source files that .gitignore hides would pass here and fail in CI.
ignored=$(git ls-files --others --ignored --exclude-standard -- src tests acceptance refcalc web/src web/e2e | grep -v -e __pycache__ -e '\.pyc$' || true)
if [ -n "$ignored" ]; then echo "ignored by .gitignore but part of the source:"; echo "$ignored"; exit 1; fi
uv run ruff check src tests acceptance refcalc
uv run ruff format --check src tests acceptance refcalc
uv run pytest -q
uv run python scripts/check_tui_i18n.py
uv run simubench schemas --out schemas >/dev/null
git diff --exit-code --stat schemas || { echo "schemas changed: commit them"; exit 1; }
cd web
pnpm -s check:api
pnpm -s typecheck
pnpm -s lint
pnpm -s test
