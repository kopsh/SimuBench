"""Plugin components (issue 47): installed packages' entry points load by default, a workspace's
``components/*.py`` only when the engineer allows it, and run records carry plugin hashes (D36)."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from simubench import plugins
from simubench.api import open_bench
from simubench.engine.component import _REGISTRY, component_type
from simubench.engine.replay import replay
from simubench.ops import doctor, manifest
from simubench.server.manager import SessionManager

ROOT = Path(__file__).resolve().parents[1]
BENCH_WITHOUT_PLUGINS = ROOT / "acceptance" / "benches" / "awg_rc_scope.bench.yaml"

PLUGIN_SRC = """\
plugin_api = 1
from simubench.engine.circuit import GROUND
from simubench.engine.component import Component, PortSpec, component


@component("plugin_load")
class PluginLoad(Component):
    \"\"\"A resistor to ground, provided by a plugin.\"\"\"

    category = "dut"
    label = "插件负载"
    ports = {"A": PortSpec("inout", "electrical")}
    defaults = {"r": 2000.0}

    def stamp(self, circuit, node):
        circuit.resistor(node("A"), GROUND, float(self.param("r")))
"""

BENCH = """\
schema: simubench.bench/1
name: plugin_bench
description: uses a component that only a plugin provides
instruments:
  awg:
    model: sb-awg-2
    address: "TCPIP0::127.0.0.1::5025::SOCKET"
duts:
  load:
    type: plugin_load
    params: {r: 2000.0}
wires:
  - {from: awg.OUT1, to: load.A}
run: {seed: 1, clock: command}
"""

#: Imports are the danger (and the tell): if this file runs, it leaves a marker behind.
BOMB = "from pathlib import Path\nPath(__file__).with_suffix('.imported').write_text('x')\n"


@pytest.fixture
def clean_plugins():
    """Plugin state is process-global; each test leaves the registry as it found it."""
    before = dict(_REGISTRY)
    plugins._reset()
    yield
    _REGISTRY.clear()
    _REGISTRY.update(before)
    plugins._reset()


def workspace_with(tmp_path: Path, *files: str, bench: bool = True) -> Path:
    (tmp_path / "components").mkdir()
    (tmp_path / "components" / "plugin_load.py").write_text(PLUGIN_SRC, encoding="utf-8")
    for f in files:
        (tmp_path / "components" / f).write_text(BOMB, encoding="utf-8")
    if bench:
        (tmp_path / "plugin.bench.yaml").write_text(BENCH, encoding="utf-8")
    return tmp_path


class FakeEntryPoint:
    """Stands in for an installed package's ``simubench.components`` entry point."""

    def __init__(self, file: Path, dist: str = "acme-components 1.2"):
        self.name = file.stem
        self._file = file
        pkg, version = dist.split()
        self.dist = SimpleNamespace(metadata={"Name": pkg}, version=version)

    @property
    def value(self) -> str:
        return f"fake.{self.name}"

    def load(self):
        spec = importlib.util.spec_from_file_location(f"fake_pkg_{self.name}", self._file)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[str(spec.name)] = module
        spec.loader.exec_module(module)
        return module


def test_entry_point_plugin_loads_by_default_and_serves(tmp_path, monkeypatch, clean_plugins):
    ws = workspace_with(tmp_path)
    monkeypatch.setattr(plugins, "_discover", lambda: [FakeEntryPoint(ws / "components" / "plugin_load.py")])
    session = open_bench(ws / "plugin.bench.yaml", clock="command")
    try:
        assert "load" in session.components
        assert component_type("plugin_load").type_name == "plugin_load"
    finally:
        session.close()


def test_record_header_names_the_plugins(tmp_path, monkeypatch, clean_plugins):
    ws = workspace_with(tmp_path)
    monkeypatch.setattr(plugins, "_discover", lambda: [FakeEntryPoint(ws / "components" / "plugin_load.py")])
    records = tmp_path / "records"
    s = open_bench(ws / "plugin.bench.yaml", record_dir=records, clock="command")
    s.write("awg", b"*IDN?\n")
    s.read("awg")
    s.close()
    header = json.loads(next(records.glob("run-*.jsonl")).read_text(encoding="utf-8").splitlines()[0])
    plugin = header["plugins"]["plugin_load"]
    assert plugin["source"] == "entry-point" and plugin["origin"] == "acme-components 1.2"
    assert len(plugin["sha256"]) == 64


