"""Command line: ``simubench serve | mcp | validate | verdicts | replay | ...``."""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
import threading
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import __version__
from .service import (
    DEFAULT_HISLIP_PORT,
    DEFAULT_SCPI_BASE_PORT,
    add_service_options,
    address_line,
    build_manager,
    link_line,
)

if TYPE_CHECKING:
    from .library.loader import DefinitionLibrary
    from .server.manager import SessionManager


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .server.app import create_app
    from .server.catalog import components as component_catalog

    bench = Path(args.bench).resolve() if args.bench else None
    workspace = Path(args.workspace).resolve() if args.workspace else (bench.parent if bench else Path.cwd())
    manager = build_manager(args, workspace)
    manager.web_port = args.port  # the assistant's LLM address may not point back at it (#872)
    from .ops import setup_logging

    setup_logging(args.log_level, args.log_format, args.log_file)
    token = _token(args)
    url = _ui_url(args.port, token)
    mcp = None
    if args.mcp:
        try:
            from .mcp_server import build_server
        except ImportError:
            print("MCP 需要可选依赖：pip install 'simubench[mcp]'", file=sys.stderr)
            return 2
        mcp = build_server(manager, allow_control=args.allow_control, ui_url=url)
    app = create_app(manager, token=token, mcp=mcp)

    @app.on_event("startup")
    async def _startup() -> None:
        # The component catalog (/api/components) takes a second or more to build the first time:
        # build it now, beside the startup, so the first build area opened does not wait (#1231).
        threading.Thread(target=component_catalog, name="component-catalog", daemon=True).start()
        if bench:
            served = await manager.start(bench.relative_to(workspace).as_posix())
            for lst in served.scpi.listeners if served.scpi else []:
                print(address_line("SCPI", lst.instrument, lst.address))
            for name, addr in served.hislip.addresses().items() if served.hislip else []:
                print(address_line("HiSLIP", name, addr))
            for name, addr in served.vxi11.addresses().items() if served.vxi11 else []:
                print(address_line("VXI-11", name, addr))
            if served.link:
                print(link_line(served.link.address()))
        print(f"SimuBench {__version__}  workspace: {workspace}")
        print(f"  界面 / UI: {url}")
        if mcp is not None:
            print(f"  MCP (streamable HTTP): {_mcp_url(args.port)}")
            if getattr(args, "mcp_config", False):  # launch: the client config, once (#1193)
                config = {"mcpServers": {"simubench": {"type": "http", "url": _mcp_url(args.port)}}}
                print("  AI 助手（MCP 客户端）配置 / MCP client config: " + json.dumps(config))
        _print_plugins(manager)
        if args.open:
            webbrowser.open(url)

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level=args.log_level, log_config=None)
    return 0


DEFAULT_PORT = 8765


def _token(args: argparse.Namespace) -> str | None:
    """The access token is opt-in (D43): ``--token VALUE`` or ``--require-token`` (random)."""
    if args.token:
        return str(args.token)
    return secrets.token_urlsafe(24) if args.require_token else None


def _ui_url(port: int, token: str | None) -> str:
    return f"http://127.0.0.1:{port}/" + (f"?token={token}" if token else "")


def _mcp_url(port: int) -> str:
    from .server import MCP_PATH

    return f"http://127.0.0.1:{port}{MCP_PATH}"


def _running_simubench(port: int) -> dict[str, Any] | None:
    """What answers on this loopback port, when it is SimuBench (a second double-click, #1193):
    ``{"mcp_url": … or None, "token": whether its data routes want a token}``; ``None`` otherwise."""
    import urllib.error
    import urllib.request

    base = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(base + "/api/health", timeout=1) as r:
            health = json.loads(r.read())
        if not isinstance(health, dict) or "version" not in health:
            return None
        try:
            urllib.request.urlopen(base + "/api/benches", timeout=1).close()
            token = False
        except urllib.error.HTTPError as exc:
            token = exc.code == 401
        return {"mcp_url": health.get("mcp_url"), "token": token}
    except (OSError, ValueError):
        return None


