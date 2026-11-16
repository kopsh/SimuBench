#!/usr/bin/env bash
# Build the portable Windows bundle (#1188): dist/SimuBench-<version>-win64.zip. Unzip, double-click
# SimuBench.exe: no Python, no install, no network on the user's PC. Runs on Linux (or any OS) after
# scripts/build-wheel.sh, from the wheel it left in dist/. Needs uv and the package index (internal
# mirror: UV_INDEX_URL / UV_DEFAULT_INDEX as for any uv install) for the Windows wheels, and the
# embeddable CPython zip: python.org by default, or SIMUBENCH_PYTHON_EMBED=<URL or local file>.
set -euo pipefail
cd "$(dirname "$0")/.."
shopt -s nullglob
wheels=(dist/simubench-*.whl)
if [ "${#wheels[@]}" -ne 1 ]; then
  echo "error: run scripts/build-wheel.sh first (expected one dist/simubench-*.whl, found ${#wheels[@]})" >&2
  exit 1
fi
uv run --no-project --with distlib python scripts/windows_bundle.py "${wheels[0]}" "$@"
