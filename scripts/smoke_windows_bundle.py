"""Smoke-test an unzipped Windows bundle (#1188): stdlib only, so any Python on the test PC runs it.

    python scripts/smoke_windows_bundle.py <unzipped SimuBench-<version> folder>

Checks: ``tools\\simubench.exe doctor`` passes; a double-click start (``SimuBench.exe --no-browser``)
serves the Web Bench page and MCP on one port (initialize, then the tool list); a second start finds
the first one and exits. ``--launch``/``--cli`` replace the two executables with other commands (a
checkout on Linux: ``--launch "uv run simubench launch" --cli "uv run simubench"``).
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"


def _get(path: str) -> str:
    with urllib.request.urlopen(BASE + path, timeout=5) as r:
        return r.read().decode("utf-8", "replace")


def _mcp(body: dict, session: str | None) -> tuple[str, str | None]:
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if session:
        headers["mcp-session-id"] = session
    req = urllib.request.Request(BASE + "/mcp", data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read().decode("utf-8", "replace"), r.headers.get("mcp-session-id")


def _check(ok: bool, what: str) -> None:
    print(("ok   " if ok else "FAIL ") + what, flush=True)
    if not ok:
        raise SystemExit(1)


def _utf8_output() -> None:
    """Write UTF-8 whatever the console's code page: the doctor's ✓ and Chinese text, echoed here,
    do not fit cp1252 (a Windows runner's default, #1210) — the same choice the CLI makes (#1205)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _utf8_output()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("bundle", type=Path, nargs="?")
    p.add_argument("--launch", help="command instead of <bundle>/SimuBench.exe")
    p.add_argument("--cli", help="command instead of <bundle>/tools/simubench.exe")
    p.add_argument("--ui", action="store_true", help="also require the Web Bench page (a built UI)")
    args = p.parse_args(argv)
    launch = shlex.split(args.launch) if args.launch else [str(args.bundle / "SimuBench.exe")]
    cli = shlex.split(args.cli) if args.cli else [str(args.bundle / "tools" / "simubench.exe")]

    doctor = subprocess.run(
        [*cli, "doctor"], capture_output=True, encoding="utf-8", errors="replace", timeout=300
    )
    print(doctor.stdout + doctor.stderr)
    _check(doctor.returncode == 0, "simubench doctor")

    workspace = Path(tempfile.mkdtemp(prefix="simubench-smoke-"))
    log = open(workspace / "launch.log", "w+", encoding="utf-8", errors="replace")  # noqa: SIM115
    proc = subprocess.Popen([*launch, "--no-browser", "--workspace", str(workspace)], stdout=log, stderr=log)
    try:
        deadline = time.monotonic() + 120
        health = ""
        while time.monotonic() < deadline and proc.poll() is None:
            try:
                health = _get("/api/health")
                break
            except OSError:
                time.sleep(1)
        _check('"version"' in health, f"started, /api/health answers: {health.strip()}")
        if args.ui:
            _check("<html" in _get("/").lower(), "Web Bench page served at /")
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "smoke", "version": "0"},
            },
        }
        text, session = _mcp(init, None)
        _check('"simubench"' in text, "MCP initialize at /mcp")
        _mcp({"jsonrpc": "2.0", "method": "notifications/initialized"}, session)
        text, _ = _mcp({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, session)
        _check('"list_benches"' in text, "MCP tools/list")
        again = subprocess.run(
            [*launch, "--no-browser"], capture_output=True, encoding="utf-8", errors="replace", timeout=120
        )
        _check(
            again.returncode == 0 and "already running" in again.stdout, "a second start opens the first one"
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.seek(0)
        print("--- launch log ---\n" + log.read())
        log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
