"""Command line: ``simubench serve | validate | schemas | version``."""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
import webbrowser
from pathlib import Path

from . import __version__


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .server.app import create_app
    from .server.manager import SessionManager

    bench = Path(args.bench).resolve() if args.bench else None
    workspace = Path(args.workspace).resolve() if args.workspace else (bench.parent if bench else Path.cwd())
    manager = SessionManager(
        workspace,
        scpi_host="127.0.0.1",
        scpi_base_port=None if args.no_scpi else args.scpi_base_port,
        record_dir=Path(args.record_dir).resolve() if args.record_dir else None,
    )
    token = args.token or secrets.token_urlsafe(24)
    app = create_app(manager, token=token)

    @app.on_event("startup")
    async def _startup() -> None:
        if bench:
            served = await manager.start(bench.relative_to(workspace).as_posix())
            for lst in served.scpi.listeners if served.scpi else []:
                print(f"  SCPI  {lst.instrument:<12} {lst.address}")
        url = f"http://127.0.0.1:{args.port}/?token={token}"
        print(f"SimuBench {__version__}  workspace: {workspace}")
        print(f"  界面 / UI: {url}")
        if args.open:
            webbrowser.open(url)

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level=args.log_level)
    return 0


def _validate(args: argparse.Namespace) -> int:
    """Print diagnostics as ``file:pointer: CODE message (suggestion)``; exit 1 on any error."""
    from .engine.validate import validate_data
    from .library.loader import DefinitionLibrary, _read_yaml

    status = 0
    for path in args.benches:
        p = Path(path)
        try:
            data = json.loads(p.read_text(encoding="utf-8")) if p.suffix == ".json" else _read_yaml(p)
        except Exception as exc:
            print(f"{path}: E-READ {exc}", file=sys.stderr)
            status = 1
            continue
        library = DefinitionLibrary([p.resolve().parent / "instruments"])
        _, report = validate_data(data, library)
        for d in report.diagnostics:
            hint = f" ({d.suggestion})" if d.suggestion else ""
            print(f"{path}:{d.pointer}: {d.code} {d.message}{hint}", file=sys.stderr)
        if not report.ok:
            status = 1
    return status


def _schemas(args: argparse.Namespace) -> int:
    from .server.schemas import export_all

    out = Path(args.out)
    written = export_all(out)
    for p in written:
        print(p)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="simubench", description="SimuBench virtual instrument benches")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="start the local service (Web Bench, SCPI sockets)")
    p.add_argument("bench", nargs="?", help="bench file to start right away")
    p.add_argument("--workspace", help="folder searched for bench files (default: the bench's folder)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--scpi-base-port", type=int, default=5025, help="first SCPI port; 0 lets the OS choose")
    p.add_argument("--no-scpi", action="store_true", help="do not open SCPI sockets")
    p.add_argument("--record-dir", help="write run records here")
    p.add_argument("--token", help="access token (default: random)")
    p.add_argument("--open", action="store_true", help="open the browser")
    p.add_argument("--log-level", default="warning")
    p.set_defaults(fn=_serve)

    p = sub.add_parser("validate", help="check bench files; exit 1 on the first problem")
    p.add_argument("benches", nargs="+")
    p.set_defaults(fn=_validate)

    p = sub.add_parser("schemas", help="export OpenAPI and JSON Schemas (D21)")
    p.add_argument("--out", default="schemas")
    p.set_defaults(fn=_schemas)

    p = sub.add_parser("version", help="print the version")
    p.set_defaults(fn=lambda a: print(json.dumps({"simubench": __version__})) or 0)

    args = parser.parse_args(argv)
    result = args.fn(args)
    return int(result or 0)


if __name__ == "__main__":
    sys.exit(main())

__all__ = ["asyncio", "main"]
