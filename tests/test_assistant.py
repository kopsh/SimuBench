"""The AI assistant's conversation backend with a scripted LLM (#873, #874, #875, D66): the tool
loop, streaming, the server-side rules, self-repair, the loop guard, stop, concurrency with the
canvas, compaction and the conversation history. Each test walks one flow (D49)."""

from __future__ import annotations

import ast
import asyncio
import logging
from pathlib import Path

import pytest
import yaml
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage
from tests.helpers import ScriptedLLM, last_tool_return

from helpers import raises_with
from simubench.contracts import api as M
from simubench.engine.edit import Move
from simubench.errors import Invalid
from simubench.server.assistant import compaction
from simubench.server.assistant.guard import LoopGuard, Stuck
from simubench.server.assistant.service import AssistantService
from simubench.server.assistant.store import Conversation, TurnFiles
from simubench.server.assistant.tools import TOOLS, TurnContext, stop_session
from simubench.server.errors import Conflict
from simubench.server.manager import SessionManager

ROOT = Path(__file__).resolve().parents[1]

BENCH = "hello.bench.yaml"
ENV = {"SIMUBENCH_LLM_BASE_URL": "http://llm.test/v1", "SIMUBENCH_LLM_MODEL": "m"}


@pytest.fixture
def manager(tmp_path):
    m = SessionManager(tmp_path, scpi_base_port=None, hislip_port=None, link_port=None)
    m.files.create_hello("ui")
    return m


def service_with(manager, llm: ScriptedLLM) -> AssistantService:
    return AssistantService(manager, model_factory=llm.factory, env=ENV)


async def turn(service: AssistantService, text: str, bench: str = BENCH, stop_consent: bool | None = None):
    started = await service.send(bench, text, stop_consent)
    await service.tasks[bench]
    conv = service.conversation(bench)
    assert conv.turns[-1].id == started.id
    return conv.turns[-1]


def sha(messages) -> str:
    """The bench sha256 the latest bench tool returned."""
    for m in reversed(messages):
        for p in reversed(getattr(m, "parts", [])):
            if isinstance(p, ToolReturnPart) and isinstance(p.content, dict):
                value = p.content.get("bench_sha256") or (
                    p.tool_name.endswith("bench") and p.content.get("sha256")
                )
                if value:
                    return value
    return ""


def edit(ops, path=None):
    def step(messages):
        args = {"base_sha256": sha(messages), "ops": ops}
        if path:
            args["path"] = path
        return [("edit_bench", args)]

    return step


async def test_tool_loop_builds_a_bench_streams_and_summarizes(manager):
    llm = ScriptedLLM(
        [("list_catalog", {"query": "oscilloscope"}), ("read_bench", {})],
        edit(
            [
                {"op": "add_instrument", "name": "scope2", "model": "siglent-sds2104xplus"},
                {"op": "connect", "from": "awg.CH2", "to": "scope2.CH1"},
            ]
        ),
        "已添加示波器 scope2，接在 awg 的 CH2 上。",
    )
    service = service_with(manager, llm)
    t = await turn(service, "再加一台示波器接在 CH2 上")

    assert t.status == "done", t.stop_reason
    assert t.reply == "已添加示波器 scope2，接在 awg 的 CH2 上。"
    # progress lines, not JSON: the instrument's class names the step
    actions = [(s.action, s.subject, s.ok) for s in t.steps]
    assert ("add_instrument", "oscilloscope", True) in actions
    assert ("connect", "awg.CH2 → scope2.CH1", True) in actions
    # the tool call's arguments ride the turn record and its step_done event as a dict (#1694)
    edit_step = next(s for s in t.steps if s.action == "add_instrument")
    assert isinstance(edit_step.args, dict)
    assert edit_step.args["ops"][0]["name"] == "scope2"
    done = [
        e for e in service.logs[BENCH].events if e.type == "step_done" and e.step.action == "add_instrument"
    ]
    assert done and done[0].step.args == edit_step.args
    # "what changed" comes from the file, not from the model's words
    (summary,) = t.files
    assert summary.path == BENCH and not summary.created
    kinds = {(c.kind, c.action, c.target) for c in summary.changes}
    assert ("instrument", "added", "scope2") in kinds
    assert ("wire", "added", "awg.CH2 ↔ scope2.CH1") in kinds
    # written as the agent, in the version history (D35)
    assert manager.files.versions(BENCH)[0].source == "agent"
    # the events the panel followed: text pieces, steps, the turn at the end
    kinds_seen = [e.type for e in service.logs[BENCH].events]
    assert "text" in kinds_seen and "step" in kinds_seen and kinds_seen[-1] == "turn"
    # the history is kept on the server and survives a new service (a reload, another device)
    again = AssistantService(manager, model_factory=llm.factory, env=ENV)
    conv = again.conversation(BENCH)
    assert [x.user for x in conv.turns] == ["再加一台示波器接在 CH2 上"] and conv.configured
    # the next turn sends the earlier messages along
    llm.script.append("好的")
    await turn(again, "谢谢")
    assert any(isinstance(m, ModelResponse) for m in llm.seen[-1][:-1])
    again.clear(BENCH)
    assert again.conversation(BENCH).turns == []


