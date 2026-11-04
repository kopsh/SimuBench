#!/usr/bin/env bash
# What changed for the user between two versions (D58): the product commits, newest version first.
#   scripts/changelog.sh                       # everything, by version
#   scripts/changelog.sh 2026.11.4.3           # since that version
#   scripts/changelog.sh 2026.11.4.3 2026.11.6.2
# Needs the git history with its v* tags (git fetch --tags). Rules: cliff.toml.
set -euo pipefail
cd "$(dirname "$0")/.."
from=${1:-}
to=${2:-HEAD}
[[ -n "$from" && "$from" != v* ]] && from="v$from"
[[ "$to" != HEAD && "$to" != v* ]] && to="v$to"
range=()
[[ -n "$from" ]] && range=("$from..$to")
[[ -z "$from" && "$to" != HEAD ]] && range=("$to")
exec uvx -q --from git-cliff==2.14.2 git-cliff -c cliff.toml "${range[@]}" 2>/dev/null
