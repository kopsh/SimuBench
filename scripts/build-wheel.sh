#!/usr/bin/env bash
# Build an installable wheel with the web UI inside: pip install dist/simubench-*.whl, then
# `simubench serve` works without Node or a checkout.
set -euo pipefail
cd "$(dirname "$0")/.."
(cd web && pnpm install --frozen-lockfile && pnpm build)
rm -rf src/simubench/web_dist
cp -r web/dist src/simubench/web_dist
trap 'rm -rf src/simubench/web_dist' EXIT
rm -rf dist
uv build --wheel
ls -l dist/*.whl
