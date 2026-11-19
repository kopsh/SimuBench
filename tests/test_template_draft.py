"""From a script to template drafts (issue 145, D81): with the built-in library and the templates only,
an instrument the library does not speak gets a template and inputs filled in from the script, with a
command table learned command by command (from a library model with the same header, else a
mnemonic, else kept as a setting to confirm); the bench draft wires the source to the scope
(inferred) and runs deterministically."""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from simubench.library.loader import DefinitionLibrary
from simubench.library.scriptscan import draft_bench
from simubench.library.templates import get_template
from simubench.library.templates.draft import suggester

ROOT = Path(__file__).resolve().parents[1]
LFCAL_SCRIPT = ROOT / "examples" / "lowfreq-cal" / "lowfreq_cal_demo.py"
HOST_SCRIPT = ROOT / "acceptance" / "lowfreq_cal_host.py"  # the issue-146 host: it writes PERSist 5
KEYSIGHT_SCRIPT = ROOT / "examples" / "scripts" / "keysight_dsox_read_waveform.py"


def builtin() -> list:
    lib = DefinitionLibrary([])  # the wheel's built-in definitions only
    return [lib.get(i) for i in lib.ids()]


def _draft(source: str, definitions=builtin) -> dict:
    models = definitions()
    return draft_bench(source, lambda: models, name="draft", suggest=suggester(source, models))


def _entry(result: dict, receiver: str) -> dict:
    return next(e for e in result["report"] if e["receiver"] == receiver)


def test_the_low_frequency_script_gets_both_templates_and_every_command():
    result = _draft(LFCAL_SCRIPT.read_text("utf-8"))
    scope, cal = _entry(result, "scope"), _entry(result, "cal")
    assert scope["template"]["template"] == "oscilloscope" and cal["template"]["template"] == "signal_source"
    # The scope's commands as the library's LeCroy binds them; the calibrator's tree no library model
    # speaks, so its commands are bound by the source template's mnemonics. No command set is chosen.
    assert scope["template"]["learned_from"][0] == "lecroy-wavepro804hd"
    assert cal["template"]["learned_from"] == [] and cal["template"]["stored_only"] == []
    for e in (scope, cal):
        assert e["supported"] == e["checked"] > 0 and e["unsupported"] == [], e["unsupported"]
        assert e["template"]["diagnostics"] == []
    # What neither knows is kept as a setting that only stores its value, with its line, to confirm.
    stored = {s["command"]: s for s in scope["template"]["stored_only"]}
    assert set(stored) == {"ILVD OFF"} and stored["ILVD OFF"]["line"] > 0
    table = {c["header"]: c for c in cal["template"]["inputs"]["commands"]["table"]}
    assert table[":CHANnel<ch>:BASE:FREQuency"] == {
        "header": ":CHANnel<ch>:BASE:FREQuency",
        "state": "ch.frequency",
    }
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


def test_a_keysight_style_script_without_any_library_is_bound_by_mnemonics():
    result = _draft(KEYSIGHT_SCRIPT.read_text("utf-8"), definitions=list)
    scope = _entry(result, "scope")
    assert scope["template"]["template"] == "oscilloscope" and scope["template"]["learned_from"] == []
    assert scope["supported"] == scope["checked"] > 0 and scope["unsupported"] == []
    assert scope["template"]["stored_only"] == []
    assert scope["template"]["diagnostics"] == []
    # With the built-in library the model itself is chosen and no template is needed.
    library = _entry(_draft(KEYSIGHT_SCRIPT.read_text("utf-8")), "scope")
    assert library["model"] == "keysight-dsox2024a" and "template" not in library


def test_the_report_rows_list_the_alternatives_beside_what_was_chosen():
    """One row constructor serves both outcomes, with its slices pinned: a definition pick names
    the two runner-up definitions (it is ranked[0] itself, so ranked[1:3]); a template pick names
    the two best the library had (the template definition is new, so ranked[:2])."""
    library = _entry(_draft(KEYSIGHT_SCRIPT.read_text("utf-8")), "scope")
    assert "template" not in library and library["model"] == "keysight-dsox2024a"
    assert [(a["model"], a["supported"], a["checked"]) for a in library["alternatives"]] == [
        ("sb-dso-2x", 9, 10),  # the sb-* validation definitions the tests load (conftest.py)
        ("sb-dso-4", 9, 10),
    ]
    template = _entry(_draft(LFCAL_SCRIPT.read_text("utf-8")), "scope")
    assert "template" in template and template["model"] == "lfcal-refscope"
    assert [(a["model"], a["supported"], a["checked"]) for a in template["alternatives"]] == [
        ("lecroy-wavepro804hd", 15, 18),
        ("keysight-uxr0334b", 2, 18),
    ]


def test_a_row_names_the_commands_the_library_did_not_speak():
    """A command the chosen model refuses is one unsupported entry with its line and reason; a
    receiver no library speaks keeps its row (model null, no instrument) with every command
    named — the row is the engineer's list of what the draft could not place."""
    source = KEYSIGHT_SCRIPT.read_text("utf-8").replace(
        '    scope.write(":WAVeform:BYTeorder MSBFirst")\n',
        '    scope.write(":WAVeform:BYTeorder MSBFirst")\n    scope.write("BOGUS:COMMAND 1")\n',
    )
    row = _entry(_draft(source), "scope")
    assert (row["supported"], row["checked"]) == (10, 11)
    line = source.splitlines().index('    scope.write("BOGUS:COMMAND 1")') + 1
    assert row["unsupported"] == [
        {"line": line, "command": "BOGUS:COMMAND 1", "reason": "仪表模型中没有该命令"}
    ]
    stranger = """
import pyvisa
rm = pyvisa.ResourceManager()
x = rm.open_resource("TCPIP0::1.2.3.4::10001::SOCKET")
x.write("ZZZ:NOTHING 1")
"""
    row = draft_bench(stranger, builtin, name="draft")["report"][0]
    assert row["model"] is None and "template" not in row
    assert row["unsupported"] == [{"line": 5, "command": "ZZZ:NOTHING 1", "reason": "没有匹配的仪表"}]