def _already_running(port: int, running: dict[str, Any], *, browser: bool) -> int:
    """A second start: open the running service's page, or say why it is not the double-click one."""
    url = _ui_url(port, None)
    if running["token"]:
        print(f"SimuBench 已在运行，但它要求访问令牌 / already running, but it requires a token: {url}")
        print("  用启动它时打印的带令牌的地址打开 / open the address with the token it printed at start")
        return 1
    print(f"SimuBench 已在运行 / already running: {url}")
    if not running["mcp_url"]:
        print(
            "  它没有开启 MCP（serve 未带 --mcp），AI 助手连不上；关闭它后再启动，或用 --port 另起一个"
            " / it has no MCP (serve without --mcp): close it and start again, or use --port"
        )
    if browser:
        webbrowser.open(url)
    return 0


def default_launch_workspace() -> Path:
    """The double-click start's workspace: ``Documents/SimuBench`` in the user's home (#1188)."""
    home = Path.home()
    docs = home / "Documents"
    return (docs if docs.is_dir() else home) / "SimuBench"


def _launch(args: argparse.Namespace) -> int:
    """``SimuBench.exe`` of the Windows bundle (#1188): Web Bench and MCP in one service, browser open.

    A second start finds the first one on the port (the default, or ``--port``) and only opens its
    page; one that wants a token or has no MCP is named as such (#1193).
    """
    running = _running_simubench(args.port or DEFAULT_PORT)
    if running is not None:
        return _already_running(args.port or DEFAULT_PORT, running, browser=not args.no_browser)
    workspace = Path(args.workspace) if args.workspace else default_launch_workspace()
    workspace.mkdir(parents=True, exist_ok=True)
    port = _free_port(args.port or DEFAULT_PORT)
    # A port not given on the command line is the usual one, or a free one when that is taken: the
    # double-click must not fail on another program's 5025. A port given is kept, or refused (#1197).
    fallback = set()
    if args.scpi_base_port is None:
        args.scpi_base_port = DEFAULT_SCPI_BASE_PORT
        fallback.add("SCPI")
    if args.hislip_port is None:
        args.hislip_port = DEFAULT_HISLIP_PORT
        fallback.add("HiSLIP")
    print("SimuBench —— 关闭此窗口即停止服务 / close this window to stop the service")
    print(
        "  连接外部 AI：首页右上角「连接外部 AI（MCP）」/ Connect external AI (MCP): top right of the home page"
    )
    args.port_fallback, args.mcp_config = fallback, True
    args.workspace, args.port, args.bench = str(workspace), port, None
    args.mcp, args.allow_control, args.open = True, False, not args.no_browser
    return _serve(args)


def _print_plugins(manager: SessionManager, *, err: bool = False) -> None:
    """The startup banner names every loaded plugin and what waits for consent (issue 47)."""
    from .plugins import status_line

    line = status_line(manager.workspace, manager.plugins_allowed())
    if line:
        print(f"  plugins: {line}", file=sys.stderr if err else sys.stdout)


def _free_port(port: int) -> int:
    import socket

    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _tui(args: argparse.Namespace) -> int:
    """The terminal console (D18): without Textual installed it only names the install command."""
    from importlib.util import find_spec

    if find_spec("textual") is None:
        print(
            "The terminal console needs the optional dependency: pip install 'simubench[tui]'",
            file=sys.stderr,
        )
        return 2
    from .tui.launch import run_attach, run_bench

    if args.attach is not None or getattr(args, "session", None):
        return run_attach(
            args.attach if args.attach is not None else "http://127.0.0.1:8000",
            args.session,
            args.token,
        )
    return run_bench(args)


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

    setup_logging(args.log_level, args.log_format, args.log_file)
    workspace = Path(args.workspace).resolve()
    manager = build_manager(args, workspace)
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
    _print_plugins(manager, err=True)
    mcp = build_server(manager, allow_control=args.allow_control, ui_url=ui_url)

    async def run() -> None:
        ui = asyncio.create_task(server.serve()) if server else None
        try:
            await mcp.run_stdio_async()
        finally:
            for sid in list(manager.sessions):
                await manager.stop(sid, force=True)  # the service is going down; its ports close
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
    from .library.loader import DefinitionLibrary, _read_yaml, layered_definition_data

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
            try:
                data = layered_definition_data(data, p)  # D59: what the engine runs is what is checked
            except ValueError as exc:
                code, pointer = getattr(exc, "code", "E-DEF-EXTENDS"), getattr(exc, "pointer", "/extends")
                print(f"{path}:{pointer}: {code} {exc}", file=sys.stderr)
                status = 1
                continue
            _, report = validate_definition_data(data)
        else:
            _, report = validate_data(data, DefinitionLibrary([p.resolve().parent / "instruments"]))
        for d in report.diagnostics:
            hint = f" ({d.suggestion})" if d.suggestion else ""
            print(f"{path}:{d.pointer}: {d.code} {d.message}{hint}", file=sys.stderr)
        if not report.ok:
            status = 1
    return status


