"""The assistant's tools (#873, #874, #875, D66): the same services the MCP tools and the Web
Bench use (``BenchFiles``, ``DefinitionFiles``, ``engine/edit.py``, the session manager), a subset.

The rules live here, in the server, not in the prompt — whatever the model says, it cannot pass them:

* no tool deletes a bench or a definition file (an instrument or a wire in a bench, yes);
* while a bench runs, only settings change: structure (instruments, parts, wires, models,
  addresses) needs the bench stopped, and stopping needs the user's agreement — the assistant asks
  with ``ask_to_stop_session`` and may stop only in the next turn, when the user agreed with the
  panel's button (``AssistantTurn.stop_consent``, #902) — a typed reply is no agreement;
* no simulation control (faults, the clock, true values) and no SCPI to a running bench;
* a definition's commands, replies and error codes change only with an example the user gave
  (``user_example`` must quote the user's own message): no guessing;
* built-in models are read only (``DefinitionFiles.edit`` refuses them).

Every write is marked ``source=agent`` in the version history (D35) and recorded for the turn, so
"restore to before the last turn" knows what to put back (#876). Each call is a progress step for
the panel and goes through the loop guard (:mod:`guard`).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field, TypeAdapter
from pydantic_ai import RunContext, Tool

from ...contracts import api as M
from ...contracts import tools as T
from ...engine.edit import AddInstrument
from ...engine.jsonpatch import PatchOp
from ...engine.record import bounded, jsonable
from .. import drive
from ..errors import DOMAIN_ERRORS, Forbidden, Invalid, tool_text
from ..manager import ServedSession
from ..mytemplates import save_as_template
from ..projections import definition_spec, instrument_state, run_cases_file, session_summary, try_commands
from .guard import LoopGuard
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
    """The text a failed tool leaves for the model: the assistant column of the one domain-error
    table (:mod:`..server.errors`, #936, #1382) — the same wording the MCP tools read."""
    return tool_text(exc, "assistant")


_EXPECTED = DOMAIN_ERRORS


class _Step:
    """One progress line of the panel, opened when a tool starts and closed with its outcome.
    The tool call's arguments (``_call``/``_call_async`` -> :meth:`stamp`, #1679) land on the step
    record and the ``step_done`` event; a bench-edit sub-step (``add``) has none."""

    def __init__(self, ctx: TurnContext, action: str, subject: str | None) -> None:
        self.ctx = ctx
        self.indices: list[int] = []
        self.add(action, subject)

    def stamp(self, args: Any) -> None:
        """The tool call's arguments, onto the tool's own step record (#1679): what the model
        actually sent, in the turn and the store. A bench-edit sub-step keeps none."""
        self.ctx.turn.steps[self.indices[0]].args = args

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


def _step_args(args: Any) -> Any:
    """The step record's ``args``: a dict as it came; anything else in dict form -- a request
    model dumps itself (edit_bench passes one; the field is declared ``dict | None``), a scalar
    stands for itself (#1679). Bounded (#1694): edit_bench carries a whole bench in its ops, so
    a long text is cut and marked rather than stored whole."""
    if not isinstance(args, dict):
        dump = getattr(args, "model_dump", None)
        args = jsonable(dump()) if dump is not None else args
    return bounded(args)


def _call(ctx: TurnContext, tool: str, args: Any, fn: Callable[[], Any], step: _Step | None = None) -> Any:
    """Run one tool body — the one wrapper protocol (#1734), for a sync body here and an awaited
    one through :func:`_call_async`: the step opens and the model's arguments are stamped on it
    (#1679); an expected domain error becomes ``{"error": ...}`` for the model to act on (no
    retry budget, no turn limit) and the guard counts the call; the step closes with the outcome.
    A bug (any other error) surfaces as itself, and the guard's own ``Stuck`` — raised after the
    body ran — is never caught here."""
    step = step or _Step(ctx, tool, None)
    step.stamp(_step_args(args))  # what the model actually sent, on the step record (#1679)
    try:
        result = fn()
    except _EXPECTED as exc:
        text = _error_text(exc)
        step.done(False, text)
        ctx.guard.record(tool, args, False, text)
        return {"error": text}
    step.done(True)
    ctx.guard.record(tool, args, True, result)
    return result


async def _call_async(
    ctx: TurnContext, tool: str, args: Any, fn: Callable[[], Any], step: _Step | None = None
) -> Any:
    """An awaited body through the one protocol (:func:`_call`, #1734): awaiting is the only
    difference from a sync tool, so the outcome — the result, or a caught error handed over for
    the protocol to judge — goes through _call once."""
    try:
        outcome: Any = await fn()
    except Exception as exc:  # the protocol below decides what a domain error becomes
        outcome = exc

    def run() -> Any:
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return _call(ctx, tool, args, run, step)


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


def observes(fn: Any) -> Any:
    """Mark, on the tool itself, that a call of it only looks (#1734): no bench, file or session
    changes, so the loop guard does not count it as progress. The guard consumes the declaration
    (:mod:`guard`, one completeness check over ``TOOLS``); the counterpart is :func:`changes`."""
    fn.observes = True
    return fn


def changes(fn: Any) -> Any:
    """Mark, on the tool itself, that a call of it may change something — a write, a live setting,
    a start or a stop, or the turn's own state: the guard counts such calls as progress."""
    fn.observes = False
    return fn


@observes
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


@observes
def get_definition(ctx: RunContext[TurnContext], definition_id: str) -> dict[str, Any]:
    """One model: its settings (path, type, unit, range, options, default), ports and the headers
    of its SCPI commands. Use the setting paths with set_state (ch<n>.<name> for channel settings)."""
    t = ctx.deps

    def run() -> dict[str, Any]:
        d = t.service.files.library(None).get(definition_id)
        return {
            "id": d.id,
            "class": d.instrument_class,
            "manufacturer": d.manufacturer,
            "model": d.model,
            "channels": d.channels.count,
            "state": [definition_spec(k, s) for k, s in d.state.items()],
            "channel_state": [definition_spec(f"ch<n>.{k}", s) for k, s in d.channel_state.items()],
            "channel_ports": list(d.ports.channel),
            "instrument_ports": list(d.ports.instrument),
            "commands": [c.header for c in d.commands][:120],
            "identity": d.identity.model_dump(),
        }

    return _call(t, "get_definition", {"id": definition_id}, run, _Step(t, "get_definition", definition_id))


@observes
def list_benches(ctx: RunContext[TurnContext]) -> list[dict[str, Any]]:
    """Bench files in the workspace (path, name, number of instruments)."""
    t = ctx.deps
    return _call(  # type: ignore[no-any-return]
        t, "list_benches", {}, lambda: [b.model_dump(mode="json") for b in t.service.manager.bench_files()]
    )


@observes
def read_bench(ctx: RunContext[TurnContext], path: str | None = None) -> dict[str, Any]:
    """A bench's content, its sha256 (pass it to edit_bench), its diagnostics and whether it is
    running. Without `path`: the bench this conversation belongs to."""
    t = ctx.deps
    bench = _bench(t, path)

    def run() -> dict[str, Any]:
        doc = t.service.files.read(bench)
        return _bench_out(doc, t, {"data": jsonable(doc.data)})

    return _call(t, "read_bench", {"path": bench}, run, _Step(t, "read_bench", bench))  # type: ignore[no-any-return]


@changes
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


@changes
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


@changes
def edit_bench(ctx: RunContext[TurnContext], req: T.AssistantBenchEdit) -> dict[str, Any]:
    """Change a bench: add and remove instruments and parts, connect and disconnect ports, set initial
    settings and part parameters. An address already used by another instrument is replaced by a
    free one (the result says which). While the bench runs only settings may change; structure needs
    it stopped. set_state / set_params always write the file's initial values: a running session
    keeps its own (use set_live_setting to change what runs now). Returns the new sha256, the
    diagnostics and what changed."""
    t = ctx.deps
    bench = _bench(t, req.path)
    parsed = req.ops
    step = _Step(t, parsed[0].op if parsed else "edit_bench", _op_subject(t, parsed[0]) if parsed else bench)
    for op in parsed[1:]:
        step.add(op.op, _op_subject(t, op))

    def run() -> dict[str, Any]:
        structural = sorted({op.op for op in parsed} & STRUCTURAL)
        running = running_session(t, bench) is not None
        if structural and running:
            raise Forbidden(
                f"测试台正在运行，不能改结构（{', '.join(structural)}）：运行中只能调参数。"
                "先向用户说明为什么要停止，用 ask_to_stop_session 征得同意，等用户回答后再停止"
            )
        before = t.service.files.read(bench)
        touch(t, bench, "bench")
        doc = t.service.files.edit(bench, req.base_sha256, parsed, "agent")
        notes = []
        if running and any(op.op in ("set_state", "set_params") for op in parsed):
            notes.append(
                "这里只改了文件里的初值，正在运行的会话不受影响；要现在生效，请对同一项用 set_live_setting"
            )
        for op in parsed:
            if isinstance(op, AddInstrument) and op.address:
                given = ((doc.data.get("instruments") or {}).get(op.name) or {}).get("address")
                if given and given != op.address:
                    notes.append(f"地址 {op.address} 已被占用，{op.name} 分配到空闲地址 {given}")
        from ..benchchanges import bench_changes

        changes = [_change_text(c) for c in bench_changes(before.data, doc.data)]
        return _bench_out(doc, t, {"changes": changes, "notes": notes})

    return _call(t, "edit_bench", req, run, step)  # type: ignore[no-any-return]


def _change_text(c: M.DefinitionChange) -> str:
    detail = f".{c.detail}" if c.detail else ""
    return f"{c.action} {c.kind} {c.target}{detail}: {c.old!r} → {c.new!r}"


@observes
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


@changes
async def start_session(ctx: RunContext[TurnContext], path: str | None = None) -> dict[str, Any]:
    """Start the bench (it replaces the bench that runs now): the instruments answer SCPI at the
    returned resource addresses (and the SCPI socket), and the user watches it in the Web Bench.
    Refused while scripts are connected to the ports — tell the user."""
    t = ctx.deps
    bench = _bench(t, path)
    step = _Step(t, "start_session", bench)

    async def run() -> dict[str, Any]:
        s = await t.service.manager.start(bench)
        return session_summary(t.service.manager, s)

    return await _call_async(t, "start_session", {"path": bench}, run, step)  # type: ignore[no-any-return]


@changes
def ask_to_stop_session(
    ctx: RunContext[TurnContext],
    reason: Annotated[str, Field(description="Why the bench has to stop, for the user")],
) -> str:
    """Ask the user to agree to stopping the running bench (needed before changing its structure).
    End the turn after this: the panel offers the user to agree or decline, and stop_session works
    only in the next turn, if the user agreed."""
    t = ctx.deps

    def run() -> str:
        if running_session(t, t.conv.bench) is None:
            raise Invalid("测试台没有在运行，不需要停止")
        t.conv.stop_requested_in = t.index
        t.turn.stop_question = reason
        return (
            "已记下。现在结束本轮，用一句话说明为什么要停止；面板会给用户“同意停止 / 不停止”两个按钮，"
            "用户点了同意的下一轮才能调用 stop_session"
        )

    return _call(t, "ask_to_stop_session", {"reason": reason}, run, _Step(t, "ask_to_stop_session", None))  # type: ignore[no-any-return]


@changes
async def stop_session(ctx: RunContext[TurnContext]) -> dict[str, Any]:
    """Stop this conversation's running bench — only in the turn right after ask_to_stop_session,
    when the user agreed with the panel's button."""
    t = ctx.deps
    step = _Step(t, "stop_session", t.conv.bench)

    async def run() -> dict[str, Any]:
        if not t.conv.stop_consent_alive(t.index):
            raise Forbidden(
                "停止运行中的测试台要先征得用户同意：用 ask_to_stop_session 说明原因，结束本轮，等用户回答"
            )
        if t.turn.stop_consent is False:
            raise Forbidden("用户点了“不停止”：不要停止测试台，告诉用户哪些改动因此没有做")
        if t.turn.stop_consent is None:
            raise Forbidden(
                "用户没有用面板上的“同意停止”按钮回答，文字回复不算同意；需要停止就再用 "
                "ask_to_stop_session 询问并结束本轮"
            )
        served = running_session(t, t.conv.bench)
        if served is None:
            t.conv.stop_requested_in = None
            return {"stopped": False, "note": "测试台已经停止"}
        await t.service.manager.stop(served.id)
        t.conv.stop_requested_in = None
        return {"stopped": True, "session_id": served.id}

    return await _call_async(t, "stop_session", {}, run, step)  # type: ignore[no-any-return]


@observes
def describe_session(ctx: RunContext[TurnContext]) -> dict[str, Any]:
    """The running session of this bench: session id, clock, each instrument's model and the
    addresses it answers at, and the wires."""
    t = ctx.deps

    def run() -> dict[str, Any]:
        served = running_session(t, t.conv.bench)
        if served is None:
            return {"running": False}
        info = t.service.manager.views.describe(served)
        return {
            "running": True,
            **session_summary(t.service.manager, served, info),
            "wires": [{"from": w.from_, "to": w.to, "net": w.net} for w in info.wires],
        }

    return _call(t, "describe_session", {}, run, _Step(t, "describe_session", None))  # type: ignore[no-any-return]


@observes
def get_instrument_state(ctx: RunContext[TurnContext], instrument: str) -> dict[str, Any]:
    """The current settings and error queue of an instrument of the running bench."""
    t = ctx.deps

    def run() -> dict[str, Any]:
        served = running_session(t, t.conv.bench)
        if served is None:
            raise Invalid("测试台没有在运行；先 start_session")
        return instrument_state(served, instrument)

    return _call(
        t, "get_instrument_state", {"i": instrument}, run, _Step(t, "get_instrument_state", instrument)
    )  # type: ignore[no-any-return]


@changes
def set_live_setting(ctx: RunContext[TurnContext], instrument: str, path: str, value: Any) -> dict[str, Any]:
    """Change one setting of an instrument of the running bench, as on its front panel: it takes
    effect now and does not touch the bench file. When the user also wants the value kept for the
    next start, use edit_bench set_state with the same value (that writes the file's initial
    value; on its own it does not change the running session)."""
    t = ctx.deps

    def run() -> dict[str, Any]:
        served = running_session(t, t.conv.bench)
        if served is None:
            raise Invalid("测试台没有在运行，没有可改的会话；改文件初值用 edit_bench 的 set_state")
        served.require_local(instrument)
        old = jsonable(served.session.instrument(instrument).state.get(path))
        out = drive.configure(served, instrument, path, value, client="agent")
        if not out.ok:
            raise Invalid(
                f"{out.error.code if out.error else ''} {out.error.message if out.error else ''}".strip()
            )
        if out.changed:  # the write_tx no-op rule, made visible to the model (#1678)
            t.turn.live.append(
                M.DefinitionChange(
                    kind="state",
                    action="changed",
                    target=f"{instrument}.{path}",
                    old=old,
                    new=out.value,
                )
            )
            return {"ok": True, "value": out.value, "changed": True}
        return {
            "ok": True,
            "value": out.value,
            "changed": False,
            "note": "值本来就是该值，没有变化；不要为这一项重试",
        }

    return _call(
        t,
        "set_live_setting",
        {"i": instrument, "p": path, "v": value},
        run,
        _Step(t, "set_live_setting", f"{instrument}.{path}"),
    )  # type: ignore[no-any-return]


@observes
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


@changes
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


@changes
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


@observes
def validate_definition(ctx: RunContext[TurnContext], definition_id: str) -> dict[str, Any]:
    """Static checks of a definition (schema, settings, components, commands)."""
    t = ctx.deps

    def run() -> dict[str, Any]:
        doc = t.service.definitions.read(definition_id)
        return _diagnostics(doc)

    return _call(
        t, "validate_definition", {"id": definition_id}, run, _Step(t, "validate_definition", definition_id)
    )  # type: ignore[no-any-return]


@observes
async def try_definition(ctx: RunContext[TurnContext], req: T.AssistantTryDefinition) -> dict[str, Any]:
    """Send commands to a model's instrument alone, without a bench (a scratch session): each
    command's reply and the errors it caused. Use it after edit_definition to show the user what
    was sent and what came back."""
    t = ctx.deps
    step = _Step(t, "try_definition", req.definition_id)

    async def run() -> dict[str, Any]:
        results = await try_commands(t.service.manager, req.definition_id, req.commands, client="agent")
        return {"definition": req.definition_id, "results": results}

    return await _call_async(t, "try_definition", req, run, step)  # type: ignore[no-any-return]


@observes
def run_cases(ctx: RunContext[TurnContext], req: T.RunCases) -> dict[str, Any]:
    """Run a cases file (commands and expected replies), each case on a fresh instrument: a
    workspace path, or a built-in model's manual cases (builtin:<folder>/cases.yaml); with model,
    on that definition in place of the cases' own."""
    t = ctx.deps

    def run() -> dict[str, Any]:
        results = run_cases_file(t.service.files, req.path, model=req.model)
        return {
            "passed": sum(r.passed for r in results),
            "failed": sum(not r.passed for r in results),
            "failures": [r.describe() for r in results if not r.passed][:20],
        }

    return _call(t, "run_cases", req, run, _Step(t, "run_cases", req.path))  # type: ignore[no-any-return]


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
