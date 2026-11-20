"""The Windows double-click start (#1188): `serve --mcp` (UI and MCP on one port), `simubench launch`,
and the launcher executables scripts/windows_bundle.py writes."""

from __future__ import annotations

import gc
import io
import json
import zipfile
from pathlib import Path

import pytest

import simubench.cli as cli
from helpers import load_script

ROOT = Path(__file__).resolve().parents[1]
bundle = load_script(ROOT / "scripts" / "windows_bundle.py")

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "0"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def test_serve_mcp_shares_the_port_and_the_token(tmp_path):
    _serve_mcp_flow(tmp_path)
    # The app, the MCP server and the test client leave ~14k objects in reference cycles. Collect
    # them here, so the full GC they trigger is not paid inside the next (unit) test's budget (#1199).
    gc.collect()


def _serve_mcp_flow(tmp_path):
    from fastapi.testclient import TestClient

    from simubench.mcp_server import build_server
    from simubench.server.app import create_app
    from simubench.server.manager import SessionManager

    manager = SessionManager(tmp_path, scpi_base_port=None)
    app = create_app(manager, token="t0k", mcp=build_server(manager))
    started = []
    app.router.add_event_handler("startup", lambda: started.append(True))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        # step: the app's own startup hooks still run under the MCP lifespan (serve's banner)
        assert started == [True]
        # step: no token, no MCP
        assert c.post("/mcp", json=INIT, headers=MCP_HEADERS).status_code == 401
        # step: the token as a bearer opens MCP, and the server names itself
        ok = c.post("/mcp", json=INIT, headers={**MCP_HEADERS, "Authorization": "Bearer t0k"})
        assert ok.status_code == 200
        assert '"simubench"' in ok.text
        # step: the same token header as the data routes works too
        alt = c.post("/mcp", json=INIT, headers={**MCP_HEADERS, "x-simubench-token": "t0k"})
        assert alt.status_code == 200
        # step: a page on another host cannot reach it (DNS rebinding, D22)
        evil = c.post(
            "/mcp", json=INIT, headers={**MCP_HEADERS, "Host": "evil.example", "x-simubench-token": "t0k"}
        )
        assert evil.status_code == 403
        # step: health names the MCP address, for the Web Bench's "connect an AI assistant" dialog
        assert c.get("/api/health").json()["mcp_url"] == "http://127.0.0.1/mcp"


RUNNING = {"mcp_url": "http://127.0.0.1:8765/mcp", "token": False}