def _trust(args: argparse.Namespace) -> int:
    """Grant or withdraw this user's consent to run a workspace's ``components/*.py`` (#1237)."""
    from .paths import user_data_dir
    from .plugins import TRUST_FILE, trust_workspace, untrust_workspace, workspace_allows_plugins

    workspace = Path(args.workspace).resolve()
    if args.revoke:
        untrust_workspace(workspace)
        print(f"no longer trusting {workspace}")
    else:
        trust_workspace(workspace)
        print(f"trusting {workspace}: its components/*.py runs when it is opened")
    print(f"kept in {user_data_dir() / TRUST_FILE} (yours, not the workspace's)")
    return 0 if workspace_allows_plugins(workspace) != args.revoke else 1


def _storage(args: argparse.Namespace) -> int:
    """What the workspace's ``.simubench/`` holds and which of it lost its file; ``--clean`` removes it (#1238)."""
    from dataclasses import asdict

    from .storage import clean_orphans, human, inventory

    workspace = Path(args.workspace).resolve()
    entries = inventory(workspace)
    kinds = ("assistant", "files", "history") if args.history else ("assistant", "files")
    doomed = [e for e in entries if e.orphan and e.kind in kinds]
    if args.json:
        print(json.dumps([{**asdict(e), "path": str(e.path)} for e in entries], ensure_ascii=False, indent=2))
    else:
        for kind in ("history", "assistant", "files"):
            mine = [e for e in entries if e.kind == kind]
            print(f"{kind}: {human(sum(e.bytes for e in mine))} in {len(mine)} entries")
            for e in sorted(mine, key=lambda e: -e.bytes):
                print(f"  {human(e.bytes):>10}  {e.name}{'  (its file is gone)' if e.orphan else ''}")
        if args.clean:
            print(
                f"\n{'removing' if args.yes else 'would remove'}: {len(doomed)} entries, {human(sum(e.bytes for e in doomed))}"
            )
            for e in doomed:
                print(f"  {e.kind}: {e.name}")
    if args.clean and args.yes:
        clean_orphans(workspace, kinds)
    elif args.clean and doomed:
        print("nothing deleted; add --yes to delete (version history is kept unless --history)")
    return 0


def _doctor(args: argparse.Namespace) -> int:
    """Health checks; exit 1 when one fails (warnings do not fail)."""
    from dataclasses import asdict

    from .ops import doctor

    workspace = Path(args.workspace).resolve() if args.workspace else None
    checks = doctor(
        workspace, port=args.port, scpi_base_port=args.scpi_base_port, allow_plugins=args.allow_plugins
    )
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


def _changelog(args: argparse.Namespace) -> int:
    """Print the changelog the wheel was built with (D58); a source checkout has none."""
    path = Path(__file__).with_name("CHANGELOG.md")
    if not path.is_file():
        print(
            "no changelog in this installation: it is written when the wheel is built "
            "(scripts/build-wheel.sh); in a checkout run python scripts/changelog.py",
            file=sys.stderr,
        )
        return 1
    sys.stdout.write(path.read_text(encoding="utf-8"))
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
        report = replay(
            args.record,
            definition_dirs=args.definitions or None,
            stop_at_first=args.first,
            workspace_plugins=Path.cwd() / "components" if args.allow_plugins else None,
        )
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