async def test_edit_bench_arguments_are_bounded_in_the_turn_record(manager):
    """#1694: edit_bench may carry a whole bench in its ops; the turn record and its step_done
    event keep what was asked, not unbounded text -- an over-long value is cut and marked."""
    llm = ScriptedLLM(
        [("read_bench", {})],
        edit([{"op": "set_label", "name": "awg", "label": "x" * 5000}]),
        "已加标签。",
    )
    service = service_with(manager, llm)
    t = await turn(service, "给 awg 加个标签")
    assert t.status == "done", t.stop_reason
    (step,) = [s for s in t.steps if s.action == "set_label"]
    label = step.args["ops"][0]["label"]
    assert "…(truncated" in label and len(label) < 1000
    done = [e for e in service.logs[BENCH].events if e.type == "step_done" and e.step.action == "set_label"]
    assert done and done[0].step.args == step.args


async def test_session_summary_comes_from_the_one_projection(tmp_path):
    """#1485: start_session and describe_session return the summary both agent seams share — the
    SCPI socket address included, which the assistant's own copy used to drop (the agent could not
    tell the user how to point a script at the bench)."""
    m = SessionManager(tmp_path, scpi_base_port=0, hislip_port=None)  # fixed ports collide across workers
    m.files.create_hello("ui")
    llm = ScriptedLLM([("start_session", {})], [("describe_session", {})], "已启动。")
    service = service_with(m, llm)
    t = await turn(service, "启动 hello 测试台")
    assert t.status == "done", t.stop_reason
    started = last_tool_return(llm.seen[-1], "start_session")
    assert started["session_id"] in m.sessions and started["bench"] == "hello"
    (awg,) = [i for i in started["instruments"] if i["name"] == "awg"]
    assert awg["scpi_socket"] and awg["scpi_socket"].startswith("TCPIP0::127.0.0.1::")
    assert awg["scpi_socket"].endswith("::SOCKET") and awg["scpi_socket"] == awg["resource_virtual"]
    described = last_tool_return(llm.seen[-1], "describe_session")
    assert described["running"] is True
    assert described["instruments"] == started["instruments"]
    assert {w["from"] for w in described["wires"]} == {"awg.CH1", "awg.CH2"}


