#!/usr/bin/env bash
# Build an installable wheel with the web UI inside: pip install dist/simubench-*.whl, then
# `simubench serve` works without Node or a checkout. Beside it, dist/CHANGELOG.md: what changed
# for the user, by day (D58); the same file is in the wheel (`simubench changelog`). It needs the
# full git history (no tags: the version is computed from it); without it the wheel is built without
# a changelog. Release notes of this build: pass the version published before it (argument or
# SIMUBENCH_SINCE) and dist/RELEASE_NOTES.md lists only what is new since then, to post as is.
set -euo pipefail
cd "$(dirname "$0")/.."
(cd web && pnpm install --frozen-lockfile && pnpm build)
rm -rf src/simubench/web_dist src/simubench/CHANGELOG.md
cp -r web/dist src/simubench/web_dist
trap 'rm -rf src/simubench/web_dist src/simubench/CHANGELOG.md' EXIT
python3 scripts/changelog.py -o src/simubench/CHANGELOG.md ||
  echo "warning: no changelog in this build (see above)" >&2
rm -rf dist
uv build --wheel
[ -f src/simubench/CHANGELOG.md ] && cp src/simubench/CHANGELOG.md dist/CHANGELOG.md
since="${1:-${SIMUBENCH_SINCE:-}}"
if [ -n "$since" ]; then
  python3 scripts/changelog.py --since "$since" -o dist/RELEASE_NOTES.md
fi
ls -l dist/