def _verdicts(args: argparse.Namespace) -> int:
    """Check a calibration script's verdicts against the truth; exit 1 on a misjudgment (C7)."""
    from .engine.verdicts import VerdictError, check_verdicts

    try:
        report = check_verdicts(
            args.record,
            args.report,
            args.map,
            definition_dirs=args.definitions or None,
            workspace_plugins=Path.cwd() / "components" if args.allow_plugins else None,
        )
    except (VerdictError, ValueError, OSError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        return 0 if report.ok else 1
    for w in report.warnings:
        print(f"warning: {w}", file=sys.stderr)

    def g(x: float) -> str:
        return f"{x:.9g}"

    for p in report.points:
        print(
            f"row {p.row} ({p.instrument}): reading {g(p.reading)}  true input {g(p.true_input)}  "
            f"script error {p.script_error:+.9g}  true error {p.true_error:+.9g}  "
            f"{p.statement} → {p.verdict}"
        )
    s = report.summary
    print(
        f"{len(report.points)} points: {s['correct']} correct, {s['false_accept']} false_accept, "
        f"{s['false_reject']} false_reject, {s['withheld']} withheld"
    )
    if not report.ok:
        print("MISJUDGED: the script's statements disagree with the truth", file=sys.stderr)
        return 1
    print("every statement agrees with the truth")
    return 0


def _cases(args: argparse.Namespace) -> int:
    """Run cases files, each case on a freshly powered bench; exit 1 when a case fails (D6)."""
    from .library.coverage import junit_xml, run_cases

    dirs = [Path(d) for d in args.definitions] + [Path.cwd() / "instruments"]
    suites = []
    for path in args.files:
        try:
            results = run_cases(Path(path), dirs)
        except Exception as exc:  # the file does not read
            print(f"{path}: {exc or type(exc).__name__}", file=sys.stderr)
            return 2
        suites.append((path, results))
        for r in results:
            if not r.passed:
                print(f"{path}: FAIL {r.describe()}", file=sys.stderr)
        passed = sum(r.passed for r in results)
        print(f"{path}: {passed}/{len(results)} cases passed")
    if args.junit:
        Path(args.junit).write_text(junit_xml(suites), encoding="utf-8")
    return 0 if all(r.passed for _, results in suites for r in results) else 1


def _coverage(args: argparse.Namespace) -> int:
    """A definition's coverage table (D42): every command of the basis and how far it is implemented."""
    from .library.coverage import GAP_TEXT, LEVEL_TEXT, coverage_path, load_coverage
    from .library.loader import DefinitionLibrary

    lib = DefinitionLibrary([Path(d) for d in args.definitions] + [Path.cwd() / "instruments"])
    if args.draft:
        return _coverage_draft(args, lib)
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
            gap = ""
            if c.gap is not None:
                gap = f"  [{GAP_TEXT[c.gap]}{f' {c.blocked_by}' if c.blocked_by else ''}]"
            note = f"  {c.note}" if c.note else ""
            print(f"  {LEVEL_TEXT[c.level]:<4} {c.header}{gap}{note}")
    st = cov.stats()
    total = sum(st.values())
    print(f"\n{' · '.join(f'{LEVEL_TEXT[k]} {v}' for k, v in st.items())} · 共 {total}")
    if st["not_modeled"]:
        gaps = cov.gap_stats()
        print(f"未实现：{' · '.join(f'{GAP_TEXT[k]} {v}' for k, v in gaps.items())}")
    return 0


def _coverage_draft(args: argparse.Namespace, lib: DefinitionLibrary) -> int:
    """``coverage <id> --draft list.txt``: write or extend the coverage table from a command list."""
    from .library.coverage import (
        coverage_path,
        draft_coverage,
        dump_coverage,
        leading_comments,
        load_coverage,
        read_command_list,
    )
    from .library.loader import COVERAGE_SUFFIX

    commands = read_command_list(Path(args.draft).read_text(encoding="utf-8"))
    try:
        definition = lib.get(args.id)
        default_out = coverage_path(lib.path(args.id))
    except KeyError:
        definition, default_out = None, Path.cwd() / "instruments" / f"{args.id}{COVERAGE_SUFFIX}"
    out = Path(args.out) if args.out else default_out
    existing, comments = None, ""
    if out.is_file():
        existing = load_coverage(out)
        comments = leading_comments(out.read_text(encoding="utf-8"))
    cov, added = draft_coverage(args.id, commands, existing, definition)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dump_coverage(cov, comments), encoding="utf-8")
    total = sum(len(h) for _, h in commands)
    print(f"{out}: {added} of {total} listed commands added, {len(cov.entries())} in the table")
    return 0


