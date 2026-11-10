#!/usr/bin/env python
"""The type check of ``src`` against a baseline that can only shrink (#897).

``ty check src`` (Astral's checker, seconds not minutes) reports the diagnostics it finds; a
diagnostic that the baseline does not cover fails, the ones in it do not block — a batch of fixes
deletes its lines from the baseline, so it only ever gets shorter. A baseline line is
``path:level[rule] message`` without line/column (those drift with every edit) and a count, since
the same message on many lines collapses into one key: more occurrences than the baseline covers
is a new diagnostic too.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "scripts" / "ty_baseline.txt"

LINE = re.compile(r"^(src/[^:]+):\d+:\d+: (?:error|warning)\[([a-z-]+)\] (.*)$")

#: ty's contract: 0 = no diagnostics, 1 = diagnostics found, anything else = the tool itself failed.
TY_OK_CODES = (0, 1)


class TyBroken(RuntimeError):
    """ty did not run or did not report in the expected format (#916): the gate must fail, not
    silently pass on zero parsed diagnostics."""


def current() -> Counter[str]:
    """The diagnostics ty reports for ``src``, as ``path:rule message -> count`` keys."""
    ty = shutil.which("ty")
    cmd = (
        ["ty", "check", "src", "--output-format=concise"]
        if ty
        else [
            "uv",
            "run",
            "ty",
            "check",
            "src",
            "--output-format=concise",
        ]
    )
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    except OSError as exc:  # ty missing and uv not on PATH either: a broken environment
        raise TyBroken(f"cannot run {cmd[0]!r}: {exc}") from None
    # ty exits non-zero when it finds diagnostics: that is not our failure, read the report.
    # Everything else — a crash, a bad config, an output-format change — must fail the gate
    # instead of parsing to zero diagnostics and passing (#916).
    if proc.returncode not in TY_OK_CODES:
        raise TyBroken(f"ty exited {proc.returncode}:\n{proc.stderr.strip() or proc.stdout.strip()}")
    keys: Counter[str] = Counter()
    for line in proc.stdout.splitlines():
        if m := LINE.match(line):
            path, rule, message = m.groups()
            keys[f"{path}:{rule} {message.strip()}"] += 1
    if proc.returncode == 1 and not keys:
        raise TyBroken(
            "ty found diagnostics (exit 1) but none matched the expected"
            " '<path>:<line>:<col>: <level>[<rule>] <message>' lines — the output format or the"
            f" parse below changed:\n{(proc.stdout or proc.stderr).strip()[:2000]}"
        )
    return keys


def main() -> int:
    try:
        return _main()
    except TyBroken as exc:
        print(f"the type check itself failed (#916): {exc}")
        return 1


def _main() -> int:
    if "--rebuild" in sys.argv:  # a deliberate baseline refresh, reviewed like any diff
        now = current()
        BASELINE.write_text(
            "\n".join(f"{key} ×{count}" if count > 1 else key for key, count in sorted(now.items())) + "\n",
            encoding="utf-8",
        )
        print(f"{BASELINE.name} rebuilt: {sum(now.values())} diagnostics in {len(now)} keys")
        return 0
    baseline: Counter[str] = Counter()
    for line in BASELINE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.endswith("×1") or (m := re.search(r" ×(\d+)$", line)):
            count = int(m.group(1)) if m else 1
            line = line[: m.start()] if m else line[:-2].strip()
        else:
            count = 1
        baseline[line] = count
    now = current()
    new = sorted(key for key, count in now.items() if count > baseline[key])
    for key in new:
        print(f"NEW ({now[key]} × baseline {baseline[key]}): {key}")
    if new:
        print(
            f"\n{len(new)} diagnostics are not covered by {BASELINE.name}: fix them — adding"
            " baseline coverage is for a deliberate type-level API change only, in review (D-#897)"
        )
        return 1
    stale = sorted(
        (key, baseline[key] - now.get(key, 0)) for key in baseline if baseline[key] > now.get(key, 0)
    )
    if stale:
        print(f"{len(stale)} baseline keys are less frequent now — shorten {BASELINE.name}:")
        for key, delta in stale:
            print(f"  - {key} ×{delta}")
    print(f"ty: {sum(now.values())} diagnostics in {len(now)} keys, none new")
    return 0


if __name__ == "__main__":
    sys.exit(main())
