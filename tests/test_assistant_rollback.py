"""“Restore to before the last turn” (#876, D66): files the last turn changed go back, files it
created are deleted (the user's action, never a tool), changes made after the turn are listed and
need consent, running sessions are stopped first with consent, and the conversation goes on."""

from __future__ import annotations

import pytest
import yaml
from tests.helpers import ScriptedLLM, last_tool_return
from tests.test_assistant import BENCH, edit, manager, service_with, sha, turn

from simubench.assistant.service import ROLLED_BACK_NOTE
from simubench.engine.edit import SetLabel
from simubench.library.loader import load_yaml
from simubench.models import api as M
from simubench.server.benchfiles import Conflict

__all__ = ["manager"]


def label(manager, name: str = "awg"):
    return yaml.safe_load((manager.workspace / BENCH).read_text(encoding="utf-8"))["instruments"][name].get(
        "label"
    )


async def test_restore_a_changed_file_after_confirming_what_is_lost_then_talk_on(manager):
    llm = ScriptedLLM(
        [("read_bench", {})], edit([{"op": "set_label", "name": "awg", "label": "AI 改的"}]), "改好了"
    )
    service = service_with(manager, llm)
    t = await turn(service, "改标签")
    assert label(manager) == "AI 改的"

    plan = service.rollback_plan(BENCH)
    assert plan.turn_id == t.id
    assert [(f.path, f.action, f.modified_since) for f in plan.files] == [(BENCH, "restore", False)]

    # the user then changes the file by hand on the canvas: restoring would lose that
    doc = manager.files.read(BENCH)
    manager.files.edit(BENCH, doc.sha256, [SetLabel(name="sa", label="手改的")], "ui")
    plan = service.rollback_plan(BENCH)
    (f,) = plan.files
    assert f.modified_since and [(c.target, c.new) for c in f.lost] == [("sa", "手改的")]
    asked = await service.rollback(M.RollbackRequest(bench=BENCH, turn_id=t.id))
    assert not asked.done and asked.needs == "confirm_lost"
    assert label(manager, "sa") == "手改的"  # nothing restored without consent

    done = await service.rollback(M.RollbackRequest(bench=BENCH, turn_id=t.id, confirm_lost=True))
    assert done.done
    assert label(manager) == "函数发生器" and label(manager, "sa") == "频谱分析仪"
    # the restore is itself a version in the history
    assert manager.files.versions(BENCH)[0].source == "restore"
    # the button goes away: nothing more to restore, and an old turn id is refused
    assert service.rollback_plan(BENCH).turn_id is None
    assert service.conversation(BENCH).turns[-1].rolled_back
    with pytest.raises(Conflict):
        await service.rollback(M.RollbackRequest(bench=BENCH, turn_id=t.id, confirm_lost=True))

    # the conversation goes on, and the model is told the files were restored
    llm.script.append("好的")
    t2 = await turn(service, "继续")
    assert t2.status == "done"
    assert ROLLED_BACK_NOTE in str(llm.seen[-1][-1].parts[0].content)


async def test_files_the_turn_created_are_deleted_benches_first_no_dangling_template(manager):
    llm = ScriptedLLM(
        [("copy_bench", {})],
        [("read_bench", {})],
        lambda m: [("save_template", {"instrument": "scope", "name": "Scope A", "base_sha256": sha(m)})],
        "复制并保存了模板。",
    )
    service = service_with(manager, llm)
    original = (manager.workspace / BENCH).read_bytes()
    t = await turn(service, "复制测试台，把示波器存成模板")
    assert t.status == "done", t.stop_reason
    template = manager.workspace / "instruments" / "my-scope-a.yaml"
    assert template.is_file() and (manager.workspace / "hello-copy.bench.yaml").is_file()

    plan = service.rollback_plan(BENCH)
    assert [(f.path, f.action) for f in plan.files] == [
        (BENCH, "restore"),
        ("hello-copy.bench.yaml", "delete"),
        ("instruments/my-scope-a.yaml", "delete"),
    ]
    done = await service.rollback(M.RollbackRequest(bench=BENCH, turn_id=t.id))
    assert done.done
    assert not template.exists() and not (manager.workspace / "hello-copy.bench.yaml").exists()
    restored = manager.files.read(BENCH)
    assert restored.ok and restored.data["instruments"]["scope"]["model"] == "siglent-sds2104xplus"
    assert load_yaml(original.decode())["instruments"] == restored.data["instruments"]