def _schemas(args: argparse.Namespace) -> int:
    from .server.schemas import export_all

    out = Path(args.out)
    written = export_all(out)
    for p in written:
        print(p)
    return 0


def utf8_output(*streams: object) -> None:
    """Write UTF-8 to a pipe or file whose locale encoding is narrower (#1203).

    On Windows a redirected stdout is cp1252 (or another code page), and the CLI's ✓, Chinese and
    dashes would raise UnicodeEncodeError mid-output. A console window is already Unicode, and a
    stream that cannot be reconfigured is left alone.
    """
    for stream in streams or (sys.stdout, sys.stderr):
        encoding = str(getattr(stream, "encoding", "") or "").lower().replace("-", "")
        reconfigure = getattr(stream, "reconfigure", None)
        if encoding != "utf8" and reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    utf8_output()
    parser = argparse.ArgumentParser(prog="simubench", description="SimuBench virtual instrument benches")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="start the local service (Web Bench, SCPI sockets)")
    p.add_argument("bench", nargs="?", help="bench file to start right away")
    p.add_argument("--workspace", help="folder searched for bench files (default: the bench's folder)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    add_service_options(p)
    p.add_argument("--open", action="store_true", help="open the browser")
    p.add_argument(
        "--mcp",
        action="store_true",
        help="also serve MCP for AI agents at /mcp (streamable HTTP, needs [mcp])",
    )
    p.add_argument(
        "--allow-control",
        action="store_true",
        help="with --mcp: enable simulation control tools (advance the clock, read true values)",
    )
    p.set_defaults(fn=_serve)

    p = sub.add_parser(
        "launch", help="start Web Bench and MCP together and open the browser (the Windows SimuBench.exe)"
    )
    p.add_argument("--workspace", help="folder with bench files (default: Documents/SimuBench in your home)")
    p.add_argument("--port", type=int, help=f"port (default: {DEFAULT_PORT}, next free one if taken)")
    p.add_argument("--no-browser", action="store_true", help="do not open the browser")
    add_service_options(p)
    # None: not given, so the usual port, or a free one when it is taken (#1197).
    p.set_defaults(fn=_launch, scpi_base_port=None, hislip_port=None)

    p = sub.add_parser("tui", help="terminal console for a running bench (optional dependency, D18)")
    p.add_argument("bench", nargs="?", help="bench file to start in this process, then observe")
    p.add_argument("--workspace", help="folder searched for bench files (default: the bench's folder)")
    p.add_argument(
        "--attach",
        nargs="?",
        const="http://127.0.0.1:8000",
        default=None,
        metavar="URL",
        help="connect to a running serve/mcp instead of starting one (default URL: 127.0.0.1:8000)",
    )
    p.add_argument("--session", help="the session id to attach to (default: only one, else pick)")
    p.add_argument(
        "--clock", choices=["command", "wall", "stepped", "scaled"], help="clock mode for a started bench"
    )
    p.add_argument(
        "--time-scale",
        type=float,
        metavar="N",
        help="with --clock scaled: virtual seconds per real second (default: the bench's run.time_scale)",
    )
    p.add_argument("--seed", type=int, help="bench seed for a started bench")
    add_service_options(p, logs=False, token="present")
    p.add_argument("--script", metavar="STEPS_YAML", help="run these steps headlessly and exit (6)")
    p.add_argument("--out", metavar="DIR", default="tui-shots", help="where the SVGs go (default: tui-shots)")
    p.add_argument(
        "--size", metavar="COLSxROWS", help="override the steps file's size, e.g. 100x34 (issue 684)"
    )
    p.add_argument("--record", action="store_true", help="also write per-frame SVGs and a .cast (6)")
    p.set_defaults(fn=_tui)

    p = sub.add_parser("mcp", help="MCP service on stdio for AI agents, with the Web Bench alongside")
    p.add_argument("--workspace", default=".", help="folder with bench files (default: current folder)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help="Web Bench port (next free one if taken)")
    p.add_argument("--no-ui", action="store_true", help="do not serve the Web Bench")
    add_service_options(p)
    p.add_argument(
        "--allow-control",
        action="store_true",
        help="enable simulation control tools (advance the clock, read true values)",
    )
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
    p.add_argument(
        "--allow-plugins", action="store_true", help="also run components/*.py from the current folder"
    )
    p.add_argument("--first", action="store_true", help="stop at the first difference")
    p.add_argument("--json", action="store_true", help="print the report as JSON")
    p.set_defaults(fn=_replay)

    p = sub.add_parser("coverage", help="a definition's coverage table: commands of its basis by level (D42)")
    p.add_argument("id", help="definition id, e.g. keysight-34465a")
    p.add_argument("--definitions", action="append", default=[], help="also look for definitions here")
    p.add_argument(
        "--draft",
        metavar="LIST",
        help="write the coverage table from a command list (one header per line, '# section' lines); "
        "an existing table keeps its entries and gains the missing commands as not modeled",
    )
    p.add_argument(
        "--out",
        help="with --draft: the table to write (default: next to the definition, or instruments/<id>.coverage.yaml)",
    )
    p.set_defaults(fn=_coverage)

    p = sub.add_parser(
        "verdicts",
        help="check a calibration script's verdicts against the truth (exit 1 on a misjudgment, C7)",
    )
    p.add_argument("record", help="run-*.jsonl of the script's run")
    p.add_argument("report", help="the verdict report (CSV) the script wrote")
    p.add_argument("--map", required=True, help="column map (YAML): which column is what, how decisions read")
    p.add_argument("--definitions", action="append", help="extra instrument definition folder (repeatable)")
    p.add_argument(
        "--allow-plugins", action="store_true", help="also run components/*.py from the current folder"
    )
    p.add_argument("--json", action="store_true", help="print the report as JSON")
    p.set_defaults(fn=_verdicts)

    p = sub.add_parser(
        "cases", help="run cases files (commands and expected replies) on fresh benches; exit 1 on a failure"
    )
    p.add_argument("files", nargs="+", metavar="cases.yaml")
    p.add_argument("--junit", help="also write a JUnit XML report here (for CI)")
    p.add_argument("--definitions", action="append", default=[], help="also look for definitions here")
    p.set_defaults(fn=_cases)

    p = sub.add_parser("schemas", help="export OpenAPI and JSON Schemas (D21)")
    p.add_argument("--out", default="schemas")
    p.set_defaults(fn=_schemas)

    p = sub.add_parser("version", help="print the version (--full: the whole version manifest)")
    p.add_argument(
        "--full", action="store_true", help="dependencies, definitions and their hashes, web UI build"
    )
    p.add_argument("--workspace", help="include this workspace's own definitions")
    p.set_defaults(fn=_version)

    p = sub.add_parser("changelog", help="what changed for the user, by version (built into the wheel)")
    p.set_defaults(fn=_changelog)

    p = sub.add_parser("trust", help="let a workspace's components/*.py run when you open it (kept per user)")
    p.add_argument("workspace", nargs="?", default=".", help="the workspace folder (default: here)")
    p.add_argument("--revoke", action="store_true", help="withdraw the consent")
    p.set_defaults(fn=_trust)

    p = sub.add_parser(
        "storage", help="what the workspace's .simubench/ holds and what lost its file (--clean removes it)"
    )
    p.add_argument("workspace", nargs="?", default=".", help="the workspace folder (default: here)")
    p.add_argument("--clean", action="store_true", help="list what would be removed: data whose file is gone")
    p.add_argument("--yes", action="store_true", help="with --clean: really delete")
    p.add_argument(
        "--history", action="store_true", help="with --clean: also saved versions of files that are gone"
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_storage)

    p = sub.add_parser("doctor", help="check that SimuBench can run here (exit 1 on a failure)")
    p.add_argument("--workspace", help="also validate the bench files in this folder")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--scpi-base-port", type=int, default=5025)
    p.add_argument("--allow-plugins", action="store_true", help="also run components/*.py from the workspace")
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
