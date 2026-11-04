#!/usr/bin/env bash
# Build an installable wheel with the web UI inside: pip install dist/simubench-*.whl, then
# `simubench serve` works without Node or a checkout. Beside it, dist/CHANGELOG.md: what changed
# for the user, by version (D58); the same file is in the wheel (`simubench changelog`). It needs
# the git history with its v* tags; without them the wheel is built without a changelog.
set -euo pipefail
cd "$(dirname "$0")/.."
(cd web && pnpm install --frozen-lockfile && pnpm build)
rm -rf src/simubench/web_dist src/simubench/CHANGELOG.md
cp -r web/dist src/simubench/web_dist
trap 'rm -rf src/simubench/web_dist src/simubench/CHANGELOG.md' EXIT
# The version this build gets (hatch-vcs reads the same tags); names the not-yet-tagged commits.
version=$(uvx -q --from setuptools-scm python -m setuptools_scm 2>/dev/null | sed 's/+.*//' || true)
python3 scripts/changelog.py ${version:+--version "$version"} -o src/simubench/CHANGELOG.md ||
  echo "warning: no changelog in this build (see above)" >&2
rm -rf dist
uv build --wheel
[ -f src/simubench/CHANGELOG.md ] && cp src/simubench/CHANGELOG.md dist/CHANGELOG.md
ls -l dist/