async def test_a_file_in_use_by_a_running_session_is_stopped_first_with_consent(manager):
    llm = ScriptedLLM([("read_bench", {})], edit([{"op": "set_label", "name": "awg", "label": "X"}]), "好")
    service = service_with(manager, llm)
    t = await turn(service, "改标签")
    served = await manager.start(BENCH)
    plan = service.rollback_plan(BENCH)
    assert plan.sessions == [served.id]
    asked = await service.rollback(M.RollbackRequest(bench=BENCH, turn_id=t.id))
    assert asked.needs == "stop_sessions" and manager.sessions
    done = await service.rollback(M.RollbackRequest(bench=BENCH, turn_id=t.id, stop_sessions=True))
    assert done.done and not manager.sessions and label(manager) == "函数发生器"


async def test_a_created_file_changed_while_the_plan_runs_is_not_deleted_unseen(manager):
    """The delete checks the sha the plan saw (#900): a change during the stop window is a
    refused rollback with the file intact, and the next plan asks again."""
    llm = ScriptedLLM([("copy_bench", {})], "复制好了。")
    service = service_with(manager, llm)
    t = await turn(service, "复制这个测试台")
    copy = manager.workspace / "hello-copy.bench.yaml"
    assert copy.is_file()
    served = await manager.start("hello-copy.bench.yaml")
    plan = service.rollback_plan(BENCH)
    assert [(f.path, f.action) for f in plan.files] == [("hello-copy.bench.yaml", "delete")]
    assert plan.sessions == [served.id]

    original_stop = manager.stop

    async def stop_and_tamper(sid):
        copy.write_text("name: 手改的\n", encoding="utf-8")  # what a script could do during the stop
        return await original_stop(sid)

    manager.stop = stop_and_tamper  # type: ignore[method-assign]
    try:
        with pytest.raises(Conflict, match="hello-copy"):
            await service.rollback(
                M.RollbackRequest(bench=BENCH, turn_id=t.id, stop_sessions=True, confirm_lost=True)
            )
    finally:
        manager.stop = original_stop
    assert (
        copy.read_text(encoding="utf-8") == "name: 手改的\n"
        and not service.conversation(BENCH).turns[-1].rolled_back
    )
    # the next plan shows the hand change: confirm again, then it goes
    assert service.rollback_plan(BENCH).files[0].modified_since
    done = await service.rollback(
        M.RollbackRequest(bench=BENCH, turn_id=t.id, stop_sessions=True, confirm_lost=True)
    )
    assert done.done and not copy.exists()