def test_a_command_nothing_knows_is_kept_as_a_setting_to_confirm_with_its_line():
    source = LFCAL_SCRIPT.read_text("utf-8").replace(
        '    scope.write("RCLK INTERNAL")\n',
        '    scope.write("RCLK INTERNAL")\n    scope.write("HISTOGRAM_MODE ON")\n',
    )
    line = source.splitlines().index('    scope.write("HISTOGRAM_MODE ON")') + 1
    scope = _entry(_draft(source), "scope")
    assert scope["unsupported"] == []
    stored = [(s["line"], s["command"]) for s in scope["template"]["stored_only"]]
    assert (line, "HISTOGRAM_MODE ON") in stored
    setting = scope["template"]["inputs"]["settings"]["stored.histogram_mode"]
    assert setting == {"type": "bool", "default": True}


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


async def test_the_draft_extends_a_template_definition_in_place_and_reexpansion_keeps_it(tmp_path):
    """Issue 169: extending a workspace definition in place is one call; re-expanding the template
    afterwards refuses to drop the drafted command until it is merged or explicitly discarded."""
    shutil.copy(HOST_SCRIPT, tmp_path / "host.py")
    server = build_server(SessionManager(tmp_path, scpi_base_port=None), ui_url="http://127.0.0.1:1/?token=x")

    async def call(c, tool, **args):
        r = await c.call_tool(tool, args)
        assert not r.is_error, (tool, r.content[0].text)
        sc = r.structured_content
        return (
            sc.get("result", sc)
            if sc is not None and set(sc) == {"result"}
            else (sc or json.loads(r.content[0].text))
        )

    async with Client(server) as c:
        draft = await call(c, "draft_bench_from_script", script_path="host.py")
        scope_t = next(e for e in draft["report"] if e["receiver"] == "scope")["template"]
        # The definition as it was made before the host sent :PERSist (a template draft keeps every
        # command of the script, D81): without that command and the setting it stored.
        inputs = copy.deepcopy(scope_t["inputs"])
        [persist] = [c for c in inputs["commands"]["table"] if "PERS" in c["header"].upper()]
        inputs["commands"]["table"].remove(persist)
        inputs["settings"].pop(persist["state"])
        doc = await call(
            c, "create_definition_from_template", template_id="oscilloscope",
            definition_id=scope_t["definition_id"], inputs=inputs,
        )  # fmt: skip
        assert doc["saved"] and doc["ok"], doc["diagnostics"]
        before = await call(c, "read_definition", definition_id="lfcal-refscope")
        new_index = len(before["data"]["commands"])

        # The in-place extension: one call saves the draft over the file, recorded as the agent's.
        fixed = await call(
            c, "draft_definition_from_script", script_path="host.py", definition_id="lfcal-refscope",
            receiver="scope", base_definition="lfcal-refscope", save=True, base_sha256=before["sha256"],
        )  # fmt: skip
        assert fixed["saved"]["ok"] and [d["header"] for d in fixed["drafted"]] == [":PERSIST"]
        assert fixed["saved"]["path"] == "instruments/lfcal-refscope.yaml"
        history = (tmp_path / ".simubench/history/instruments/lfcal-refscope.yaml/index.jsonl").read_text(
            "utf-8"
        )
        assert json.loads(history.splitlines()[-1])["source"] == "agent"
        after = await call(c, "read_definition", definition_id="lfcal-refscope")
        assert len(after["data"]["commands"]) == new_index + 1

        # A stale sha256 is a conflict and leaves the file as it is.
        r = await c.call_tool(
            "draft_definition_from_script", {"script_path": "host.py", "definition_id": "lfcal-refscope",
                                             "receiver": "scope", "base_definition": "lfcal-refscope",
                                             "save": True, "base_sha256": before["sha256"]}
        )  # fmt: skip
        assert r.is_error and "changed since it was read" in r.content[0].text
        same = await call(c, "read_definition", definition_id="lfcal-refscope")
        assert same["sha256"] == after["sha256"]

        # Re-expanding the template would drop the drafted command: refused, with its pointer.
        r = await c.call_tool(
            "create_definition_from_template", {"template_id": "oscilloscope", "definition_id": "lfcal-refscope",
                                                "inputs": inputs, "base_sha256": after["sha256"]}
        )  # fmt: skip
        assert r.is_error and f"/commands/{new_index}" in r.content[0].text, r.content[0].text
        assert (await call(c, "read_definition", definition_id="lfcal-refscope"))["sha256"] == after["sha256"]

        # Confirmed discarded, the re-expansion goes through and the command is gone; a definition
        # untouched since the template still re-expands as before.
        redone = await call(
            c, "create_definition_from_template", template_id="oscilloscope", definition_id="lfcal-refscope",
            inputs=inputs, base_sha256=after["sha256"], discard_edits=True,
        )  # fmt: skip
        assert redone["saved"] and redone["ok"], redone["diagnostics"]
        final = await call(c, "read_definition", definition_id="lfcal-refscope")
        assert len(final["data"]["commands"]) == new_index
        again = await call(
            c, "create_definition_from_template", template_id="oscilloscope", definition_id="lfcal-refscope",
            inputs=inputs, base_sha256=final["sha256"],
        )  # fmt: skip
        assert again["saved"] and again["ok"], again["diagnostics"]
