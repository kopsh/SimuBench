"""The Windows double-click start (#1188): `serve --mcp` (UI and MCP on one port), `simubench launch`,
and the launcher executables scripts/windows_bundle.py writes."""

from __future__ import annotations

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
    pytest.importorskip("mcp")
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


@pytest.mark.unit
def test_second_launch_only_opens_the_running_one(monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(cli, "_running_simubench", lambda port: port == cli.DEFAULT_PORT)
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    assert cli.main(["launch"]) == 0
    assert opened == [f"http://127.0.0.1:{cli.DEFAULT_PORT}/"]
    assert "already running" in capsys.readouterr().out


@pytest.mark.unit
def test_launch_workspace_is_in_documents(monkeypatch, tmp_path):
    (tmp_path / "Documents").mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert cli.default_launch_workspace() == tmp_path / "Documents" / "SimuBench"


@pytest.mark.unit
def test_launch_starts_serve_with_mcp_and_the_browser(monkeypatch, tmp_path, capsys):
    served = []
    monkeypatch.setattr(cli, "_running_simubench", lambda port: False)
    monkeypatch.setattr(cli, "_free_port", lambda port: 9123)
    monkeypatch.setattr(cli, "_serve", lambda args: served.append(args) or 0)
    assert cli.main(["launch", "--workspace", str(tmp_path / "ws")]) == 0
    (args,) = served
    assert (args.workspace, args.port, args.mcp, args.open) == (str(tmp_path / "ws"), 9123, True, True)
    # no port given: the usual ones, replaced by free ones if taken (#1197)
    assert (args.scpi_base_port, args.hislip_port) == (5025, 4880)
    assert args.port_fallback == {"SCPI", "HiSLIP"}
    config = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert config["mcpServers"]["simubench"]["url"] == "http://127.0.0.1:9123/mcp"


@pytest.mark.unit
def test_launch_keeps_a_port_given_on_the_command_line(monkeypatch, tmp_path):
    served = []
    monkeypatch.setattr(cli, "_running_simubench", lambda port: False)
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
