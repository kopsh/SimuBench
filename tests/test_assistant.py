"""The AI assistant's conversation backend with a scripted LLM (#873, #874, #875, D66): the tool
loop, streaming, the server-side rules, self-repair, the loop guard, stop, concurrency with the
canvas, compaction and the conversation history. Each test walks one flow (D49)."""

from __future__ import annotations

import asyncio

import pytest
import yaml
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from tests.helpers import ScriptedLLM, last_tool_return

from helpers import raises_with
from simubench.assistant import compaction
from simubench.assistant.guard import LoopGuard, Stuck
from simubench.assistant.service import AssistantService
from simubench.assistant.tools import TOOLS
from simubench.engine.edit import Move
from simubench.server.manager import SessionManager

BENCH = "hello.bench.yaml"
ENV = {"SIMUBENCH_LLM_BASE_URL": "http://llm.test/v1", "SIMUBENCH_LLM_MODEL": "m"}


@pytest.fixture
def manager(tmp_path):
    m = SessionManager(tmp_path, scpi_base_port=None, hislip_port=None, link_port=None)
    m.files.create_hello("ui")
    return m


def service_with(manager, llm: ScriptedLLM) -> AssistantService:
    return AssistantService(manager, model_factory=llm.factory, env=ENV)


async def turn(service: AssistantService, text: str, bench: str = BENCH):
    started = await service.send(bench, text)
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
    served = next(iter(manager.sessions.values()))
    assert served.session.instrument("awg").state["ch1.frequency"] == pytest.approx(2e6)
    # stopping in the same turn as asking: refused (the user has not answered)
    assert results["stop_session"].ok is False and "征得用户同意" in results["stop_session"].error
    assert manager.sessions

    llm.script += [[("stop_session", {})], edit([{"op": "remove", "name": "sa"}]), "已停止并删除。"]
    t2 = await turn(service, "可以，停止吧")
    assert t2.status == "done"
    assert not manager.sessions
    assert [s.ok for s in t2.steps] == [True, True]
    assert "sa" not in yaml.safe_load((manager.workspace / BENCH).read_text(encoding="utf-8"))["instruments"]


async def test_the_tools_cannot_delete_files_or_control_the_simulation(manager):
    names = {f.__name__ for f in TOOLS}
    assert not names & {
        "delete_bench",
        "delete_definition",
        "inject_fault",
        "advance_clock",
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
    # only repeats in a row count
    checks = [[("read_bench", {})], [("list_catalog", {"query": "oscilloscope"})]] * 3
    t2 = await turn(service_with(manager, ScriptedLLM(*checks, "核对完了")), "反复核对")
    assert t2.status == "done", t2.stop_reason
    assert len(t2.steps) == 6 and all(s.ok for s in t2.steps)


_READ = ("read_file", {"p": "a"}, True, "x")
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
        pytest.param([_READ, _READ, _READ], "同一个操作（read_file）连续 3 次", id="three-in-a-row"),
        pytest.param([_edit(0), _FAIL, _FAIL, _FAIL], "连续 3 次得到同样的结果", id="same-failing-call"),
        pytest.param(
            [_fail(0), _fail(1), _fail(2), _fail(3)], "连续 4 次遇到同一个错误", id="four-same-errors"
        ),
    ],
)
def test_the_loop_guard_stops_only_repeats_in_a_row(calls, stuck):
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
