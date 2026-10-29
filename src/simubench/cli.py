"""Command line: ``simubench serve | mcp | validate | schemas | version``."""

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
        hislip_port=None if args.no_scpi else args.hislip_port,
        record_dir=Path(args.record_dir).resolve() if args.record_dir else None,
        record_raw=getattr(args, "record_raw", False),
    )
    from .ops import setup_logging

    setup_logging(args.log_level, args.log_format, args.log_file)
    token = _token(args)
    app = create_app(manager, token=token)

    @app.on_event("startup")
    async def _startup() -> None:
        if bench:
            served = await manager.start(bench.relative_to(workspace).as_posix())
            for lst in served.scpi.listeners if served.scpi else []:
                print(f"  SCPI  {lst.instrument:<12} {lst.address}")
            for name, addr in served.hislip.addresses().items() if served.hislip else []:
                print(f"  HiSLIP {name:<12} {addr}")
        url = _ui_url(args.port, token)
        print(f"SimuBench {__version__}  workspace: {workspace}")
        print(f"  界面 / UI: {url}")
        if args.open:
            webbrowser.open(url)

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level=args.log_level, log_config=None)
    return 0


def _token(args: argparse.Namespace) -> str | None:
    """The access token is opt-in (D43): ``--token VALUE`` or ``--require-token`` (random)."""
    if args.token:
        return str(args.token)
    return secrets.token_urlsafe(24) if args.require_token else None


def _ui_url(port: int, token: str | None) -> str:
    return f"http://127.0.0.1:{port}/" + (f"?token={token}" if token else "")


def _free_port(port: int) -> int:
    import socket

    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _mcp(args: argparse.Namespace) -> int:
    """stdio MCP service; the Web Bench runs in the same process so the engineer sees the agent's work.

    stdout carries the MCP protocol: everything else goes to stderr.
    """

    try:
        from .mcp_server import build_server
    except ImportError:
        print("MCP 需要可选依赖：pip install 'simubench[mcp]'", file=sys.stderr)
        return 2
    import uvicorn

    from .ops import setup_logging
    from .server.app import create_app
    from .server.manager import SessionManager

    setup_logging(args.log_level, args.log_format, args.log_file)
    workspace = Path(args.workspace).resolve()
    manager = SessionManager(
        workspace,
        scpi_host="127.0.0.1",
        scpi_base_port=None if args.no_scpi else args.scpi_base_port,
        hislip_port=None if args.no_scpi else args.hislip_port,
        record_dir=Path(args.record_dir).resolve() if args.record_dir else None,
        record_raw=getattr(args, "record_raw", False),
    )
    token = _token(args)
    ui_url = None
    server = None
    if not args.no_ui:
        port = _free_port(args.port)
        ui_url = _ui_url(port, token)
        config = uvicorn.Config(
            create_app(manager, token=token),
            host="127.0.0.1",
            port=port,
            log_config=None,
            access_log=False,
            log_level=args.log_level,
        )
        server = uvicorn.Server(config)
        print(f"SimuBench {__version__} MCP  workspace: {workspace}", file=sys.stderr)
        print(f"  界面 / UI: {ui_url}", file=sys.stderr)
    mcp = build_server(manager, allow_control=args.allow_control, ui_url=ui_url)

    async def run() -> None:
        ui = asyncio.create_task(server.serve()) if server else None
        try:
            await mcp.run_stdio_async()
        finally:
            for sid in list(manager.sessions):
                await manager.stop(sid)
            if server and ui:
                server.should_exit = True
                await ui

    asyncio.run(run())
    return 0


def _is_definition(path: Path, data: object) -> bool:
    """An instrument definition rather than a bench: by its schema line, else by its content."""
    schema = data.get("schema") if isinstance(data, dict) else None
    if isinstance(schema, str):
        return schema.startswith("simubench.instrument/")
    return ".bench." not in path.name and isinstance(data, dict) and "instruments" not in data


def _validate(args: argparse.Namespace) -> int:
    """Print diagnostics as ``file:pointer: CODE message (suggestion)``; exit 1 on any error."""
    from .engine.validate import validate_data
    from .engine.validate_definition import validate_definition_data
    from .library.loader import DefinitionLibrary, _read_yaml

    status = 0
    for path in args.files:
        p = Path(path)
        try:
            data = json.loads(p.read_text(encoding="utf-8")) if p.suffix == ".json" else _read_yaml(p)
        except Exception as exc:
            print(f"{path}: E-READ {exc}", file=sys.stderr)
            status = 1
            continue
        if _is_definition(p, data):
            _, report = validate_definition_data(data)
        else:
            _, report = validate_data(data, DefinitionLibrary([p.resolve().parent / "instruments"]))
        for d in report.diagnostics:
            hint = f" ({d.suggestion})" if d.suggestion else ""
            print(f"{path}:{d.pointer}: {d.code} {d.message}{hint}", file=sys.stderr)
        if not report.ok:
            status = 1
    return status


