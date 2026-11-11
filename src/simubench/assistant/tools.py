"""The assistant's tools (#873, #874, #875, D66): the same services the MCP tools and the Web
Bench use (``BenchFiles``, ``DefinitionFiles``, ``engine/edit.py``, the session manager), a subset.

The rules live here, in the server, not in the prompt — whatever the model says, it cannot pass them:

* no tool deletes a bench or a definition file (an instrument or a wire in a bench, yes);
* while a bench runs, only settings change: structure (instruments, parts, wires, models,
  addresses) needs the bench stopped, and stopping needs the user's agreement — the assistant asks
  with ``ask_to_stop_session`` and may stop only in a later turn, after the user answered;
* no simulation control (faults, the clock, true values) and no SCPI to a running bench;
* a definition's commands, replies and error codes change only with an example the user gave
  (``user_example`` must quote the user's own message): no guessing;
* built-in models are read only (``DefinitionFiles.edit`` refuses them).

Every write is marked ``source=agent`` in the version history (D35) and recorded for the turn, so
"restore to before the last turn" knows what to put back (#876). Each call is a progress step for
the panel and goes through the loop guard (:mod:`guard`).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field, TypeAdapter
from pydantic_ai import RunContext, Tool

from ..engine.edit import AddInstrument, EditError, EditOp
from ..engine.jsonpatch import PatchError, PatchOp
from ..engine.record import jsonable
from ..models import api as M
from ..server import drive
from ..server.benchfiles import Conflict
from ..server.errors import Forbidden
from ..server.manager import BenchInvalid, ClientsConnected, InstrumentRemote, ServedSession
from ..server.mytemplates import save_as_template
from .guard import LoopGuard, Stuck
from .store import Conversation, TurnFiles

if TYPE_CHECKING:
    from .service import AssistantService

#: Edits that change what a running bench is made of; refused while it runs.
STRUCTURAL = frozenset(
    {
        "add_instrument",
        "add_part",
        "add_dut",
        "remove",
        "replace_instrument",
        "rename",
        "connect",
        "disconnect",
        "set_accessories",
        "set_address",
        "set_dut",
    }
)
_OPS = TypeAdapter(list[EditOp])
_PATCH = TypeAdapter(list[PatchOp])
_SCPI_SECTIONS = ("/commands", "/errors", "/builtins")


@dataclass
class TurnContext:
    """What a tool call knows about the turn it runs in (the agent's deps)."""

    service: AssistantService
    conv: Conversation
    turn: M.AssistantTurn
    files: TurnFiles
    emit: Callable[[M.AssistantEvent], None]
    guard: LoopGuard = field(default_factory=LoopGuard)

    @property
    def index(self) -> int:
        return len(self.conv.turns) - 1


# ---- helpers -------------------------------------------------------------------------------------


def _error_text(exc: Exception) -> str:
    if isinstance(exc, Conflict):
        return f"{exc}：文件在别处被改过（可能是用户在画布上），重新读取后再改"
    if isinstance(exc, BenchInvalid):
        return "测试台未通过校验，不能启动：" + "；".join(
            f"{d.pointer} {d.message}" for d in exc.diagnostics if d.severity == "error"
        )
    if isinstance(exc, ClientsConnected | InstrumentRemote):
        return f"{exc}：有脚本连着端口，告诉用户先断开脚本"
    if isinstance(exc, FileNotFoundError):
        return "文件不存在"
    if isinstance(exc, KeyError):
        return str(exc).strip("'\"")
    if isinstance(exc, PatchError):
        return f"补丁没有应用：{exc}"
    text = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    return text


_EXPECTED = (EditError, Conflict, PermissionError, FileNotFoundError, KeyError, ValueError, PatchError)


class _Step:
    """One progress line of the panel, opened when a tool starts and closed with its outcome."""

    def __init__(self, ctx: TurnContext, action: str, subject: str | None) -> None:
        self.ctx = ctx
        self.indices: list[int] = []
        self.add(action, subject)

    def add(self, action: str, subject: str | None) -> None:
        turn = self.ctx.turn
        turn.steps.append(M.AssistantStep(action=action, subject=subject))
        index = len(turn.steps) - 1
        self.indices.append(index)
        self.ctx.emit(
            M.AssistantEvent(seq=0, type="step", turn_id=turn.id, step=turn.steps[index], index=index)
        )

    def done(self, ok: bool, error: str | None = None) -> None:
        for index in self.indices:
            step = self.ctx.turn.steps[index]
            step.ok, step.error = ok, error
            self.ctx.emit(
                M.AssistantEvent(seq=0, type="step_done", turn_id=self.ctx.turn.id, step=step, index=index)
            )


def _call(ctx: TurnContext, tool: str, args: Any, fn: Callable[[], Any], step: _Step | None = None) -> Any:
    """Run one tool body: the outcome goes to the loop guard and the step; an expected failure
    becomes ``{"error": ...}`` for the model to act on (no retry budget, no turn limit)."""
    step = step or _Step(ctx, tool, None)
    try:
        result = fn()
    except Stuck:
        step.done(False)
        raise
    except (*_EXPECTED, BenchInvalid, ClientsConnected, InstrumentRemote) as exc:
        text = _error_text(exc)
        step.done(False, text)
        ctx.guard.record(tool, args, False, text)
        return {"error": text}
    step.done(True)
    ctx.guard.record(tool, args, True, result)
    return result


async def _call_async(ctx: TurnContext, tool: str, args: Any, fn: Callable[[], Any], step: _Step) -> Any:
    try:
        result = await fn()
    except (*_EXPECTED, BenchInvalid, ClientsConnected, InstrumentRemote) as exc:
        text = _error_text(exc)
        step.done(False, text)
        ctx.guard.record(tool, args, False, text)
        return {"error": text}
    step.done(True)
    ctx.guard.record(tool, args, True, result)
    return result


def _bench(ctx: TurnContext, path: str | None) -> str:
    return path or ctx.conv.bench


def running_session(ctx: TurnContext, bench: str) -> ServedSession | None:
    service = ctx.service
    try:
        target = service.files.resolve(bench)
    except PermissionError:
        return None
    return next((s for s in service.manager.sessions.values() if s.bench_path == target), None)


def touch(ctx: TurnContext, rel: str, kind: str) -> None:
    """Remember a file's version before the turn's first write to it."""
    if rel in ctx.files.before:
        return
    path = ctx.service.files.resolve(rel)
    ctx.files.before[rel] = ctx.service.sha_of(path)
    ctx.files.kinds[rel] = kind
    if path.is_file():
        ctx.files.content[rel] = path.read_text(encoding="utf-8")


def _diagnostics(doc: Any) -> dict[str, Any]:
    errors = [
        f"{d.code} {d.pointer}: {d.message}" + (f"（{d.suggestion}）" if d.suggestion else "")
        for d in doc.diagnostics
        if d.severity == "error"
    ]
    warnings = [f"{d.code} {d.pointer}: {d.message}" for d in doc.diagnostics if d.severity != "error"]
    return {"ok": doc.ok, "errors": errors, "warnings": warnings}


def _bench_out(doc: M.BenchDocument, ctx: TurnContext, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    out = {"path": doc.path, "sha256": doc.sha256, **_diagnostics(doc)}
    out["running"] = running_session(ctx, doc.path) is not None
    if not doc.ok:
        out["next"] = "校验没有通过：按 errors 修好再继续"
    return out | (extra or {})


def _instrument_class(ctx: TurnContext, model: str) -> str | None:
    try:
        return str(ctx.service.files.library(None).get(model).instrument_class)
    except Exception:  # an unknown model: the edit reports it
        return None


def _op_subject(ctx: TurnContext, op: Any) -> str | None:
    kind = getattr(op, "op", "")
    if kind == "add_instrument":
        return _instrument_class(ctx, op.model) or op.model
    if kind in ("replace_instrument",):
        return op.name
    if kind == "connect":
        return f"{op.from_} → {op.to}"
    if kind == "set_state":
        return f"{op.instrument}.{op.path}"
    for attr in ("name", "instrument", "part"):
        value = getattr(op, attr, None)
        if isinstance(value, str):
            return value
    return None


def _quotes_user(ctx: TurnContext, example: str | None) -> bool:
    if not example or len(example.strip()) < 4:
        return False
    norm = re.sub(r"\s+", " ", example).strip()
    return any(norm in re.sub(r"\s+", " ", text) for text in ctx.conv.user_texts())


# ---- the tools -----------------------------------------------------------------------------------


def list_catalog(ctx: RunContext[TurnContext], query: str | None = None) -> dict[str, Any]:
    """Instrument models (id, class, manufacturer, model, channels, ports) and part types that can
    be placed on a bench; `query` filters by words in id, maker, model, class or description.
    Models with mine=true are the user's own templates."""
    t = ctx.deps

    def run() -> dict[str, Any]:
        cat = t.service.files.catalog()
        words = (query or "").lower().split()

        def hit(*texts: Any) -> bool:
            blob = " ".join(str(x or "") for x in texts).lower()
            return all(w in blob for w in words)

        return {
            "instruments": [
                {
                    "id": i.id,
                    "class": i.instrument_class,
                    "manufacturer": i.manufacturer,
                    "model": i.model,
                    "channels": i.channels,
                    "ports": i.ports,
                    "mine": i.workspace,
                }
                for i in cat.instruments
                if hit(i.id, i.manufacturer, i.model, i.instrument_class, i.description)
            ],
            "parts": [
                {"type": c.type, "label": c.label, "ports": [p.name for p in c.ports], "defaults": c.defaults}
                for c in cat.components
                if c.category == "part" and hit(c.type, c.label, c.label_en, c.doc)
            ],
        }

    return _call(t, "list_catalog", {"query": query}, run, _Step(t, "list_catalog", query))


def get_definition(ctx: RunContext[TurnContext], definition_id: str) -> dict[str, Any]:
    """One model: its settings (path, type, unit, range, options, default), ports and the headers
    of its SCPI commands. Use the setting paths with set_state (ch<n>.<name> for channel settings)."""
    t = ctx.deps

    def run() -> dict[str, Any]:
        d = t.service.files.library(None).get(definition_id)

        def spec(path: str, s: Any) -> dict[str, Any]:
            out: dict[str, Any] = {"path": path, "type": s.type}
            for key in ("unit", "min", "max", "options", "default", "readonly"):
                v = getattr(s, key)
                if v not in (None, False):
                    out[key] = jsonable(v)
            return out

        return {
            "id": d.id,
            "class": d.instrument_class,
            "manufacturer": d.manufacturer,
            "model": d.model,
            "channels": d.channels.count,
            "state": [spec(k, s) for k, s in d.state.items()],
            "channel_state": [spec(f"ch<n>.{k}", s) for k, s in d.channel_state.items()],
            "channel_ports": list(d.ports.channel),
            "instrument_ports": list(d.ports.instrument),
            "commands": [c.header for c in d.commands][:120],
            "identity": d.identity.model_dump(),
        }

    return _call(t, "get_definition", {"id": definition_id}, run, _Step(t, "get_definition", definition_id))


def list_benches(ctx: RunContext[TurnContext]) -> list[dict[str, Any]]:
    """Bench files in the workspace (path, name, number of instruments)."""
    t = ctx.deps
    return _call(  # type: ignore[no-any-return]
        t, "list_benches", {}, lambda: [b.model_dump(mode="json") for b in t.service.manager.bench_files()]
    )


def read_bench(ctx: RunContext[TurnContext], path: str | None = None) -> dict[str, Any]:
    """A bench's content, its sha256 (pass it to edit_bench), its diagnostics and whether it is
    running. Without `path`: the bench this conversation belongs to."""
    t = ctx.deps
    bench = _bench(t, path)

    def run() -> dict[str, Any]:
        doc = t.service.files.read(bench)
        return _bench_out(doc, t, {"data": jsonable(doc.data)})

    return _call(t, "read_bench", {"path": bench}, run, _Step(t, "read_bench", bench))  # type: ignore[no-any-return]


def create_bench(
    ctx: RunContext[TurnContext],
    path: Annotated[str, Field(description="Workspace-relative, ending in .bench.yaml")],
    name: Annotated[str, Field(description="The bench's name")],
    description: str | None = None,
) -> dict[str, Any]:
    """Create an empty bench file; fill it with edit_bench."""
    t = ctx.deps

    def run() -> dict[str, Any]:
        data: dict[str, Any] = {"schema": "simubench.bench/2", "name": name}
        if description:
            data["description"] = description
        touch(t, path, "bench")
        return _bench_out(t.service.files.save(path, None, data, "agent"), t)

    return _call(t, "create_bench", {"path": path}, run, _Step(t, "create_bench", path))  # type: ignore[no-any-return]


def copy_bench(
    ctx: RunContext[TurnContext],
    path: str | None = Field(None, description="The bench to copy; default: this conversation's bench"),
    new_path: str | None = Field(
        None, description="The copy's file; default <stem>-copy.bench.yaml beside it"
    ),
    name: str | None = Field(None, description="The copy's name"),
) -> dict[str, Any]:
    """Copy a whole bench — instruments, wires, settings, layout — into a new file, to reuse a set
    of instruments and wiring; continue on the copy (no need to connect anything again)."""
    t = ctx.deps
    source = _bench(t, path)

    def run() -> dict[str, Any]:
        target = new_path or t.service.files.free_path(source.replace(".bench.", "-copy.bench.", 1))
        touch(t, target, "bench")
        return _bench_out(t.service.files.copy(source, target, name, "agent"), t)

    return _call(t, "copy_bench", {"path": source, "new": new_path}, run, _Step(t, "copy_bench", source))  # type: ignore[no-any-return]


def edit_bench(
    ctx: RunContext[TurnContext],
    base_sha256: Annotated[str, Field(description="sha256 from the last read_bench / edit_bench")],
    ops: Annotated[
        list[dict[str, Any]],
        Field(
            description="Edit operations, applied in order, all or nothing: add_instrument {name, model, "
            "address?}, add_part {name, type, params}, remove {name}, connect {from: 'a.OUT1', to: 'b.CH1'}, "
            "disconnect {index}, set_state {instrument, path, value}, set_params {part, params}, "
            "replace_instrument {name, model}, rename {name, new_name}, set_label, set_address, "
            "set_identity {instrument, idn?, serial?, firmware?}, set_deviation, set_dut, set_meta, set_run",
        ),
    ],
    path: str | None = Field(None, description="The bench; default: this conversation's bench"),
) -> dict[str, Any]:
    """Change a bench: add and remove instruments and parts, connect and disconnect ports, set initial
    settings and part parameters. An address already used by another instrument is replaced by a
    free one (the result says which). While the bench runs only settings may change; structure needs
    it stopped. Returns the new sha256, the diagnostics and what changed."""
    t = ctx.deps
    bench = _bench(t, path)
    try:
        parsed = _OPS.validate_python(ops)
    except ValueError as exc:
        message = "操作格式不对：" + str(exc).splitlines()[0]

        def refuse() -> Any:
            raise EditError(message)

        return _call(t, "edit_bench", {"path": bench, "ops": ops}, refuse, _Step(t, "edit_bench", bench))  # type: ignore[no-any-return]
    step = _Step(t, parsed[0].op if parsed else "edit_bench", _op_subject(t, parsed[0]) if parsed else bench)
    for op in parsed[1:]:
        step.add(op.op, _op_subject(t, op))

    def run() -> dict[str, Any]:
        structural = sorted({op.op for op in parsed} & STRUCTURAL)
        if structural and running_session(t, bench) is not None:
            raise Forbidden(
                f"测试台正在运行，不能改结构（{', '.join(structural)}）：运行中只能调参数。"
                "先向用户说明为什么要停止，用 ask_to_stop_session 征得同意，等用户回答后再停止"
            )
        before = t.service.files.read(bench)
        touch(t, bench, "bench")
        doc = t.service.files.edit(bench, base_sha256, parsed, "agent")
        notes = []
        for op in parsed:
            if isinstance(op, AddInstrument) and op.address:
                given = ((doc.data.get("instruments") or {}).get(op.name) or {}).get("address")
                if given and given != op.address:
                    notes.append(f"地址 {op.address} 已被占用，{op.name} 分配到空闲地址 {given}")
        from ..server.benchchanges import bench_changes

        changes = [_change_text(c) for c in bench_changes(before.data, doc.data)]
        return _bench_out(doc, t, {"changes": changes, "notes": notes})

    return _call(t, "edit_bench", {"path": bench, "ops": ops}, run, step)  # type: ignore[no-any-return]


def _change_text(c: M.DefinitionChange) -> str:
    detail = f".{c.detail}" if c.detail else ""
    return f"{c.action} {c.kind} {c.target}{detail}: {c.old!r} → {c.new!r}"


def validate_bench(ctx: RunContext[TurnContext], path: str | None = None) -> dict[str, Any]:
    """Check a bench: errors and warnings with where they are and how to fix them."""
    t = ctx.deps
    bench = _bench(t, path)
    return _call(  # type: ignore[no-any-return]
        t,
        "validate_bench",
        {"path": bench},
        lambda: _bench_out(t.service.files.read(bench), t),
        _Step(t, "validate_bench", bench),
    )


async def start_session(ctx: RunContext[TurnContext], path: str | None = None) -> dict[str, Any]:
    """Start the bench (it replaces the bench that runs now): the instruments answer SCPI at the
    returned addresses and the user watches it in the Web Bench. Refused while scripts are
    connected to the ports — tell the user."""
    t = ctx.deps
    bench = _bench(t, path)
    step = _Step(t, "start_session", bench)

    async def run() -> dict[str, Any]:
        s = await t.service.manager.start(bench)
        info = t.service.manager.views.describe(s)
        return {
            "session_id": s.id,
            "instruments": [
                {"name": i.name, "model": i.model, "address": i.address_real or i.address_virtual}
                for i in info.instruments
            ],
        }

    return await _call_async(t, "start_session", {"path": bench}, run, step)  # type: ignore[no-any-return]


def ask_to_stop_session(
    ctx: RunContext[TurnContext],
    reason: Annotated[str, Field(description="Why the bench has to stop, for the user")],
) -> str:
    """Ask the user to agree to stopping the running bench (needed before changing its structure).
    End the turn after this and wait for the answer; stop_session works only after the user replied."""
    t = ctx.deps

    def run() -> str:
        if running_session(t, t.conv.bench) is None:
            raise ValueError("测试台没有在运行，不需要停止")
        t.conv.stop_requested_in = t.index
        return "已记下。现在结束本轮，用一句话请用户确认是否停止；用户同意后再调用 stop_session"

    return _call(t, "ask_to_stop_session", {"reason": reason}, run, _Step(t, "ask_to_stop_session", None))  # type: ignore[no-any-return]


async def stop_session(ctx: RunContext[TurnContext]) -> dict[str, Any]:
    """Stop this conversation's running bench — only after ask_to_stop_session in an earlier turn
    and the user's agreement in their reply."""
    t = ctx.deps
    step = _Step(t, "stop_session", t.conv.bench)

    async def run() -> dict[str, Any]:
        asked = t.conv.stop_requested_in
        if asked is None or asked >= t.index:
            raise Forbidden(
                "停止运行中的测试台要先征得用户同意：用 ask_to_stop_session 说明原因，结束本轮，等用户回答"
            )
        served = running_session(t, t.conv.bench)
        if served is None:
            t.conv.stop_requested_in = None
            return {"stopped": False, "note": "测试台已经停止"}
        await t.service.manager.stop(served.id)
        t.conv.stop_requested_in = None
        return {"stopped": True, "session_id": served.id}

    return await _call_async(t, "stop_session", {}, run, step)  # type: ignore[no-any-return]


def describe_session(ctx: RunContext[TurnContext]) -> dict[str, Any]:
    """The running session of this bench: its instruments, their addresses, wires and nets."""
    t = ctx.deps

    def run() -> dict[str, Any]:
        served = running_session(t, t.conv.bench)
        if served is None:
            return {"running": False}
        info = t.service.manager.views.describe(served)
        return {
            "running": True,
            "session_id": served.id,
            "instruments": [
                {"name": i.name, "model": i.model, "address": i.address_real or i.address_virtual}
                for i in info.instruments
            ],
            "wires": [{"from": w.from_, "to": w.to, "net": w.net} for w in info.wires],
        }

    return _call(t, "describe_session", {}, run, _Step(t, "describe_session", None))  # type: ignore[no-any-return]


def get_instrument_state(ctx: RunContext[TurnContext], instrument: str) -> dict[str, Any]:
    """The current settings and error queue of an instrument of the running bench."""
    t = ctx.deps

    def run() -> dict[str, Any]:
        served = running_session(t, t.conv.bench)
        if served is None:
            raise ValueError("测试台没有在运行；先 start_session")
        snap = served.session.instrument(instrument).snapshot()
        snap["state"] = {k: jsonable(v) for k, v in snap["state"].items()}
        return snap  # type: ignore[no-any-return]

    return _call(
        t, "get_instrument_state", {"i": instrument}, run, _Step(t, "get_instrument_state", instrument)
    )  # type: ignore[no-any-return]


def set_live_setting(ctx: RunContext[TurnContext], instrument: str, path: str, value: Any) -> dict[str, Any]:
    """Change one setting of an instrument of the running bench, as on its front panel (a
    parameter: allowed while it runs). To keep it for the next start, also set_state in the file."""
    t = ctx.deps

    def run() -> dict[str, Any]:
        served = running_session(t, t.conv.bench)
        if served is None:
            raise ValueError("测试台没有在运行；改文件用 edit_bench 的 set_state")
        served.require_local(instrument)
        out = drive.configure(served, instrument, path, value, client="agent")
        if not out.ok:
            raise ValueError(
                f"{out.error.code if out.error else ''} {out.error.message if out.error else ''}".strip()
            )
        return {"ok": True, "value": out.value}

    return _call(
        t,
        "set_live_setting",
        {"i": instrument, "p": path, "v": value},
        run,
        _Step(t, "set_live_setting", f"{instrument}.{path}"),
    )  # type: ignore[no-any-return]


def read_definition(ctx: RunContext[TurnContext], definition_id: str) -> dict[str, Any]:
    """A definition file as stored: data, sha256 (for edit_definition), diagnostics, and whether it
    can be edited (only the user's own, in the workspace)."""
    t = ctx.deps

    def run() -> dict[str, Any]:
        doc = t.service.definitions.read(definition_id)
        return {
            "id": doc.id,
            "editable": doc.editable,
            "sha256": doc.sha256,
            "data": jsonable(doc.data),
            **_diagnostics(doc),
        }

    return _call(t, "read_definition", {"id": definition_id}, run, _Step(t, "read_definition", definition_id))  # type: ignore[no-any-return]


def save_template(
    ctx: RunContext[TurnContext],
    instrument: Annotated[str, Field(description="The instrument of this bench to save")],
    name: Annotated[str, Field(description="The template's name, as the user gave it")],
    base_sha256: Annotated[str, Field(description="sha256 of the bench from read_bench")],
) -> dict[str, Any]:
    """Save a bench instrument as the user's own template: a definition that stands on its own (it
    no longer follows the built-in model), with this instrument's initial settings as defaults and
    its identity. The bench's instrument then uses the template. Ask the user for the name only."""
    t = ctx.deps

    def run() -> dict[str, Any]:
        if running_session(t, t.conv.bench) is not None:
            raise Forbidden("测试台正在运行：保存为模板会换掉这台仪表的型号，先征得同意停止测试台")
        touch(t, t.conv.bench, "bench")
        out = save_as_template(t.service.definitions, t.conv.bench, base_sha256, instrument, name, "agent")
        assert out.definition.path is not None
        t.files.before.setdefault(out.definition.path, None)
        t.files.kinds[out.definition.path] = "definition"
        return {
            "definition_id": out.definition.id,
            "based_on": out.based_on,
            "not_folded": out.not_folded,
            "bench_sha256": out.bench.sha256,
            **_diagnostics(out.definition),
        }

    return _call(t, "save_template", {"i": instrument, "n": name}, run, _Step(t, "save_template", name))  # type: ignore[no-any-return]


def edit_definition(
    ctx: RunContext[TurnContext],
    definition_id: str,
    base_sha256: Annotated[str, Field(description="sha256 from read_definition or the last edit")],
    patch: Annotated[list[dict[str, Any]], Field(description="JSON Patch (RFC 6902) operations")],
    user_example: str | None = Field(
        None,
        description="Required when the patch touches /commands, /errors or /builtins: the command "
        "format and reply example exactly as the user wrote it in this conversation",
    ),
) -> dict[str, Any]:
    """Change one of the user's own templates (built-in models are read only): identity, defaults,
    channels, ranges, ports, SCPI commands and replies, error codes. Changing commands, replies or
    error codes needs the user's own example (user_example) — if they gave none, ask; never guess.
    After it: validate_definition, then try_definition with the changed commands, and tell the user
    what was sent and what came back."""
    t = ctx.deps

    def run() -> dict[str, Any]:
        ops = _PATCH.validate_python(patch)
        touches_scpi = any(op.path.startswith(_SCPI_SECTIONS) for op in ops)
        if touches_scpi and not _quotes_user(t, user_example):
            raise Forbidden(
                "改命令、回包或错误码前，需要用户给出命令格式和回包示例（user_example 要引用用户的原话）："
                "先问用户，不要猜"
            )
        rel = t.service.definitions.rel_path(definition_id)
        doc = t.service.definitions.read(definition_id)
        if doc.editable:
            touch(t, rel, "definition")
        new = t.service.definitions.edit(definition_id, base_sha256, ops, "agent")
        t.service.retire_tries(definition_id)
        changes = [_change_text(c) for c in new.changes]
        return {"sha256": new.sha256, "changes": changes, **_diagnostics(new)}

    return _call(
        t,
        "edit_definition",
        {"id": definition_id, "patch": patch},
        run,
        _Step(t, "edit_definition", definition_id),
    )  # type: ignore[no-any-return]


def validate_definition(ctx: RunContext[TurnContext], definition_id: str) -> dict[str, Any]:
    """Static checks of a definition (schema, settings, components, commands)."""
    t = ctx.deps

    def run() -> dict[str, Any]:
        doc = t.service.definitions.read(definition_id)
        return _diagnostics(doc)

    return _call(
        t, "validate_definition", {"id": definition_id}, run, _Step(t, "validate_definition", definition_id)
    )  # type: ignore[no-any-return]


async def try_definition(
    ctx: RunContext[TurnContext],
    definition_id: str,
    commands: Annotated[
        list[str], Field(description="Program messages sent in order to the instrument alone", max_length=100)
    ],
) -> dict[str, Any]:
    """Send commands to a model's instrument alone, without a bench (a scratch session): each
    command's reply and the errors it caused. Use it after edit_definition to show the user what
    was sent and what came back."""
    t = ctx.deps
    step = _Step(t, "try_definition", definition_id)

    async def run() -> dict[str, Any]:
        def go() -> list[dict[str, Any]]:
            with t.service.manager.tries.using(definition_id) as entry:
                return [
                    {
                        "command": c,
                        **drive.scpi_response(
                            entry.served.session, definition_id, c, client="agent"
                        ).model_dump(mode="json"),
                    }
                    for c in commands
                ]

        return {"definition": definition_id, "results": await asyncio.to_thread(go)}

    return await _call_async(t, "try_definition", {"id": definition_id, "c": commands}, run, step)  # type: ignore[no-any-return]


def run_cases(
    ctx: RunContext[TurnContext], path: Annotated[str, Field(description="Workspace-relative cases file")]
) -> dict[str, Any]:
    """Run a cases file (commands and expected replies), each case on a fresh instrument."""
    t = ctx.deps

    def run() -> dict[str, Any]:
        from ..library.coverage import run_cases as run_file

        results = run_file(t.service.files.resolve(path), [t.service.files.workspace / "instruments"])
        return {
            "passed": sum(r.passed for r in results),
            "failed": sum(not r.passed for r in results),
            "failures": [r.describe() for r in results if not r.passed][:20],
        }

    return _call(t, "run_cases", {"path": path}, run, _Step(t, "run_cases", path))  # type: ignore[no-any-return]


TOOLS = [
    list_catalog,
    get_definition,
    list_benches,
    read_bench,
    create_bench,
    copy_bench,
    edit_bench,
    validate_bench,
    start_session,
    ask_to_stop_session,
    stop_session,
    describe_session,
    get_instrument_state,
    set_live_setting,
    read_definition,
    save_template,
    edit_definition,
    validate_definition,
    try_definition,
    run_cases,
]


def tools() -> list[Tool[TurnContext]]:
    return [Tool(f, takes_ctx=True) for f in TOOLS]