def test_workspace_plugins_are_not_loaded_unless_allowed(tmp_path, clean_plugins):
    ws = workspace_with(tmp_path, "bomb.py")
    with pytest.raises(KeyError, match="plugin_load"):
        open_bench(ws / "plugin.bench.yaml")
    assert not (ws / "components" / "bomb.imported").exists()  # never imported, so never run
    session = open_bench(ws / "plugin.bench.yaml", workspace_plugins=ws / "components")
    try:
        assert "load" in session.components
    finally:
        session.close()
    assert (ws / "components" / "bomb.imported").exists()  # consent runs every file in there


def test_manager_takes_the_flag_and_the_workspace_setting(tmp_path, clean_plugins):
    ws = workspace_with(tmp_path, "bomb.py")
    default = SessionManager(ws, scpi_base_port=0, hislip_port=0)
    assert not default.plugins_allowed()
    assert not (ws / "components" / "bomb.imported").exists()
    SessionManager(ws, scpi_base_port=0, hislip_port=0, allow_plugins=True)
    assert (ws / "components" / "bomb.imported").exists()


def test_workspace_setting_enables_plugins_without_the_flag(tmp_path, clean_plugins):
    ws = workspace_with(tmp_path, "bomb.py")
    (ws / ".simubench").mkdir()
    (ws / ".simubench" / "settings.json").write_text('{"allow_workspace_plugins": true}', encoding="utf-8")
    manager = SessionManager(ws, scpi_base_port=0, hislip_port=0)
    assert manager.plugins_allowed()
    assert (ws / "components" / "bomb.imported").exists()


async def test_manager_session_uses_a_workspace_plugin(tmp_path, clean_plugins):
    ws = workspace_with(tmp_path)
    manager = SessionManager(ws, scpi_base_port=0, hislip_port=0, allow_plugins=True)
    served = await manager.start("plugin.bench.yaml")  # validation sees the plugin's type too
    try:
        assert "load" in served.session.components
    finally:
        await manager.stop(served.id)


def test_a_plugin_targeting_another_api_is_refused(tmp_path, clean_plugins):
    (tmp_path / "components").mkdir()
    (tmp_path / "components" / "future.py").write_text(
        PLUGIN_SRC.replace("plugin_api = 1", "plugin_api = 2"), encoding="utf-8"
    )
    infos = plugins.load_workspace_plugins(tmp_path / "components")
    assert infos[0].name == "future.py" and "plugin_api 2" in (infos[0].error or "")
    with pytest.raises(KeyError, match="plugin_load"):
        component_type("plugin_load")  # its registrations were undone
    assert plugins.report() == {}  # refused plugins stay out of the record header


def test_replay_warns_when_a_plugin_changed(tmp_path, clean_plugins):
    ws = workspace_with(tmp_path)
    records = tmp_path / "records"
    s = open_bench(
        ws / "plugin.bench.yaml", record_dir=records, clock="command", workspace_plugins=ws / "components"
    )
    s.write("awg", b"*IDN?\n")
    s.read("awg")
    s.close()
    rec = next(records.glob("run-*.jsonl"))
    report = replay(rec, workspace_plugins=ws / "components")
    assert report.ok and report.warnings == []
    (ws / "components" / "plugin_load.py").write_text(
        PLUGIN_SRC + "\n# changed since the record\n", encoding="utf-8"
    )
    report = replay(rec, workspace_plugins=ws / "components")
    assert any("plugin_load.py changed since the record" in w for w in report.warnings)


def test_replay_warns_when_a_recorded_plugin_is_not_loaded(tmp_path, monkeypatch, clean_plugins):
    monkeypatch.setattr(plugins, "_discover", lambda: [FakeEntryPoint(tmp_path / "acme.py")])
    (tmp_path / "acme.py").write_text(PLUGIN_SRC, encoding="utf-8")
    records = tmp_path / "records"
    s = open_bench(BENCH_WITHOUT_PLUGINS, record_dir=records, clock="command")
    s.write("awg", b"*IDN?\n")
    s.read("awg")
    s.close()
    rec = next(records.glob("run-*.jsonl"))
    monkeypatch.undo()
    plugins._reset()
    _REGISTRY.pop("plugin_load", None)
    report = replay(rec)  # no plugins at all in this replay
    assert any("plugin acme" in w and "not loaded now" in w for w in report.warnings)


def test_doctor_and_manifest_list_the_plugins(tmp_path, clean_plugins):
    ws = workspace_with(tmp_path)
    checks = {c.name: c for c in doctor(ws)}
    assert checks["plugins"].status == "ok" and checks["plugins"].detail == "none"
    assert "not enabled" in checks["workspace plugins"].detail
    allowed = {c.name: c for c in doctor(ws, allow_plugins=True)}
    assert allowed["workspace plugins"].status == "ok" and "1 loaded" in allowed["workspace plugins"].detail
    assert {p["name"] for p in manifest(ws)["plugins"]} == {"plugin_load.py"}