@pytest.mark.unit
def test_second_launch_only_opens_the_running_one(monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(cli, "_running_simubench", lambda port: RUNNING if port == cli.DEFAULT_PORT else None)
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    assert cli.main(["launch"]) == 0
    assert opened == [f"http://127.0.0.1:{cli.DEFAULT_PORT}/"]
    out = capsys.readouterr().out
    assert "already running" in out
    assert "no MCP" not in out


@pytest.mark.unit
def test_second_launch_names_a_running_service_without_mcp(monkeypatch, capsys):
    # `serve` without --mcp on the port: the page opens, and the missing MCP is said (#1193)
    opened = []
    monkeypatch.setattr(cli, "_running_simubench", lambda port: {"mcp_url": None, "token": False})
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    assert cli.main(["launch", "--port", "9001"]) == 0
    assert opened == ["http://127.0.0.1:9001/"]
    assert "it has no MCP" in capsys.readouterr().out


@pytest.mark.unit
def test_second_launch_does_not_open_a_service_that_wants_a_token(monkeypatch, capsys):
    # `serve --require-token` on the port: a page without the token would not open (#1193)
    opened = []
    monkeypatch.setattr(cli, "_running_simubench", lambda port: {**RUNNING, "token": True})
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    assert cli.main(["launch"]) == 1
    assert opened == []
    assert "requires a token" in capsys.readouterr().out


@pytest.mark.unit
def test_launch_workspace_is_in_documents(monkeypatch, tmp_path):
    (tmp_path / "Documents").mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert cli.default_launch_workspace() == tmp_path / "Documents" / "SimuBench"


@pytest.mark.unit
def test_launch_starts_serve_with_mcp_and_the_browser(monkeypatch, tmp_path, capsys):
    served = []
    monkeypatch.setattr(cli, "_running_simubench", lambda port: None)
    monkeypatch.setattr(cli, "_free_port", lambda port: 9123)
    monkeypatch.setattr(cli, "_serve", lambda args: served.append(args) or 0)
    assert cli.main(["launch", "--workspace", str(tmp_path / "ws")]) == 0
    (args,) = served
    assert (args.workspace, args.port, args.mcp, args.open) == (str(tmp_path / "ws"), 9123, True, True)
    # no port given: the usual ones, replaced by free ones if taken (#1197)
    assert (args.scpi_base_port, args.hislip_port) == (5025, 4880)
    assert args.port_fallback == {"SCPI", "HiSLIP"}
    # serve prints the MCP client config after the MCP address, once (#1193)
    assert args.mcp_config is True
    assert "mcpServers" not in capsys.readouterr().out


@pytest.mark.unit
def test_launch_keeps_a_port_given_on_the_command_line(monkeypatch, tmp_path):
    served = []
    monkeypatch.setattr(cli, "_running_simubench", lambda port: None)
    monkeypatch.setattr(cli, "_serve", lambda args: served.append(args) or 0)
    assert cli.main(["launch", "--workspace", str(tmp_path), "--no-browser", "--scpi-base-port", "6000"]) == 0
    (args,) = served
    assert args.scpi_base_port == 6000
    assert args.port_fallback == {"HiSLIP"}  # the SCPI port was asked for: kept, or refused


@pytest.mark.unit
def test_launcher_exe_runs_the_bundled_python_on_its_appended_zip():
    exe = bundle._exe(b"MZlauncher", "..\\python\\python.exe", bundle.CLI_MAIN)
    assert exe.startswith(b"MZlauncher#!<launcher_dir>\\..\\python\\python.exe\n")
    with zipfile.ZipFile(io.BytesIO(exe)) as z:  # a zip read from its end, past the launcher
        assert z.read("__main__.py").decode() == bundle.CLI_MAIN


@pytest.mark.unit
def test_embed_zip_from_a_mirror_must_be_the_python_org_file():
    with pytest.raises(SystemExit) as err:
        bundle.check_embed(b"not python", "mirror.zip")
    assert bundle.EMBED_SHA256 in str(err.value)


@pytest.mark.unit
def test_cli_output_to_a_code_page_pipe_is_utf8():
    # A redirected stdout on Windows is cp1252: doctor's ✓ and launch's Chinese must not raise (#1203)
    raw = io.BytesIO()
    pipe = io.TextIOWrapper(raw, encoding="cp1252", newline="\n")  # no \r\n on Windows
    cli.utf8_output(pipe)
    print("✓ SimuBench 已在运行 — ok", file=pipe, flush=True)
    assert raw.getvalue().decode("utf-8") == "✓ SimuBench 已在运行 — ok\n"


LAUNCH = "import sys\nfrom simubench.cli import main\nsys.exit(main(sys.argv[1:]))\n"


def _start(args, log, env):
    import subprocess
    import sys

    return subprocess.Popen([sys.executable, "-c", LAUNCH, *args], stdout=log, stderr=log, env=env)


def _health(port, proc, timeout=60.0):
    import time
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and proc.poll() is None:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
                return json.loads(r.read())
        except OSError:
            time.sleep(0.2)
    return None


def _stop(proc):
    import subprocess

    proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


@pytest.mark.tui_boot  # starts real services
def test_launch_with_a_code_page_stdout_redirected_starts_and_a_second_start_finds_it(tmp_path):
    import os
    import subprocess
    import sys

    # A redirected stdout in a narrow code page (Windows' cp1252) must not stop the start (#1192)
    # unbuffered: the banner reaches the log before the service is stopped, not at a flush on exit
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0", "PYTHONUNBUFFERED": "1"}
    port, token_port = cli._free_port(0), cli._free_port(0)
    common = ["--scpi-base-port", "0", "--hislip-port", "0", "--no-browser", "--workspace", str(tmp_path)]
    with open(tmp_path / "launch.log", "w+b") as log, open(tmp_path / "serve.log", "w+b") as slog:
        proc = _start(["launch", "--port", str(port), *common], log, env)
        guarded = _start(
            ["serve", "--port", str(token_port), "--require-token", "--workspace", str(tmp_path)], slog, env
        )
        try:
            health = _health(port, proc)
            # step: the service answers, with MCP on the same port
            assert health is not None, (tmp_path / "launch.log").read_bytes()
            assert health["mcp_url"] == f"http://127.0.0.1:{port}/mcp"
            # step: a second start on that port finds it and exits (#1193)
            again = subprocess.run(
                [sys.executable, "-c", LAUNCH, "launch", "--port", str(port), "--no-browser"],
                capture_output=True,
                env=env,
                timeout=60,
            )
            assert again.returncode == 0
            assert "already running" in again.stdout.decode("utf-8")
            # step: a service started with a token is named as such, not opened (#1193)
            assert _health(token_port, guarded) is not None
            locked = subprocess.run(
                [sys.executable, "-c", LAUNCH, "launch", "--port", str(token_port), "--no-browser"],
                capture_output=True,
                env=env,
                timeout=60,
            )
            assert locked.returncode == 1
            assert "requires a token" in locked.stdout.decode("utf-8")
        finally:
            _stop(proc)
            _stop(guarded)
        # step: the banner was written whole, in UTF-8, and the MCP client config once (#1192, #1193)
        banner = (tmp_path / "launch.log").read_bytes().decode("utf-8")
        assert "关闭此窗口即停止服务" in banner
        assert banner.count('"mcpServers"') == 1
        config = json.loads(banner.split("MCP client config: ", 1)[1].splitlines()[0])
        assert config["mcpServers"]["simubench"]["url"] == f"http://127.0.0.1:{port}/mcp"
