"""The assistant service (#873, #876, D66): one conversation per bench, its turns run on the server.

A turn runs as a task of the server, not of the HTTP request: the panel posts the message, then
follows the turn's events (``events``) and can come back to it after a reload. The user can stop a
turn at any time (the task is cancelled); the loop guard stops one that goes round in circles. A
turn's answer is accepted only when every file it wrote passes the checks — otherwise the model is
told what fails and goes on (self-repair, no retry limit: the guard and the stop button end it).

"Restore to before the last turn" (``rollback_plan`` / ``rollback``) puts back the files the last
turn wrote: benches first, so they no longer point at templates the turn created, then the
definitions, then deletes the files the turn created — the only path in the server that deletes a
file, started by the user's button, never by a tool. A file changed after the turn (by hand) is
listed with what would be lost and restored only once the user agrees; a running session that
uses one of the files is stopped first, also only once the user agrees.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.run import AgentRunResultEvent
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from ..library.loader import load_yaml
from ..models import api as M
from ..server.benchchanges import bench_changes
from ..server.benchfiles import Conflict
from ..server.definitionchanges import definition_changes
from ..server.definitionfiles import FOLDER, DefinitionFiles
from ..server.manager import SessionManager
from . import compaction
from .config import AssistantSettings, ConfigError, LlmSettings
from .guard import Stuck
from .llm import build_model, describe_error
from .prompt import instructions
from .store import Conversation, ConversationStore, TurnFiles
from .tools import TurnContext, tools

log = logging.getLogger(__name__)

#: "No limit on steps" (D66): the guard and the stop button end a turn, not a count.
UNLIMITED_RETRIES = 1_000_000
ROLLED_BACK_NOTE = (
    "（系统说明：用户已把上一轮改过的文件恢复到那一轮之前，新建的文件已删除；继续前请重新读取。）"
)


class Busy(Exception):
    """A turn of this conversation is still running."""


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


@dataclass
class EventLog:
    """The events of the running (or last) turn of one conversation, for the panel to follow."""

    turn_id: str
    events: list[M.AssistantEvent] = field(default_factory=list)
    closed: bool = False
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def append(self, event: M.AssistantEvent) -> None:
        event.seq = len(self.events) + 1
        self.events.append(event)
        self._wake()

    def close(self) -> None:
        self.closed = True
        self._wake()

    def _wake(self) -> None:
        changed, self.changed = self.changed, asyncio.Event()
        changed.set()


class AssistantService:
    def __init__(
        self,
        manager: SessionManager,
        *,
        model_factory: Callable[[LlmSettings], Model] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.manager = manager
        self.files = manager.files
        self.definitions = DefinitionFiles(manager.files)
        self.settings = AssistantSettings(manager.workspace, env)
        self.store = ConversationStore(self.files.workspace)
        self.model_factory = model_factory or build_model
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.logs: dict[str, EventLog] = {}

    # ---- files ---------------------------------------------------------------------------------------

    @staticmethod
    def sha_of(path: Path) -> str | None:
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

    def retire_tries(self, def_id: str) -> None:
        """A changed definition: the next try opens a fresh scratch session for it."""
        for entry in self.manager.tries.all():
            if entry.definition == def_id:
                self.manager.tries.close(entry.served.id)

    def _content(self, rel: str, sha: str, record: TurnFiles) -> dict[str, Any] | None:
        """A file's parsed content at a version (by sha256): from the history, else the text the
        turn kept."""
        for v in self.files.history.versions(rel):
            if v.sha256 == sha:
                return _parse(rel, self.files.history.content(rel, v.seq))
        if rel in record.content and hashlib.sha256(record.content[rel].encode()).hexdigest() == sha:
            return _parse(rel, record.content[rel].encode())
        return None

    def _current(self, rel: str) -> dict[str, Any] | None:
        path = self.files.resolve(rel)
        return _parse(rel, path.read_bytes()) if path.is_file() else None

    def _changes(
        self, rel: str, kind: str, old: dict[str, Any] | None, new: dict[str, Any] | None
    ) -> list[M.DefinitionChange]:
        diff = bench_changes if kind == "bench" else definition_changes
        return diff(old or {}, new or {})

    # ---- conversations -------------------------------------------------------------------------------

    def _check_bench(self, bench: str) -> None:
        self.files.resolve(bench)  # inside the workspace, or PermissionError

    def conversation(self, bench: str) -> M.AssistantConversation:
        self._check_bench(bench)
        conv = self.store.load(bench)
        running = bench in self.tasks
        for turn in conv.turns:
            if turn.status == "running" and not running:  # the service stopped during the turn
                turn.status, turn.stop_reason = "error", "服务在这一轮进行中重启，本轮没有完成"
        return M.AssistantConversation(
            bench=bench,
            turns=conv.turns,
            running=running,
            configured=self.settings.current().configured,
            compacted=conv.compacted,
        )

    def clear(self, bench: str) -> None:
        self._check_bench(bench)
        if bench in self.tasks:
            raise Busy("这一轮还在进行：先停止再清除")
        self.store.clear(bench)
        self.logs.pop(bench, None)

    async def send(self, bench: str, text: str) -> M.AssistantTurn:
        self._check_bench(bench)
        if bench in self.tasks:
            raise Busy("上一轮还在进行：等它结束或先停止")
        settings = self.settings.current()
        model = self.model_factory(settings)  # ConfigError when not configured
        conv = self.store.load(bench)
        turn = M.AssistantTurn(id=uuid.uuid4().hex[:12], user=text, started_at=_now())
        conv.turns.append(turn)
        record = TurnFiles()
        conv.files[turn.id] = record
        self.store.save(conv)
        events = EventLog(turn.id)
        self.logs[bench] = events
        task = asyncio.create_task(self._run(conv, turn, record, events, model, settings))
        self.tasks[bench] = task
        task.add_done_callback(lambda _t: self.tasks.pop(bench, None))
        return turn

    async def stop(self, bench: str) -> bool:
        task = self.tasks.get(bench)
        if task is None:
            return False
        task.cancel()
        with _suppress_cancel():
            await task
        return True

    async def events(self, bench: str, turn_id: str, after: int = 0) -> AsyncIterator[M.AssistantEvent]:
        """The turn's events after ``after``, following it live until it ends; for a turn that
        ended before this service started, just its final state."""
        events = self.logs.get(bench)
        if events is None or events.turn_id != turn_id:
            turn = next((t for t in self.conversation(bench).turns if t.id == turn_id), None)
            if turn is None:
                raise KeyError(f"没有这一轮对话 {turn_id!r}")
            yield M.AssistantEvent(seq=after + 1, type="turn", turn_id=turn_id, turn=turn)
            return
        while True:
            changed = events.changed
            if after < len(events.events):
                event = events.events[after]
                after = event.seq
                yield event
                continue  # more may have come while the consumer held this one
            if events.closed:
                return
            await changed.wait()

    # ---- a turn --------------------------------------------------------------------------------------

    def _agent(self, model: Model, settings: LlmSettings, ctx: TurnContext) -> Agent[TurnContext, str]:
        def compacted() -> None:
            ctx.turn.compacted = True
            ctx.conv.compacted = True
            ctx.emit(M.AssistantEvent(seq=0, type="compacted", turn_id=ctx.turn.id))

        agent: Agent[TurnContext, str] = Agent(
            model,
            deps_type=TurnContext,
            instructions=instructions(ctx.conv.bench),
            tools=tools(),
            retries=UNLIMITED_RETRIES,
            capabilities=[ProcessHistory(compaction.processor(lambda: settings.context_tokens, compacted))],
        )

        @agent.output_validator
        def files_pass_the_checks(run: RunContext[TurnContext], output: str) -> str:
            problems = self._problems(run.deps.files)
            if problems:
                run.deps.guard.record("final_check", sorted(run.deps.files.kinds), False, problems)
                raise ModelRetry("本轮改过的文件还没有通过校验，先修好再回答：\n" + "\n".join(problems))
            return output

        return agent

    def _problems(self, record: TurnFiles) -> list[str]:
        out: list[str] = []
        for rel, kind in record.kinds.items():
            if not self.files.resolve(rel).is_file():
                continue
            try:
                doc: Any = self.files.read(rel) if kind == "bench" else self.definitions.read(Path(rel).stem)
            except (ValueError, UnicodeDecodeError) as exc:
                out.append(f"{rel}: {exc}")
                continue
            out += [
                f"{rel} {d.code} {d.pointer}: {d.message}" for d in doc.diagnostics if d.severity == "error"
            ]
        return out

    async def _run(
        self,
        conv: Conversation,
        turn: M.AssistantTurn,
        record: TurnFiles,
        events: EventLog,
        model: Model,
        settings: LlmSettings,
    ) -> None:
        loop = asyncio.get_running_loop()

        def emit(event: M.AssistantEvent) -> None:
            # Tools run in worker threads: every event goes through the loop, in order.
            loop.call_soon_threadsafe(events.append, event)

        ctx = TurnContext(self, conv, turn, record, emit)
        agent = self._agent(model, settings, ctx)
        prompt = f"{conv.note}\n\n{turn.user}" if conv.note else turn.user
        messages: list[ModelMessage] | None = None
        try:
            async with agent.run_stream_events(
                prompt,
                message_history=conv.messages,
                deps=ctx,
                model_settings=ModelSettings(timeout=settings.timeout_s, parallel_tool_calls=False),
                usage_limits=UsageLimits(request_limit=None),
            ) as stream:
                async for ev in stream:
                    piece = None
                    if isinstance(ev, PartStartEvent) and isinstance(ev.part, TextPart):
                        piece = (
                            "\n\n" if turn.reply and not turn.reply.endswith("\n") else ""
                        ) + ev.part.content
                    elif isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta):
                        piece = ev.delta.content_delta
                    elif isinstance(ev, AgentRunResultEvent):
                        messages = ev.result.all_messages()
                    if piece:
                        turn.reply += piece
                        emit(M.AssistantEvent(seq=0, type="text", turn_id=turn.id, text=piece))
            turn.status = "done"
        except Stuck as exc:
            turn.status, turn.stop_reason = "stuck", str(exc)
        except asyncio.CancelledError:
            turn.status, turn.stop_reason = "stopped", "已按你的要求停止"
        except ConfigError as exc:
            turn.status, turn.stop_reason = "error", str(exc)
        except Exception as exc:  # the LLM service failed: said in words, the key never in it
            log.warning("assistant turn failed: %s", describe_error(exc, settings))
            turn.status, turn.stop_reason = "error", describe_error(exc, settings)
        finally:
            conv.note = None
            if messages is None:  # ended early: the next turn still sees what happened
                said = turn.reply or "（没有回答）"
                messages = [
                    *conv.messages,
                    ModelRequest(parts=[UserPromptPart(content=prompt)]),
                    ModelResponse(
                        parts=[TextPart(content=f"{said}\n\n[本轮在完成前结束：{turn.stop_reason}]")]
                    ),
                ]
            conv.messages = messages
            self._summarize(turn, record)
            turn.finished_at = _now()
            self.store.save(conv)
            emit(M.AssistantEvent(seq=0, type="turn", turn_id=turn.id, turn=turn))
            loop.call_soon_threadsafe(events.close)

    def _summarize(self, turn: M.AssistantTurn, record: TurnFiles) -> None:
        """The turn's "what changed", from the files themselves (not from the model's words)."""
        turn.files = []
        for rel, before in record.before.items():
            kind = record.kinds.get(rel, "bench")
            now = self.sha_of(self.files.resolve(rel))
            if now is not None:
                record.after[rel] = now
            old = self._content(rel, before, record) if before else None
            changes = self._changes(rel, kind, old, self._current(rel))
            if before is None or changes:
                turn.files.append(
                    M.AssistantFileChange(path=rel, kind=kind, created=before is None, changes=changes)  # type: ignore[arg-type]
                )

    # ---- restore to before the last turn (#876) --------------------------------------------------------

    def rollback_plan(self, bench: str) -> M.RollbackPlan:
        self._check_bench(bench)
        conv = self.store.load(bench)
        last = conv.turns[-1] if conv.turns else None
        record = conv.files.get(last.id) if last else None
        if (
            last is None
            or record is None
            or last.rolled_back
            or last.status == "running"
            or bench in self.tasks
        ):
            return M.RollbackPlan()
        files: list[M.RollbackFile] = []
        for rel, before in record.before.items():
            kind = record.kinds.get(rel, "bench")
            current = self.sha_of(self.files.resolve(rel))
            after = record.after.get(rel)
            if before is None and current is None:
                continue  # created and already gone
            if before is not None and current == before:
                continue  # already as it was
            modified = current != after
            lost: list[M.DefinitionChange] = []
            if modified:
                at_turn_end = self._content(rel, after, record) if after else None
                lost = self._changes(rel, kind, at_turn_end, self._current(rel))
            files.append(
                M.RollbackFile(
                    path=rel,
                    kind=kind,  # type: ignore[arg-type]
                    action="delete" if before is None else "restore",
                    modified_since=modified,
                    lost=lost,
                )
            )
        files.sort(key=lambda f: (f.kind != "bench", f.action == "delete", f.path))
        return M.RollbackPlan(
            turn_id=last.id if files else None, files=files, sessions=self._sessions_using(files)
        )

    def _sessions_using(self, files: list[M.RollbackFile]) -> list[str]:
        benches = {self.files.resolve(f.path) for f in files if f.kind == "bench"}
        definitions = {Path(f.path).stem for f in files if f.kind == "definition"}
        out = []
        for sid, served in self.manager.sessions.items():
            uses = {inst.definition.id for inst in served.session.instruments.values()}
            if served.bench_path in benches or uses & definitions:
                out.append(sid)
        return out

    async def rollback(self, req: M.RollbackRequest) -> M.RollbackResult:
        plan = self.rollback_plan(req.bench)
        if plan.turn_id is None or plan.turn_id != req.turn_id:
            raise Conflict("只能恢复最后一轮；对话已经变化，重新打开面板", None)
        if plan.sessions and not req.stop_sessions:
            return M.RollbackResult(done=False, needs="stop_sessions", plan=plan)
        if any(f.modified_since for f in plan.files) and not req.confirm_lost:
            return M.RollbackResult(done=False, needs="confirm_lost", plan=plan)
        for sid in plan.sessions:
            await self.manager.stop(sid)
        conv = self.store.load(req.bench)
        record = conv.files[plan.turn_id]
        # Benches first (they stop pointing at templates of this turn), then definitions, then the
        # files the turn created.
        for f in [f for f in plan.files if f.action == "restore"]:
            self._restore(f, record)
        for f in [f for f in plan.files if f.action == "delete"]:
            current = self.sha_of(self.files.resolve(f.path))
            if current is not None:
                self.files.remove_created(f.path, current)
            if f.kind == "definition":
                self.retire_tries(Path(f.path).stem)
        turn = next(t for t in conv.turns if t.id == plan.turn_id)
        turn.rolled_back = True
        conv.note = ROLLED_BACK_NOTE
        self.store.save(conv)
        return M.RollbackResult(done=True, plan=plan)

    def _restore(self, f: M.RollbackFile, record: TurnFiles) -> None:
        before = record.before[f.path]
        assert before is not None
        current = self.sha_of(self.files.resolve(f.path))
        if f.kind == "bench":
            seq = next((v.seq for v in self.files.history.versions(f.path) if v.sha256 == before), None)
            if seq is not None and current is not None:
                self.files.restore(f.path, seq, current)
                return
            data = self._content(f.path, before, record)
            if data is None:
                raise FileNotFoundError(f"{f.path} 的上一轮之前的版本已不在历史里")
            self.files.save(f.path, current, data, "restore")
            return
        def_id = Path(f.path).stem
        rel = f"{FOLDER}/{def_id}.yaml"
        seq = next((v.seq for v in self.files.history.versions(rel) if v.sha256 == before), None)
        if seq is not None and current is not None:
            self.definitions.restore(def_id, seq, current)
        else:
            data = self._content(f.path, before, record)
            if data is None:
                raise FileNotFoundError(f"{f.path} 的上一轮之前的版本已不在历史里")
            self.definitions.save(def_id, current, data, "restore")
        self.retire_tries(def_id)


def _parse(rel: str, raw: bytes) -> dict[str, Any]:
    import json

    text = raw.decode("utf-8")
    data = json.loads(text) if rel.endswith(".json") else load_yaml(text)
    return data if isinstance(data, dict) else {}


class _suppress_cancel:
    def __enter__(self) -> None:
        return None

    def __exit__(self, kind: Any, exc: Any, tb: Any) -> bool:
        return kind is asyncio.CancelledError