async def test_run_cases_takes_builtin_and_model_like_the_mcp_seam(manager):
    """#1485: the assistant seam runs the same cases resolution the acceptance page and the MCP
    seam do: a builtin: name is a shipped manual cases file, and model= runs it on another
    definition (a copy checking itself against the model it extends)."""
    (manager.workspace / "instruments").mkdir()
    (manager.workspace / "instruments" / "my-keysight-34465a.yaml").write_text(
        yaml.safe_dump(
            {
                "schema": "simubench.instrument/2",
                "id": "my-keysight-34465a",
                "extends": "keysight-34465a",
                "command_overrides": {"[:SENSe]:VOLTage[:DC]:IMPedance:AUTO": None},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    impedance = "builtin:keysight-34465a-impedance/cases.yaml"
    llm = ScriptedLLM([("run_cases", {"path": impedance})], "型号本身两例都过。")
    service = service_with(manager, llm)
    t = await turn(service, "跑一遍阻抗手测用例")
    assert t.status == "done", t.stop_reason
    base = last_tool_return(llm.seen[-1], "run_cases")
    assert (base["passed"], base["failed"]) == (2, 0)
    llm.script += [
        [("run_cases", {"path": impedance, "model": "my-keysight-34465a"})],
        "副本上两例都挂。",
    ]
    t2 = await turn(service, "再在我的副本上跑一遍")
    assert t2.status == "done", t2.stop_reason
    copy = last_tool_return(llm.seen[-1], "run_cases")
    assert (copy["passed"], copy["failed"]) == (0, 2)


async def test_running_bench_allows_settings_only_and_stopping_needs_consent(manager):
    await manager.start(BENCH)
    llm = ScriptedLLM(
        [("read_bench", {})],
        edit([{"op": "remove", "name": "sa"}]),
        edit([{"op": "set_state", "instrument": "awg", "path": "ch1.frequency", "value": 2e6}]),
        [("set_live_setting", {"instrument": "awg", "path": "ch1.frequency", "value": 2e6})],
        [("stop_session", {})],
        [("ask_to_stop_session", {"reason": "要删除频谱分析仪"})],
        "要删除频谱分析仪需要先停止测试台，可以停止吗？",
    )
    service = service_with(manager, llm)
    t = await turn(service, "把频谱仪删掉，频率改成 2 MHz")
    assert t.status == "done"
    results = {s.action: s for s in t.steps}
    assert results["remove"].ok is False and "运行中只能调参数" in results["remove"].error
    assert results["set_state"].ok is True
    assert results["set_live_setting"].ok is True
    assert results["set_live_setting"].args == {"i": "awg", "p": "ch1.frequency", "v": 2e6}  # #1679
    served = next(iter(manager.sessions.values()))
    assert served.session.instrument("awg").state["ch1.frequency"] == pytest.approx(2e6)
    # the file edit says it only changed the initial value; the live change is listed separately
    edited = last_tool_return(llm.seen[-1], "edit_bench")
    assert any("只改了文件里的初值" in note for note in edited["notes"])
    (live,) = t.live
    assert (live.kind, live.target) == ("state", "awg.ch1.frequency")
    assert live.old == pytest.approx(1e6) and live.new == pytest.approx(2e6)
    ret = last_tool_return(llm.seen[-1], "set_live_setting")
    assert ret["ok"] is True and ret["changed"] is True  # #1678
    # stopping in the same turn as asking: refused (the user has not answered)
    assert results["stop_session"].ok is False and "征得用户同意" in results["stop_session"].error
    assert manager.sessions
    # the question is on the turn, for the panel's agree / decline buttons (#902)
    assert t.stop_question == "要删除频谱分析仪"

    llm.script += [[("stop_session", {})], edit([{"op": "remove", "name": "sa"}]), "已停止并删除。"]
    t2 = await turn(service, "同意停止", stop_consent=True)
    assert t2.status == "done" and t2.stop_consent is True
    assert not manager.sessions
    assert [s.ok for s in t2.steps] == [True, True]
    assert "sa" not in yaml.safe_load((manager.workspace / BENCH).read_text(encoding="utf-8"))["instruments"]
    # the model is told the consent in words (#1707): otherwise it may not even try stop_session
    assert any("本轮可以直接调用 stop_session" in str(m) for m in llm.seen[-1])


async def test_set_live_setting_tells_the_model_when_the_value_was_already_there(manager):
    """#1678: an equal-value write is ok but not changed, with a note against retrying; the turn's
    live list stays empty (the write_tx no-op rule, made visible)."""
    await manager.start(BENCH)
    llm = ScriptedLLM(
        [("set_live_setting", {"instrument": "awg", "path": "ch1.frequency", "value": 1e6})],
        "频率本来就是 1 MHz。",
    )
    t = await turn(service_with(manager, llm), "把频率设成 1 MHz")
    assert t.status == "done", t.stop_reason
    assert t.live == []
    ret = last_tool_return(llm.seen[-1], "set_live_setting")
    assert ret["ok"] is True and ret["changed"] is False
    assert "本来就是该值" in ret["note"] and "重试" in ret["note"]


async def test_stopping_needs_the_users_button_in_the_very_next_turn(manager):
    """#902: the server, not the model's reading of a reply, decides. A decline, a typed reply and
    an agreement a turn too late all leave the bench running; the agreement right after works."""
    await manager.start(BENCH)
    ask = [("ask_to_stop_session", {"reason": "要删除频谱分析仪"})]
    stop = [("stop_session", {})]
    llm = ScriptedLLM(ask, "可以停止吗？", stop, "好的，不停止。")
    service = service_with(manager, llm)
    await turn(service, "把频谱仪删掉")
    # declined with the button: refused
    declined = await turn(service, "不停止", stop_consent=False)
    assert declined.stop_consent is False
    assert declined.steps[0].ok is False and "不停止" in declined.steps[0].error
    assert manager.sessions
    # asked again, answered by typing: no agreement
    llm.script += [ask, "可以停止吗？", stop, "需要你点同意。"]
    await turn(service, "把频谱仪删掉")
    typed = await turn(service, "可以，停止吧")
    assert typed.stop_consent is None
    assert typed.steps[0].ok is False and "文字回复不算同意" in typed.steps[0].error
    assert manager.sessions
    # asked, a turn in between, then the button: the request has lapsed
    llm.script += [ask, "可以停止吗？", "频谱仪在 RF_IN 上。", stop, "还需要再问一次。"]
    await turn(service, "把频谱仪删掉")
    await turn(service, "频谱仪接在哪？")
    late = await turn(service, "同意停止", stop_consent=True)
    assert late.stop_consent is None  # no request pending: the button carries no answer
    assert late.steps[0].ok is False and "征得用户同意" in late.steps[0].error
    assert manager.sessions
    # asked, then agreed with the button right after: stopped
    llm.script += [ask, "可以停止吗？", stop, "已停止。"]
    await turn(service, "把频谱仪删掉")
    agreed = await turn(service, "同意停止", stop_consent=True)
    assert agreed.steps[0].ok is True and not manager.sessions


def a_turn(n: int) -> M.AssistantTurn:
    """A turn record for the criterion tests: the store layer needs no service and no LLM."""
    return M.AssistantTurn(id=f"turn-{n}", user=f"第 {n} 句", started_at="2026-01-01T00:00:00Z")


def test_stop_consent_is_alive_for_the_next_turn_only():
    """#902 as one criterion on the conversation (#1726): asked in turn n, alive for turn n+1 and
    nothing else — not in the turn it was asked (the user has not answered yet), not a turn later
    (lapsed) — and without a request on record nothing is alive."""
    conv = Conversation(BENCH, [a_turn(0), a_turn(1)])
    conv.stop_requested_in = 0
    assert conv.stop_consent_alive(1)
    assert not conv.stop_consent_alive(0)
    assert not conv.stop_consent_alive(2)
    assert not Conversation(BENCH).stop_consent_alive(1)


def test_sending_answers_one_request_and_forgets_a_stale_one():
    """The sending side of the criterion (#902, #1726): a request asked in the last recorded turn
    is pending and stays on record; a stale one is forgotten here, so it cannot answer a later
    message, and without a request nothing is pending either."""
    conv = Conversation(BENCH, [a_turn(0), a_turn(1), a_turn(2)])
    conv.stop_requested_in = 2
    assert conv.stop_consent_pending() is True
    assert conv.stop_requested_in == 2
    conv.stop_requested_in = 1
    assert conv.stop_consent_pending() is False
    assert conv.stop_requested_in is None
    assert Conversation(BENCH).stop_consent_pending() is False


async def test_stop_session_gate_asks_the_same_criterion(manager):
    """The ``stop_session`` gate without a whole turn (#1726): no request, the same turn as the
    ask and a lapsed one all refuse in the words the model acts on; a live consent still needs the
    panel's answer (a typed reply and a decline are no), and with it the bench stops and the
    request is spent."""
    await manager.start(BENCH)
    served_id = next(iter(manager.sessions))
    service = AssistantService(manager, model_factory=lambda _settings: None)
    conv = Conversation(BENCH, [a_turn(0)])

    async def gate(ask: int | None, consent: bool | None) -> dict:
        turn = a_turn(len(conv.turns))
        turn.stop_consent = consent
        conv.turns.append(turn)
        conv.stop_requested_in = ask
        ctx = TurnContext(service, conv, turn, TurnFiles(), lambda _event: None)
        return await stop_session(RunContext(deps=ctx, model=TestModel(), usage=RunUsage()))

    no_request = await gate(None, True)  # turn 1: nothing on record, the button carries no answer
    assert "征得用户同意" in no_request["error"]
    same_turn = await gate(2, True)  # turn 2: asked in this very turn, no answer yet
    assert "征得用户同意" in same_turn["error"]
    lapsed = await gate(1, True)  # turn 3: asked two turns ago
    assert "征得用户同意" in lapsed["error"]
    typed = await gate(3, None)  # turn 4: asked in the previous turn, answered by typing
    assert "文字回复不算同意" in typed["error"]
    declined = await gate(4, False)  # turn 5: asked in the previous turn, declined
    assert "不停止" in declined["error"]
    assert manager.sessions  # every refusal above left the bench running

    agreed = await gate(5, True)  # turn 6: asked in the previous turn, agreed on the panel
    assert agreed == {"stopped": True, "session_id": served_id}
    assert not manager.sessions and conv.stop_requested_in is None
    again = await gate(6, True)  # turn 7: consent alive but nothing is running any more
    assert again == {"stopped": False, "note": "测试台已经停止"}
    assert conv.stop_requested_in is None


async def test_the_tools_cannot_delete_files_or_control_the_simulation(manager):
    names = {f.__name__ for f in TOOLS}
    assert not names & {
        "delete_bench",
        "delete_definition",
        "inject_fault",
        "advance_clock",
        "set_clock_mode",
        "read_true_values",
        "send_scpi",
    }
    llm = ScriptedLLM(
        [("read_definition", {"definition_id": "siglent-sdg2042x"})],
        lambda m: [
            (
                "edit_definition",
                {
                    "definition_id": "siglent-sdg2042x",
                    "base_sha256": last_tool_return(m, "read_definition")["sha256"],
                    "patch": [{"op": "replace", "path": "/model", "value": "X"}],
                },
            )
        ],
        "内置型号不能改。",
    )
    t = await turn(service_with(manager, llm), "改内置型号")
    step = next(s for s in t.steps if s.action == "edit_definition")
    assert step.ok is False and "not in the workspace" in step.error
    assert not (manager.workspace / "instruments").exists()


async def test_self_repair_until_the_bench_passes_and_spinning_is_stopped(manager):
    bad = {"op": "set_state", "instrument": "awg", "path": "ch1.nonsense", "value": 1}
    fix = {"op": "set_state", "instrument": "awg", "path": "ch1.nonsense", "value": None}
    llm = ScriptedLLM([("read_bench", {})], edit([bad]), "改好了", edit([fix]), "修好了")
    service = service_with(manager, llm)
    t = await turn(service, "设置一个参数")
    # the first answer came while the bench failed its checks: the model was told and fixed it
    assert t.status == "done" and t.reply.endswith("修好了")
    feedback = str(llm.seen[3][-1].parts[-1].content)
    assert "没有通过校验" in feedback and "E-STATE-UNKNOWN" in feedback
    assert manager.files.read(BENCH).ok

    # the same failing edit again and again: no progress, the server stops the turn and says why
    spin = ScriptedLLM([("read_bench", {})], edit([{"op": "remove", "name": "ghost"}]))
    t2 = await turn(service_with(manager, spin), "删掉 ghost")
    assert t2.status == "stuck"
    assert "没有进展" in t2.stop_reason and "ghost" in t2.stop_reason


async def test_a_long_loop_that_makes_progress_is_not_interrupted(manager):
    steps = [[("read_bench", {})]] + [
        edit([{"op": "set_state", "instrument": "awg", "path": "ch1.frequency", "value": 1e6 + i}])
        for i in range(12)
    ]
    t = await turn(service_with(manager, ScriptedLLM(*steps, "完成")), "逐步调频率")
    assert t.status == "done" and len(t.steps) == 13 and all(s.ok for s in t.steps)

    # reading the same unchanged bench again with other calls between is not spinning (#903):
    # look-ups are not progress, but neither are they a repeated change
    checks = [[("read_bench", {})], [("list_catalog", {"query": "oscilloscope"})]] * 3
    t2 = await turn(service_with(manager, ScriptedLLM(*checks, "核对完了")), "反复核对")
    assert t2.status == "done", t2.stop_reason
    assert len(t2.steps) == 6 and all(s.ok for s in t2.steps)


_READ = ("read_bench", {"p": "a"}, True, "x")
_MUT = ("set_live_setting", {"i": "awg", "p": "ch1.output", "v": False}, True, {"ok": True, "value": False})
_MUT2 = ("set_live_setting", {"i": "awg", "p": "ch1.output", "v": True}, True, {"ok": True, "value": True})
_FAIL = ("edit", {"n": 0}, False, "没有 ghost")


def _edit(n: int):
    return ("edit", {"n": n}, True, "ok")


def _fail(n: int):
    return ("edit", {"n": n}, False, "没有 ghost")


@pytest.mark.parametrize(
    ("calls", "stuck"),
    [
        pytest.param([_READ, _edit(0), _READ, _edit(1), _READ, _edit(2)], None, id="read-between-edits"),
        pytest.param(
            [_READ, ("other", {}, True, "y"), _READ, _edit(0), _READ], None, id="read-between-others"
        ),
        pytest.param([_READ, _READ, _edit(0), _READ, _READ], None, id="a-new-call-starts-the-count-over"),
        pytest.param(
            [_fail(0), _fail(1), _fail(2), _edit(3), _fail(4)], None, id="a-success-ends-the-errors"
        ),
        pytest.param([_READ, _READ, _READ], "同一个操作（read_bench）连续 3 次", id="three-in-a-row"),
        pytest.param([_edit(0), _FAIL, _FAIL, _FAIL], "连续 3 次得到同样的结果", id="same-failing-call"),
        pytest.param(
            [_fail(0), _fail(1), _fail(2), _fail(3)], "连续 4 次遇到同一个错误", id="four-same-errors"
        ),
        pytest.param(
            [_MUT, _READ, _MUT, _READ, _MUT], "在一轮里重复 3 次", id="lookups-between-the-same-change"
        ),
        pytest.param(
            [_MUT, _MUT2, _READ, _MUT, _MUT2, _READ, _MUT],
            "在一轮里重复 3 次",
            id="a-b-a-b-between-lookups",
        ),
    ],
)
def test_the_loop_guard_stops_repeats_that_hide_behind_lookups(calls, stuck):
    guard = LoopGuard()
    if stuck is None:
        for call in calls:
            guard.record(*call)
        return
    for call in calls[:-1]:
        guard.record(*call)
    with raises_with(Stuck, stuck) as caught:
        guard.record(*calls[-1])
    assert "没有进展" in str(caught.value)


async def test_stop_ends_a_running_turn_and_keeps_what_it_wrote(manager):
    gate = asyncio.Event()

    async def slow(messages):
        return "never"

    llm = ScriptedLLM(
        [("read_bench", {})], edit([{"op": "set_label", "name": "awg", "label": "信号源"}]), "x"
    )
    original = llm._stream

    async def stream(messages, info):
        if len(llm.seen) == 2:  # after the edit: hang until stopped
            llm.seen.append(list(messages))
            gate.set()
            await asyncio.sleep(3600)
        async for piece in original(messages, info):
            yield piece

    llm.model._stream_function = stream  # type: ignore[attr-defined]
    llm.model.stream_function = stream  # type: ignore[attr-defined]
    service = service_with(manager, llm)
    await service.send(BENCH, "改个标签")
    await asyncio.wait_for(gate.wait(), 10)
    assert service.conversation(BENCH).running
    assert await service.stop(BENCH)
    t = service.conversation(BENCH).turns[-1]
    assert t.status == "stopped" and t.stop_reason
    assert t.files and t.files[0].changes  # the label it wrote is listed, restorable
    assert service.rollback_plan(BENCH).turn_id == t.id


async def test_a_conflict_with_the_canvas_makes_the_assistant_read_again(manager):
    def canvas_edits_meanwhile(messages):
        doc = manager.files.read(BENCH)
        manager.files.edit(BENCH, doc.sha256, [Move(name="awg", x=5, y=5)], "ui")
        return edit([{"op": "set_label", "name": "sa", "label": "SA"}])(messages)

    llm = ScriptedLLM(
        [("read_bench", {})],
        canvas_edits_meanwhile,
        [("read_bench", {})],
        edit([{"op": "set_label", "name": "sa", "label": "SA"}]),
        "改好了",
    )
    t = await turn(service_with(manager, llm), "给频谱仪改标签")
    first, again = (s for s in t.steps if s.action == "set_label")
    assert first.ok is False and "重新读取" in first.error
    assert again.ok is True and t.status == "done"
    data = manager.files.read(BENCH).data
    assert data["layout"]["awg"] == {"x": 5, "y": 5} and data["instruments"]["sa"]["label"] == "SA"


def test_the_assistant_error_text_reads_the_shared_table():
    """The assistant's wording for a failed tool is the one table's assistant column (#1382), not a
    private copy: a Conflict says the same on the MCP seam and carries the sha."""
    from simubench.server.assistant.tools import _error_text
    from simubench.server.errors import tool_text

    conflict = Conflict("agent.bench.yaml changed since it was read", "abc")
    assert _error_text(conflict) == tool_text(conflict, "assistant")
    assert "重新读取" in _error_text(conflict) and "sha256 = abc" in _error_text(conflict)
    missing = FileNotFoundError("no such bench")
    missing.filename = "agent.bench.yaml"
    assert _error_text(missing) == "文件不存在：agent.bench.yaml"  # the name survives, for a retry


async def test_a_bug_in_the_model_stream_ends_the_turn_as_an_internal_error(manager, caplog):
    """#1725: an exception that is no domain error and no LLM service failure — the shape a bug
    has — closes the turn in words that never say the LLM service failed (retrying a service
    cannot fix a bug), and ``log.exception`` keeps the stack in the log."""
    llm = ScriptedLLM([("read_bench", {})])

    async def broken(messages, info):
        raise RuntimeError("engine blew up")
        yield  # an async generator: the raise lands before the first piece

    llm.model._stream_function = broken  # type: ignore[attr-defined]
    llm.model.stream_function = broken  # type: ignore[attr-defined]
    service = service_with(manager, llm)
    with caplog.at_level(logging.ERROR):
        t = await turn(service, "看看测试台")
    assert t.status == "error" and t.finished_at  # the state machine still closes the turn
    assert "LLM 服务" not in (t.stop_reason or "") and "内部出错" in (t.stop_reason or "")
    assert service.conversation(BENCH).turns[-1].status == "error"  # persisted
    failed = [r for r in caplog.records if "assistant turn failed" in r.getMessage()]
    assert failed and all(r.exc_info for r in failed)  # the stack survives in the log


async def test_an_llm_service_failure_still_reads_as_one(manager):
    """#1725: a failure of the LLM service's own kinds — pydantic_ai's ModelHTTPError, the form
    the SDKs' answers arrive translated in — keeps llm.py's human wording at the turn boundary."""
    llm = ScriptedLLM([("read_bench", {})])

    async def denied(messages, info):
        raise ModelHTTPError(status_code=401, model_name="m", body="bad key")
        yield  # an async generator: the raise lands before the first piece

    llm.model._stream_function = denied  # type: ignore[attr-defined]
    llm.model.stream_function = denied  # type: ignore[attr-defined]
    service = service_with(manager, llm)
    t = await turn(service, "看看测试台")
    assert t.status == "error" and t.finished_at
    assert "LLM 服务拒绝了请求（401）" in (t.stop_reason or "")


async def test_a_broken_conversation_file_is_a_domain_error_and_is_never_emptied(manager):
    """#1725: a conversation file a reload truncated or a hand edit broke answers ``Invalid`` with
    the position (a 422 at every seam), and is kept — never silently turned into an empty one."""
    folder = manager.workspace / ".simubench" / "assistant"
    folder.mkdir(parents=True)
    path = folder / f"{BENCH}.json"
    path.write_text('{"turns": [', encoding="utf-8")
    service = service_with(manager, ScriptedLLM("好的"))
    with raises_with(Invalid, f"{BENCH}.json", "line 1 column"):
        service.conversation(BENCH)
    assert path.is_file()  # the scene is kept for the user to fix
    with raises_with(Invalid, "line 1 column"):  # a new message refuses too, in the same words
        await service.send(BENCH, "继续")
    assert path.is_file()


@pytest.mark.parametrize(
    "body",
    ['{"files": {"a": 5}}', '{"files": [1]}', '{"turns": 3}', '{"messages": 3}'],
)
async def test_a_conversation_file_shaped_wrong_is_a_domain_error_and_is_never_emptied(manager, body):
    """#1738: valid JSON with the wrong shape (a hand edit) answers the same ``Invalid`` with the
    file's name as a syntax error does — a 422 at every seam, never a 500 — and is kept."""
    folder = manager.workspace / ".simubench" / "assistant"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{BENCH}.json"
    path.write_text(body, encoding="utf-8")
    service = service_with(manager, ScriptedLLM("好的"))
    with raises_with(Invalid, f"{BENCH}.json"):
        service.conversation(BENCH)
    assert path.is_file()  # the scene is kept for the user to fix
    with raises_with(Invalid, f"{BENCH}.json"):  # a new message refuses too, in the same words
        await service.send(BENCH, "继续")
    assert path.is_file()


async def test_a_bench_broken_by_hand_stops_the_rollback_plan_in_words(manager):
    """#1725: the rollback plan reads the current files with the bench files' parser; a file
    broken by hand answers ``Invalid`` with the parser's position, not the 500 a bug gets."""
    llm = ScriptedLLM(
        [("read_bench", {})], edit([{"op": "set_label", "name": "awg", "label": "X"}]), "改好了"
    )
    service = service_with(manager, llm)
    t = await turn(service, "改标签")
    assert service.rollback_plan(BENCH).turn_id == t.id
    (manager.workspace / BENCH).write_text("\tname: x", encoding="utf-8")  # a tab, as a hand edit could leave
    with raises_with(Invalid, BENCH, "line 1"):
        service.rollback_plan(BENCH)


async def test_a_bench_that_does_not_parse_reaches_the_model_as_an_error(manager):
    """#1406: the assistant's read_bench answers a broken file as ``{"error": ...}`` with the
    parser's position, instead of an exception escaping the tool loop."""
    (manager.files.workspace / "bad.bench.json").write_text("{not json", encoding="utf-8")
    llm = ScriptedLLM([("read_bench", {"path": "bad.bench.json"})], "看过了")
    t = await turn(service_with(manager, llm), "看看这个测试台")
    assert t.status == "done"
    outcome = last_tool_return(llm.seen[-1], "read_bench")
    assert "bad.bench.json" in outcome["error"] and "line 1 column 2" in outcome["error"]


async def test_long_conversations_are_compacted_without_splitting_tool_calls(manager):
    models = ["bk-8614", "fluke-5522a", "fluke-5730a", "fluke-8588a", "keysight-33622a", "keysight-34465a"]
    models += ["keysight-34970a", "keysight-53220a", "keysight-53230a", "keysight-b2901a"]
    llm = ScriptedLLM(*[[("get_definition", {"definition_id": d})] for d in models], "完成")
    # the configured context length (#872) decides when to compact
    service = AssistantService(
        manager, model_factory=llm.factory, env={**ENV, "SIMUBENCH_LLM_CONTEXT_TOKENS": "3000"}
    )
    t = await turn(service, "看看这个测试台")
    assert t.status == "done", t.stop_reason
    # the conversation went past the window and was compacted, the panel was told
    assert service.conversation(BENCH).compacted or t.compacted
    assert any(e.type == "compacted" for e in service.logs[BENCH].events)
    for messages in llm.seen:
        calls = {
            p.tool_call_id
            for m in messages
            if isinstance(m, ModelResponse)
            for p in m.parts
            if isinstance(p, ToolCallPart)
        }
        returns = {
            p.tool_call_id
            for m in messages
            if isinstance(m, ModelRequest)
            for p in m.parts
            if isinstance(p, ToolReturnPart)
        }
        assert returns <= calls  # every tool result still has its call
    assert any(
        isinstance(p, ToolReturnPart) and p.content == compaction.CLEARED
        for messages in llm.seen
        for m in messages
        for p in getattr(m, "parts", [])
    )


def test_compaction_keeps_recent_turns_and_drops_whole_old_ones():
    from pydantic_ai.messages import TextPart, UserPromptPart

    def turn_messages(i: int):
        return [
            ModelRequest(parts=[UserPromptPart(content=f"q{i}")]),
            ModelResponse(parts=[ToolCallPart(tool_name="t", args={}, tool_call_id=f"c{i}")]),
            ModelRequest(parts=[ToolReturnPart(tool_name="t", content="x" * 3000, tool_call_id=f"c{i}")]),
            ModelResponse(parts=[TextPart(content=f"a{i}")]),
        ]

    messages = [m for i in range(10) for m in turn_messages(i)]
    out, changed = compaction.compact(messages, budget=4000)
    assert changed
    assert compaction.estimate_tokens(out) < compaction.estimate_tokens(messages)
    # the last turns are whole, results kept
    assert out[-4].parts[0].content == "q9" and out[-2].parts[0].content == "x" * 3000
    # the conversation still starts with a user message, which says older content was compacted
    assert isinstance(out[0], ModelRequest) and out[0].parts[0].content == compaction.DROPPED
    # that first user message is the conversation's own opening turn, never dropped (#1708):
    # the task and what the user asked to remember live there
    assert out[0].parts[1].content == "q0"
    calls = {p.tool_call_id for m in out for p in m.parts if isinstance(p, ToolCallPart)}
    returns = {p.tool_call_id for m in out for p in m.parts if isinstance(p, ToolReturnPart)}
    assert calls == returns
    small, unchanged = compaction.compact(messages[:4], budget=100_000)
    assert not unchanged and small == messages[:4]


async def test_copy_bench_needs_no_rewiring_and_addresses_are_freed(manager):
    llm = ScriptedLLM(
        [("copy_bench", {"name": "hello 2"})],
        lambda m: edit(
            [
                {"op": "set_address", "instrument": "awg", "address": "GPIB0::5::INSTR"},
                {
                    "op": "add_instrument",
                    "name": "awg2",
                    "model": "siglent-sdg2042x",
                    "address": "GPIB0::5::INSTR",
                },
            ],
            path="hello-copy.bench.yaml",
        )(m),
        "复制好了，并加了一台信号源。",
    )
    t = await turn(service_with(manager, llm), "复制这个测试台")
    assert t.status == "done", t.stop_reason
    assert all(s.ok for s in t.steps), t.steps
    copied = manager.files.read("hello-copy.bench.yaml")
    original = manager.files.read(BENCH)
    assert copied.data["wires"] == original.data["wires"] and copied.data["name"] == "hello 2"
    assert copied.data["instruments"]["awg2"]["address"] == "GPIB0::6::INSTR"
    notes = last_tool_return(llm.seen[-1], "edit_bench")["notes"]
    assert notes and "GPIB0::6::INSTR" in notes[0]
    created = next(f for f in t.files if f.path == "hello-copy.bench.yaml")
    assert created.created


async def test_templates_need_the_users_example_before_commands_change(manager):
    def read_bench_sha(m):
        return [("save_template", {"instrument": "awg", "name": "My AWG", "base_sha256": sha(m)})]

    llm = ScriptedLLM([("read_bench", {})], read_bench_sha, "已保存为我的模板 My AWG。")
    service = service_with(manager, llm)
    t = await turn(service, "把信号源存成我的模板，叫 My AWG")
    assert t.status == "done", t.stop_reason
    new_id = last_tool_return(llm.seen[-1], "save_template")["definition_id"]
    assert new_id == "my-my-awg"
    saved = manager.files.read(BENCH).data["instruments"]["awg"]
    assert saved["model"] == new_id
    stored = yaml.safe_load(
        (manager.workspace / "instruments" / f"{new_id}.yaml").read_text(encoding="utf-8")
    )
    assert "extends" not in stored
    assert stored["channel_state"]["frequency"]["default"] == 1e6  # both channels set it: a default
    assert {f.path for f in t.files} == {BENCH, f"instruments/{new_id}.yaml"}

    patch = [{"op": "add", "path": "/errors", "value": {"-399": "Custom"}}]

    def edit_commands(example):
        def step(m):
            return [
                (
                    "edit_definition",
                    {
                        "definition_id": new_id,
                        "base_sha256": last_tool_return(m, "read_definition")["sha256"],
                        "patch": patch,
                        **({"user_example": example} if example else {}),
                    },
                )
            ]

        return step

    asks = ScriptedLLM(
        [("read_definition", {"definition_id": new_id})],
        edit_commands(None),
        "要加错误码，请给出错误码和文字的示例。",
    )
    t2 = await turn(service_with(manager, asks), "加一个错误码")
    refused = next(s for s in t2.steps if s.action == "edit_definition")
    assert refused.ok is False and "不要猜" in refused.error

    does = ScriptedLLM(
        [("read_definition", {"definition_id": new_id})],
        edit_commands('-399,"Custom"'),
        [("validate_definition", {"definition_id": new_id})],
        [("try_definition", {"definition_id": new_id, "commands": ["*IDN?"]})],
        "发送 *IDN? 回答了标识。",
    )
    t3 = await turn(service_with(manager, does), '错误码 -399,"Custom"')
    assert t3.status == "done", t3.stop_reason
    assert all(s.ok for s in t3.steps), [s.error for s in t3.steps]
    tried = last_tool_return(does.seen[-1], "try_definition")["results"][0]
    assert tried["command"] == "*IDN?" and tried["response"]


def test_a_summary_kind_missing_from_the_record_is_refused(manager):
    """#1729: ``before`` and ``kinds`` are written together (tools.touch, the template save); a
    record that lost the entry is damaged — the domain error in words, never the silent "bench"
    the old fallback read."""
    service = AssistantService(manager)
    with raises_with(Invalid, BENCH):
        service._summarize(
            M.AssistantTurn(id="t1", user="x", started_at="x"), TurnFiles(before={BENCH: "0" * 64})
        )


def test_the_service_holds_no_kind_dispatch_the_facade_owns_it():
    """#1729: the six hand-written bench/definition branches are gone from the service; the kind
    dispatch lives in FileStores, and the record's missing kind is never silently "bench"."""
    source = (ROOT / "src/simubench/server/assistant/service.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Compare)
            and len(node.ops) == 1
            and isinstance(node.ops[0], (ast.Eq, ast.NotEq))
        ):
            sides = [node.left, *node.comparators]
            names = [
                s
                for s in sides
                if (isinstance(s, ast.Name) and s.id == "kind")
                or (isinstance(s, ast.Attribute) and s.attr == "kind")
            ]
            literals = [
                s for s in sides if isinstance(s, ast.Constant) and s.value in ("bench", "definition")
            ]
            assert not (names and literals), ast.unparse(node)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "kinds"
        ):
            assert not any(isinstance(a, ast.Constant) and a.value == "bench" for a in node.args), (
                ast.unparse(node)
            )