async def test_a_delete_tampered_during_the_stop_does_not_leave_the_restores_behind(manager):
    """One turn with both a restore and a delete (#908): the delete target is re-checked after the
    sessions stopped, so a change there aborts before any restore has landed — not half-way."""

    def copy_and_label(m):
        return [
            ("copy_bench", {}),
            (
                "edit_bench",
                {"base_sha256": sha(m), "ops": [{"op": "set_label", "name": "awg", "label": "AI 改的"}]},
            ),
        ]

    llm = ScriptedLLM([("read_bench", {})], copy_and_label, "复制并改了标签。")
    service = service_with(manager, llm)
    t = await turn(service, "复制测试台并改标签")
    assert t.status == "done", t.stop_reason
    copy = manager.workspace / "hello-copy.bench.yaml"
    assert copy.is_file() and label(manager) == "AI 改的"
    await manager.start("hello-copy.bench.yaml")

    original_stop = manager.stop

    async def stop_and_tamper(sid):
        copy.write_text("name: 手改的\n", encoding="utf-8")
        return await original_stop(sid)

    manager.stop = stop_and_tamper  # type: ignore[method-assign]
    try:
        with pytest.raises(Conflict, match="hello-copy"):
            await service.rollback(
                M.RollbackRequest(bench=BENCH, turn_id=t.id, stop_sessions=True, confirm_lost=True)
            )
    finally:
        manager.stop = original_stop
    # the abort came before any write: the bench still carries the turn's label, nothing restored
    assert label(manager) == "AI 改的" and copy.read_text(encoding="utf-8") == "name: 手改的\n"
    assert not service.conversation(BENCH).turns[-1].rolled_back
    # the next plan lists the hand change: confirm again, and the whole rollback completes
    plan = service.rollback_plan(BENCH)
    assert [(f.path, f.action) for f in plan.files] == [
        (BENCH, "restore"),
        ("hello-copy.bench.yaml", "delete"),
    ]
    done = await service.rollback(
        M.RollbackRequest(bench=BENCH, turn_id=t.id, stop_sessions=True, confirm_lost=True)
    )
    assert done.done
    assert label(manager) == "函数发生器" and not copy.exists()


async def test_a_version_no_longer_in_history_aborts_before_any_file_is_touched(manager):
    """A restore whose version is gone stops the whole rollback in words (#900): no half-restore,
    the button stays, and the same request works again once the record is consistent."""

    def save_tpl(m):
        return [("save_template", {"instrument": "awg", "name": "Scope A", "base_sha256": sha(m)})]

    def read_def(m):
        return [("read_definition", {"definition_id": "my-scope-a"})]

    def edit_both(m):
        return [
            (
                "edit_definition",
                {
                    "definition_id": "my-scope-a",
                    "base_sha256": last_tool_return(m, "read_definition")["sha256"],
                    "patch": [{"op": "replace", "path": "/channel_state/frequency/default", "value": 5e6}],
                },
            ),
            (
                "edit_bench",
                {"base_sha256": sha(m), "ops": [{"op": "set_label", "name": "awg", "label": "AI 改的"}]},
            ),
        ]

    llm = ScriptedLLM([("read_bench", {})], save_tpl, "存好了。")
    service = service_with(manager, llm)
    t1 = await turn(service, "把信号源存成模板 Scope A")
    assert t1.status == "done", t1.stop_reason
    llm.script += [read_def, edit_both, "都改好了。"]
    t2 = await turn(service, "模板默认频率改成 5 MHz，测试台加个标签")
    assert t2.status == "done", t2.stop_reason
    plan = service.rollback_plan(BENCH)
    assert [(f.path, f.action) for f in plan.files] == [
        (BENCH, "restore"),
        ("instruments/my-scope-a.yaml", "restore"),
    ]

    rel = "instruments/my-scope-a.yaml"
    conv = service.store.load(BENCH)
    record = conv.files[t2.id]
    real_before = record.before[rel]
    record.before[rel] = "0" * 64  # as if the history had aged that version out
    service.store.save(conv)
    with pytest.raises(Conflict, match="my-scope-a"):
        await service.rollback(M.RollbackRequest(bench=BENCH, turn_id=t2.id))
    assert label(manager) == "AI 改的"  # the bench was not restored: no half state
    stored = yaml.safe_load((manager.workspace / rel).read_text(encoding="utf-8"))
    assert stored["channel_state"]["frequency"]["default"] == 5e6
    assert service.rollback_plan(BENCH).turn_id == t2.id

    record.before[rel] = real_before
    service.store.save(conv)
    done = await service.rollback(M.RollbackRequest(bench=BENCH, turn_id=t2.id))
    assert done.done
    assert label(manager) == "函数发生器"
    restored = yaml.safe_load((manager.workspace / rel).read_text(encoding="utf-8"))
    assert restored["channel_state"]["frequency"]["default"] == 1e6
    assert service.conversation(BENCH).turns[-1].rolled_back