def _doctor(args: argparse.Namespace) -> int:
    """Health checks; exit 1 when one fails (warnings do not fail)."""
    from dataclasses import asdict

    from .ops import doctor

    workspace = Path(args.workspace).resolve() if args.workspace else None
    checks = doctor(workspace, port=args.port, scpi_base_port=args.scpi_base_port)
    if args.json:
        print(json.dumps([asdict(c) for c in checks], ensure_ascii=False, indent=2))
    else:
        mark = {"ok": "✓", "warn": "!", "fail": "✗"}
        for c in checks:
            print(f"{mark[c.status]} {c.name:<24} {c.detail}")
    return 1 if any(c.status == "fail" for c in checks) else 0


def _diagnose(args: argparse.Namespace) -> int:
    """Write a diagnostics bundle (zip) for a bug report; no access token is included."""
    from .ops import bundle

    workspace = Path(args.workspace).resolve() if args.workspace else None
    record_dir = Path(args.record_dir).resolve() if args.record_dir else None
    out = Path(args.out)
    out.write_bytes(bundle(None, workspace, record_dir=record_dir, runs=args.runs))
    print(out)
    return 0


def _version(args: argparse.Namespace) -> int:
    if args.full:
        from .ops import manifest

        workspace = Path(args.workspace).resolve() if args.workspace else None
        print(json.dumps(manifest(workspace), ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"simubench": __version__}))
    return 0


def _migrate(args: argparse.Namespace) -> int:
    """Report (and with --write, apply) format upgrades; exit 1 when a file cannot be read."""
    import yaml

    from .library.loader import _read_yaml
    from .library.migrate import FormatError, upgrade

    status = 0
    for name in args.files:
        path = Path(name)
        fmt = "bench" if ".bench." in path.name else "instrument"
        try:
            raw = json.loads(path.read_text(encoding="utf-8")) if path.suffix == ".json" else _read_yaml(path)
            data, steps = upgrade(raw, fmt)
        except (FormatError, ValueError, OSError) as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            status = 1
            continue
        if not steps:
            print(f"{name}: current")
            continue
        print(f"{name}: {', '.join(steps)}{'' if args.write else ' (dry run; --write to apply)'}")
        if args.write:
            if path.suffix == ".json":
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            else:
                path.write_text(
                    yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8"
                )
    return status


def _assets(args: argparse.Namespace) -> int:
    """Print a bench's calibration register (CSV or JSON) for a test script's pre-run check."""
    import csv

    from .engine.calibration import register_view
    from .library.loader import load_bench

    bench = load_bench(args.bench)
    rows = register_view(bench.assets, bench.run.start.date())
    if args.format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    fields = ["serial", "model", "status", "cal_date", "cal_due", "certificate", "notes"]
    writer = csv.DictWriter(sys.stdout, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: "" if r.get(k) is None else r[k] for k in fields})
    return 0


