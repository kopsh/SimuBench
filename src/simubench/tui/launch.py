"""The ``simubench tui`` entry: start (or attach to) a service, open a session, run the app.

Bench mode mirrors ``serve``: the service runs in a background thread on 127.0.0.1 with an
auto-assigned port, the SCPI address table goes to stderr, and quitting stops the session and
the service, printing ``已关闭会话`` after the terminal is restored (tui-screens.md 3).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from .app import SimuBenchApp
from .client import CLIENT_HEADER
from .i18n import default_lang, t

MISSING_TEXTUAL = "终端控制台需要可选依赖：pip install 'simubench[tui]'"


def has_textual() -> bool:
    import importlib.util

    return importlib.util.find_spec("textual") is not None


def resolve_token(token: str | None) -> str | None:
    """``--token`` wins, else ``SIMUBENCH_TOKEN`` (tui-screens.md 3)."""
    return token or os.environ.get("SIMUBENCH_TOKEN") or None


def _serve_in_thread(app: Any, port: int, log_level: str = "warning") -> tuple[Any, threading.Thread]:
    """Run uvicorn in a daemon thread; returns (server, thread)."""
    import uvicorn

    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_config=None, access_log=False, log_level=log_level
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="simubench-tui-service", daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline and thread.is_alive():
        time.sleep(0.02)
    return server, thread


def _print_addresses(info: dict[str, Any]) -> None:
    """The SCPI address table, the same lines ``serve`` prints (to stderr here)."""
    for inst in info.get("instruments", []):
        if inst.get("scpi_port"):
            print(f"  SCPI  {inst['name']:<12} {inst['address_virtual']}", file=sys.stderr)
        if inst.get("address_hislip"):
            print(f"  HiSLIP {inst['name']:<12} {inst['address_hislip']}", file=sys.stderr)


def _headers(token: str | None) -> dict[str, str]:
    headers = dict(CLIENT_HEADER)
    if token:
        headers["x-simubench-token"] = token
    return headers


def _open_session(url: str, bench_path: str, clock: str | None, seed: int | None, token: str | None) -> dict:
    body: dict[str, Any] = {"bench_path": bench_path}
    if clock:
        body["clock"] = clock
    if seed is not None:
        body["seed"] = seed
    r = httpx.post(f"{url}/api/sessions", json=body, headers=_headers(token), timeout=120)
    r.raise_for_status()
    return r.json()  # type: ignore[no-any-return]


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run_bench(args: Any) -> int:
    """``simubench tui <bench.yaml> …``: serve in-process, open a session, run the TUI."""
    from .server.app import create_app
    from .server.manager import SessionManager

    bench = Path(args.bench).resolve()
    workspace = Path(args.workspace).resolve() if args.workspace else bench.parent
    token = resolve_token(args.token)
    manager = SessionManager(
        workspace,
        scpi_host="127.0.0.1",
        scpi_base_port=None if args.no_scpi else args.scpi_base_port,
        hislip_port=None if args.no_scpi else args.hislip_port,
        record_dir=Path(args.record_dir).resolve() if args.record_dir else None,
        record_raw=getattr(args, "record_raw", False),
        allow_plugins=args.allow_plugins,
    )
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    server, thread = _serve_in_thread(create_app(manager, token=token), port)
    rel = bench.relative_to(workspace).as_posix() if bench.is_relative_to(workspace) else str(bench)

    try:
        served = asyncio.run(manager.start(rel, clock=args.clock, seed=args.seed))
        info = served.describe().model_dump(by_alias=True)
        _print_addresses(info)
        info = _open_session(url, rel, args.clock, args.seed, token)
    except Exception as exc:
        print(f"{exc}", file=sys.stderr)
        server.should_exit = True
        thread.join(timeout=10)
        return 2

    app = SimuBenchApp(
        url,
        sid=info["id"],
        token=token,
        lang=args.lang or default_lang(),
        launched=True,
        bench_path=bench,
        bench_name=info.get("bench_name", bench.stem),
    )
    try:
        app.run()
    finally:
        # The terminal is restored; now stop the session and the service.
        with contextlib.suppress(Exception):
            httpx.delete(
                f"{url}/api/sessions/{info['id']}",
                params={"force": "true"},
                headers=_headers(token),
                timeout=10,
            )
        server.should_exit = True
        thread.join(timeout=10)
        print(t("closed"), file=sys.stderr)
    return 0


def run_attach(url: str, session: str | None, token: str | None, lang: str | None) -> int:
    """``simubench tui --attach [URL] [--session SID]``: connect to a running service."""
    app = SimuBenchApp(url.rstrip("/"), sid=session, token=resolve_token(token), lang=lang or default_lang())
    app.run()
    return 0
