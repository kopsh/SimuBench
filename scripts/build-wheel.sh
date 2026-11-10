#!/usr/bin/env bash
# Build an installable wheel with the web UI inside: pip install dist/simubench-*.whl, then
# `simubench serve` works without Node or a checkout. Beside it, dist/CHANGELOG.md: what changed
# for the user, by build (D58); the same file is in the wheel (`simubench changelog`). It needs the
# full git history (no tags: the version is computed from it); without it the wheel is built without
# a changelog. Release notes of this build: pass the version published before it (argument or
# SIMUBENCH_SINCE) and dist/RELEASE_NOTES.md lists only what is new since then, to post as is.
# dist/BUILT_VERSION lists the versions built on this machine, one per line, each build appending
# its own. The changelog has one section per build (the commits since the build before it), and
# with no argument the release notes start after the last build (dist/ is local: another machine
# or a cleaned dist/ loses the list, and the earlier builds then merge into one section).
set -euo pipefail
cd "$(dirname "$0")/.."
# The version counts the day's commits (scripts/version.py): a shallow clone would build N 0, lower
# than the last release, which pip then treats as old (#828).
if [ "$(git rev-parse --is-shallow-repository 2>/dev/null || true)" = true ]; then
  echo "error: shallow clone: the version and the changelog need the full history (git fetch --unshallow)" >&2
  exit 1
fi
cur="$(python3 scripts/version.py)"
builds=""
[ -f dist/BUILT_VERSION ] && builds="$(tr -s '[:space:]' '\n' < dist/BUILT_VERSION | grep . || true)"
prev="$(printf '%s\n' "$builds" | grep -vxF "$cur" | grep . | tail -1 || true)"
(cd web && pnpm install --frozen-lockfile && pnpm build)
rm -rf src/simubench/web_dist src/simubench/CHANGELOG.md
cp -r web/dist src/simubench/web_dist
trap 'rm -rf src/simubench/web_dist src/simubench/CHANGELOG.md' EXIT
python3 scripts/changelog.py --builds <(printf '%s\n' "$builds") -o src/simubench/CHANGELOG.md ||
  echo "warning: no changelog in this build (see above)" >&2
rm -rf dist
uv build --wheel
[ -f src/simubench/CHANGELOG.md ] && cp src/simubench/CHANGELOG.md dist/CHANGELOG.md
printf '%s\n' "$builds" "$cur" | grep . | awk '!seen[$0]++' > dist/BUILT_VERSION
since="${1:-${SIMUBENCH_SINCE:-$prev}}"
if [ -n "$since" ]; then
  python3 scripts/changelog.py --since "$since" -o dist/RELEASE_NOTES.md
fi
ls -l dist/