def _replay(args: argparse.Namespace) -> int:
    """Replay a run record and compare every reply; exit 1 when one differs (D36)."""
    from .engine.replay import replay

    try:
        report = replay(args.record, definition_dirs=args.definitions or None, stop_at_first=args.first)
    except ValueError as exc:
        print(f"{args.record}: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        for w in report.warnings:
            print(f"warning: {w}", file=sys.stderr)
        for m in report.mismatches:
            print(f"{args.record}:{m.seq}: {m.instrument} at t = {m.t_ns / 1e9:.9f} s", file=sys.stderr)
            if m.command:
                print(f"  after:    {m.command}", file=sys.stderr)
            print(f"  recorded: {m.expected}", file=sys.stderr)
            print(f"  replayed: {m.actual}", file=sys.stderr)
        verdict = "identical" if report.ok else f"{len(report.mismatches)} replies differ"
        print(f"{report.bench}: {report.actions} actions, {report.replies} replies, {verdict}")
    return 0 if report.ok else 1


def _coverage(args: argparse.Namespace) -> int:
    """A real model's coverage table (D42): every command of the manual and how far SimuBench goes."""
    from .library.coverage import LEVEL_TEXT, coverage_path, load_coverage
    from .library.loader import DefinitionLibrary

    lib = DefinitionLibrary([Path(d) for d in args.definitions] + [Path.cwd() / "instruments"])
    try:
        path = lib.path(args.id)
    except KeyError as exc:
        print(exc.args[0], file=sys.stderr)
        return 2
    definition = lib.get(args.id)
    cov_file = coverage_path(path)
    if not cov_file.is_file():
        print(f"{args.id}: no coverage table ({cov_file.name})", file=sys.stderr)
        return 2
    cov = load_coverage(cov_file)
    ref = definition.reference
    print(f"{definition.manufacturer} {definition.model} ({definition.id})")
    if ref:
        print(
            "  "
            + " · ".join(
                x for x in (ref.title, ref.document, ref.version, ref.firmware and f"FW {ref.firmware}") if x
            )
        )
    for section in cov.sections:
        print(f"\n{section.section}")
        for c in section.commands:
            note = f"  {c.note}" if c.note else ""
            print(f"  {LEVEL_TEXT[c.level]:<4} {c.header}{note}")
    st = cov.stats()
    total = sum(st.values())
    print(f"\n{' · '.join(f'{LEVEL_TEXT[k]} {v}' for k, v in st.items())} · 共 {total}")
    return 0


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
    p.add_argument(
        "--hislip-port",
        type=int,
        default=4880,
        help="shared HiSLIP port (D41); 0 lets the OS choose, and --no-scpi disables it too",
    )
    p.add_argument("--no-scpi", action="store_true", help="do not open SCPI sockets")
    p.add_argument("--record-dir", help="write run records here")
    p.add_argument("--record-raw", action="store_true", help="also keep waveform blocks byte for byte")
    p.add_argument("--token", help="require this access token (default: no token, D43)")
    p.add_argument(
        "--require-token",
        action="store_true",
        help="require a random access token; use on a computer shared with other users",
    )
    p.add_argument("--open", action="store_true", help="open the browser")
    p.add_argument("--log-level", default="warning")
    p.add_argument("--log-format", choices=["text", "json"], default="text", help="json: one object per line")
    p.add_argument("--log-file", help="also write the log here")
    p.set_defaults(fn=_serve)

    p = sub.add_parser("mcp", help="MCP service on stdio for AI agents, with the Web Bench alongside")
    p.add_argument("--workspace", default=".", help="folder with bench files (default: current folder)")
    p.add_argument("--port", type=int, default=8765, help="Web Bench port (next free one if taken)")
    p.add_argument("--no-ui", action="store_true", help="do not serve the Web Bench")
    p.add_argument("--scpi-base-port", type=int, default=5025)
    p.add_argument("--hislip-port", type=int, default=4880)
    p.add_argument("--no-scpi", action="store_true")
    p.add_argument("--record-dir")
    p.add_argument("--token", help="require this access token (default: no token, D43)")
    p.add_argument("--require-token", action="store_true", help="require a random access token")
    p.add_argument(
        "--allow-control",
        action="store_true",
        help="enable simulation control tools (advance the clock, read true values)",
    )
    p.add_argument("--log-level", default="warning")
    p.add_argument("--log-format", choices=["text", "json"], default="text")
    p.add_argument("--log-file")
    p.set_defaults(fn=_mcp)

    p = sub.add_parser("validate", help="check bench and instrument definition files; exit 1 on any error")
    p.add_argument("files", nargs="+", metavar="file")
    p.set_defaults(fn=_validate)

    p = sub.add_parser("migrate", help="upgrade bench and definition files to the current format")
    p.add_argument("files", nargs="+")
    p.add_argument("--write", action="store_true", help="rewrite the files (default: report only)")
    p.set_defaults(fn=_migrate)

    p = sub.add_parser("assets", help="print a bench's calibration register (CSV or JSON)")
    p.add_argument("bench")
    p.add_argument("--format", choices=["csv", "json"], default="csv")
    p.set_defaults(fn=_assets)

    p = sub.add_parser("replay", help="replay a run record and compare every reply (exit 1 on a difference)")
    p.add_argument("record", help="run-*.jsonl written with --record-dir")
    p.add_argument("--definitions", action="append", help="extra instrument definition folder (repeatable)")
    p.add_argument("--first", action="store_true", help="stop at the first difference")
    p.add_argument("--json", action="store_true", help="print the report as JSON")
    p.set_defaults(fn=_replay)

    p = sub.add_parser(
        "coverage", help="a real model's coverage table: commands of the manual by level (D42)"
    )
    p.add_argument("id", help="definition id, e.g. keysight-34465a")
    p.add_argument("--definitions", action="append", default=[], help="also look for definitions here")
    p.set_defaults(fn=_coverage)

    p = sub.add_parser("schemas", help="export OpenAPI and JSON Schemas (D21)")
    p.add_argument("--out", default="schemas")
    p.set_defaults(fn=_schemas)

    p = sub.add_parser("version", help="print the version (--full: the whole version manifest)")
    p.add_argument(
        "--full", action="store_true", help="dependencies, definitions and their hashes, web UI build"
    )
    p.add_argument("--workspace", help="include this workspace's own definitions")
    p.set_defaults(fn=_version)

    p = sub.add_parser("doctor", help="check that SimuBench can run here (exit 1 on a failure)")
    p.add_argument("--workspace", help="also validate the bench files in this folder")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--scpi-base-port", type=int, default=5025)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_doctor)

    p = sub.add_parser("diagnose", help="write a diagnostics bundle (zip) for a bug report")
    p.add_argument("--out", default="simubench-diagnostics.zip")
    p.add_argument("--workspace")
    p.add_argument("--record-dir", help="include the latest run records from here")
    p.add_argument("--runs", type=int, default=3, help="how many run records to include")
    p.set_defaults(fn=_diagnose)

    args = parser.parse_args(argv)
    result = args.fn(args)
    return int(result or 0)


if __name__ == "__main__":
    sys.exit(main())

__all__ = ["asyncio", "main"]
