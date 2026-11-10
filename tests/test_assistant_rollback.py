"""“Restore to before the last turn” (#876, D66): files the last turn changed go back, files it
created are deleted (the user's action, never a tool), changes made after the turn are listed and
need consent, running sessions are stopped first with consent, and the conversation goes on."""

from __future__ import annotations

import pytest
import yaml
from tests.helpers import ScriptedLLM
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
