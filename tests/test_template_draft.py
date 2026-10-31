"""From a script to template drafts (issue 145): with the built-in library and the templates only, an
instrument the library does not speak gets a template, a command set and inputs filled in from the
script; the bench draft wires the source to the scope (inferred) and runs deterministically."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from simubench.engine.scriptscan import draft_bench
from simubench.library.loader import DefinitionLibrary
from simubench.library.templates import get_template
from simubench.library.templates.draft import suggester

ROOT = Path(__file__).resolve().parents[1]
LFCAL_SCRIPT = ROOT / "examples" / "lowfreq-cal" / "lowfreq_cal_demo.py"
KEYSIGHT_SCRIPT = ROOT / "examples" / "scripts" / "keysight_dsox_read_waveform.py"


def builtin() -> list:
    lib = DefinitionLibrary([])  # the wheel's built-in definitions only
    return [lib.get(i) for i in lib.ids()]


def _draft(source: str, definitions=builtin) -> dict:
    return draft_bench(source, definitions, name="draft", suggest=suggester(source))


def _entry(result: dict, receiver: str) -> dict:
    return next(e for e in result["report"] if e["receiver"] == receiver)


def test_the_low_frequency_script_gets_both_templates_and_every_command():
    result = _draft(LFCAL_SCRIPT.read_text("utf-8"))
    scope, cal = _entry(result, "scope"), _entry(result, "cal")
    assert (scope["template"]["template"], scope["template"]["variant"]) == ("oscilloscope", "lecroy-scope")
    assert (cal["template"]["template"], cal["template"]["variant"]) == ("signal_source", "channel-base")
    for e in (scope, cal):
        assert e["supported"] == e["checked"] > 0 and e["unsupported"] == [], e["unsupported"]
        assert e["template"]["diagnostics"] == []
    # The model strings the script checks *IDN? against, with their line; the rest are defaults.
    filled = {f["pointer"]: f for f in scope["template"]["filled"]}
    assert filled["/template/inputs/identity/model"]["value"] == "LFCAL-REFSCOPE"
    assert filled["/template/inputs/identity/model"]["source"] == "script"
    assert filled["/template/inputs/identity/model"]["line"] > 0
    assert filled["/template/inputs/identity/manufacturer"]["source"] == "default"
    duty = next(f for f in cal["template"]["filled"] if f["pointer"].endswith("/duty/unit"))
    assert duty["value"] == "ratio" and duty["source"] == "script" and "待确认" in duty["note"]
    # Bench: the two instruments, the cable (inferred) and a deterministic run.
    bench = result["bench"]
    assert set(bench["instruments"]) == {"scope", "cal"}
    assert bench["instruments"]["scope"]["model"] == scope["template"]["definition_id"] == "lfcal-refscope"
    assert bench["wires"] == [{"from": "cal.CH1", "to": "scope.CH1"}]
    assert result["inferred_wires"][0]["why"].startswith("推断")
    assert bench["run"] == {"seed": 1, "clock": "command", "idle": "off"}


def test_a_keysight_style_script_without_its_model_gets_the_scpi_scope():
    result = _draft(KEYSIGHT_SCRIPT.read_text("utf-8"), definitions=list)
    scope = _entry(result, "scope")
    assert (scope["template"]["template"], scope["template"]["variant"]) == ("oscilloscope", "scpi-scope")
    assert scope["supported"] == scope["checked"] > 0 and scope["unsupported"] == []
    assert scope["template"]["diagnostics"] == []
    # With the built-in library the model itself is chosen and no template is needed.
    library = _entry(_draft(KEYSIGHT_SCRIPT.read_text("utf-8")), "scope")
    assert library["model"] == "keysight-dsox2024a" and "template" not in library


def test_a_command_no_template_has_is_reported_with_its_line():
    source = LFCAL_SCRIPT.read_text("utf-8").replace(
        '    scope.write("RCLK INTERNAL")\n',
        '    scope.write("RCLK INTERNAL")\n    scope.write("HISTOGRAM_MODE ON")\n',
    )
    line = source.splitlines().index('    scope.write("HISTOGRAM_MODE ON")') + 1
    scope = _entry(_draft(source), "scope")
    assert scope["template"]["variant"] == "lecroy-scope"
    assert [(u["line"], u["command"]) for u in scope["unsupported"]] == [(line, "HISTOGRAM_MODE ON")]


def _module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_the_drafts_run_the_script_end_to_end(tmp_path):
    """The drafted inputs expanded and the drafted bench, nothing else: the host's flow runs through."""
    result = _draft(LFCAL_SCRIPT.read_text("utf-8"))
    (tmp_path / "instruments").mkdir()
    for receiver in ("scope", "cal"):
        t = _entry(result, receiver)["template"]
        exp = get_template(t["template"]).expand(t["inputs"], t["definition_id"])
        assert exp.ok, exp.diagnostics
        (tmp_path / "instruments" / f"{t['definition_id']}.yaml").write_text(
            yaml.safe_dump(exp.definition, allow_unicode=True, sort_keys=False), "utf-8"
        )
    bench = tmp_path / "draft.bench.yaml"
    bench.write_text(yaml.safe_dump(result["bench"], allow_unicode=True, sort_keys=False), "utf-8")
    import pyvisa

    demo = _module(LFCAL_SCRIPT, "lowfreq_cal_demo_draft")
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    try:
        scope = demo.open_instrument(rm, "scope", "LFCAL-REFSCOPE")
        cal = demo.open_instrument(rm, "cal", "LFCAL-CALIBRATOR")
        response = demo.run(scope, cal, amp_vpp=1.0, point=30000, frequencies=[1e3, 1e4, 1e5])
    finally:
        rm.close()
    # 1 Vpp set for 50 Ω, into the scope's D50 input: 1 Vpp at the scope.
    assert [f for f, _ in response] == [1e3, 1e4, 1e5]
    for _, amp in response:
        assert amp == pytest.approx(1.0, rel=0.02)


# ---- MCP -----------------------------------------------------------------------------------------

pytest.importorskip("mcp")

from mcp import Client  # noqa: E402

from simubench.mcp_server import build_server  # noqa: E402
from simubench.server.manager import SessionManager  # noqa: E402


async def test_an_agent_drafts_from_the_script_and_creates_both_definitions(tmp_path):
    shutil.copy(LFCAL_SCRIPT, tmp_path / "host.py")
    server = build_server(SessionManager(tmp_path, scpi_base_port=None), ui_url="http://127.0.0.1:1/?token=x")

    async def call(c, tool, **args):
        r = await c.call_tool(tool, args)
        assert not r.is_error, r.content[0].text
        sc = r.structured_content
        return (
            sc.get("result", sc)
            if sc is not None and set(sc) == {"result"}
            else (sc or json.loads(r.content[0].text))
        )

    async with Client(server) as c:
        draft = await call(c, "draft_bench_from_script", script_path="host.py", save_as="host.bench.yaml")
        made = []
        for e in draft["report"]:
            t = e["template"]
            doc = await call(
                c, "create_definition_from_template", template_id=t["template"], definition_id=t["definition_id"],
                inputs=t["inputs"],
            )  # fmt: skip
            assert doc["saved"] and doc["ok"], doc["diagnostics"]
            made.append(t["definition_id"])
        checked = await call(c, "validate_bench", path="host.bench.yaml")
    assert sorted(made) == ["lfcal-calibrator", "lfcal-refscope"]
    assert checked["ok"], checked["diagnostics"]
