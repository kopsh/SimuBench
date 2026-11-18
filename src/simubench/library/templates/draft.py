"""From a script to a template draft (#145, instrument-authoring.md 7.4, D81): a definition for an
instrument of a PyVISA script that no library model speaks.

There is no command set to pick: the draft's command table is learned command by command — as the
library model that accepts the same header binds it, else by a template's mnemonics (spellings many
models share for one setting), else kept as a setting that only stores its value, listed to
confirm. The template whose draft supports most of the script's commands is suggested. Inputs the
script shows are filled in with the line they came from; the rest are generic values, marked to
confirm.
"""

from __future__ import annotations

import ast
import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any

from ...models.definition import InstrumentDefinition
from ..scriptscan import ScannedCommand, ScannedResource, rank
from . import Filled, Template, pointer, templates

CHANNEL = re.compile(r"(?:^|[:;])\s*(?:C|CH|CHAN|CHANNEL|SOUR|SOURCE|OUTP|OUTPUT)(\d+)\b", re.IGNORECASE)


@dataclass
class Unit:
    """One command of a script message: its header and arguments, and the line it was sent on."""

    header: str
    args: list[str]
    line: int

    @property
    def query(self) -> bool:
        return self.header.endswith("?")


def units(commands: list[ScannedCommand]) -> list[Unit]:
    out = []
    for cmd in commands:
        for part in cmd.text.split(";"):
            part = part.strip()
            if not part:
                continue
            header, _, rest = part.partition(" ")
            args = [a.strip() for a in re.split(r",|\s+", rest.strip()) if a.strip()] if rest else []
            out.append(Unit(header.strip(), args, cmd.line))
    return out


def matching(us: list[Unit], *patterns: str) -> list[Unit]:
    """The units whose header (case-insensitive, without a leading colon or a trailing ?) matches a
    pattern."""
    res = [re.compile(p, re.IGNORECASE) for p in patterns]
    return [u for u in us if any(r.fullmatch(u.header.lstrip(":").rstrip("?")) for r in res)]


def channels_used(commands: list[ScannedCommand]) -> list[tuple[int, int]]:
    """(channel number, line) for every channel a command names."""
    out = []
    for cmd in commands:
        for m in CHANNEL.finditer(cmd.text):
            out.append((int(m.group(1)), cmd.line))
    return out


def number(text: str) -> float | None:
    m = re.fullmatch(r"([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([A-Za-z]*)", text.strip())
    if not m:
        return None
    scale = {"": 1.0, "M": 1e-3, "MV": 1e-3, "U": 1e-6, "N": 1e-9, "K": 1e3, "MA": 1e6}.get(
        m.group(2).upper(), 1.0
    )
    return float(m.group(1)) * scale


# ---- hints from the script beyond the commands ------------------------------------------------------


def model_hints(source: str) -> dict[str, tuple[str, int]]:
    """Model strings the script checks *IDN? against, by the word they are named with: an argparse
    option such as ``--scope-model`` with its default, or a constant such as ``SCOPE_MODEL = "..."``."""
    out: dict[str, tuple[str, int]] = {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and "model" in node.args[0].value.lower()
        ):
            for kw in node.keywords:
                if (
                    kw.arg == "default"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    word = node.args[0].value.lower().lstrip("-").replace("_", "-").removesuffix("-model")
                    out[word] = (kw.value.value, node.lineno)
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.upper().endswith("_MODEL"):
                    out[t.id.lower().removesuffix("_model")] = (node.value.value, node.lineno)
    return out


def hint_for(res: ScannedResource, hints: dict[str, tuple[str, int]]) -> tuple[str, int] | None:
    leaf = res.key.rsplit(".", 1)[-1].lower()
    for word, value in hints.items():
        if word and (word in leaf or leaf in word):
            return value
    return None


# ---- learning a command table (D81) ---------------------------------------------------------------

#: A header node that names a channel: its number becomes ``<ch>`` in the drafted header.
CHANNEL_NODE = re.compile(r"(C|CH|CHAN|CHANNEL|SOUR|SOURCE|OUTP|OUTPUT)(\d+)", re.IGNORECASE)


def _nodes(header: str) -> list[str]:
    return [n for n in header.strip().lstrip(":").rstrip("?").split(":") if n]


def normalised(header: str) -> str:
    """The header as mnemonics are matched against it: upper case, a channel number as ``#``."""
    return ":".join(
        (m.group(1).upper() + "#") if (m := CHANNEL_NODE.fullmatch(n)) else n.upper() for n in _nodes(header)
    )


def generalised(header: str) -> str:
    """The script's header as a definition's header pattern: a channel number becomes ``<ch>``."""
    lead = ":" if header.strip().startswith(":") else ""
    return lead + ":".join(
        (m.group(1) + "<ch>") if (m := CHANNEL_NODE.fullmatch(n)) else n for n in _nodes(header)
    )


def _targets(spec: dict[str, Any]) -> tuple[list[str], list[str]]:
    """The settings and the operations a command binds."""
    states = [spec.get("state"), spec.get("read")]
    for p in spec.get("params") or []:
        states.append(p.get("state"))
        states += [v for v in (p.get("keys") or {}).values()]
    states += list((spec.get("writes") or {}).keys())
    for v in (spec.get("args") or {}).values():
        if isinstance(v, str) and v.startswith("$"):
            states.append(v[1:])
    ops = [spec["op"]] if spec.get("op") else []
    return [s for s in states if isinstance(s, str) and s], ops


def _pattern(path: str) -> re.Pattern[str]:
    return re.compile(re.sub(r"\\\{\w+\\\}", r"\\d+", re.escape(path)))


def _has_state(definition: dict[str, Any], path: str) -> bool:
    if path.startswith("ch."):
        keys, leaf = (definition.get("channel_state") or {}).keys(), path[3:]
    else:
        keys, leaf = (definition.get("state") or {}).keys(), path
    rx = _pattern(leaf)
    return any(rx.fullmatch(k) for k in keys)


def _has_op(definition: dict[str, Any], target: str) -> bool:
    from ...engine.component import component_types

    comp, _, op = target.partition(".")
    if comp == "sys":
        return True  # the template adds the system block when a command uses it
    types = component_types()
    for c in (definition.get("components") or []) + (definition.get("channel_components") or []):
        if c.get("name") == comp:
            cls = types.get(c.get("type", ""))
            return cls is not None and any(
                getattr(getattr(cls, a), "__op_name__", None) == op for a in dir(cls)
            )
    return False


@dataclass
class Learned:
    """A drafted command table and what it took."""

    table: list[dict[str, Any]] = field(default_factory=list)
    filled: list[Filled] = field(default_factory=list)
    learned_from: list[str] = field(default_factory=list)
    stored: list[dict[str, Any]] = field(default_factory=list)  # commands kept as plain settings
    #: commands learned from a library model onto settings copied from it that no behaviour reads
    plain: list[dict[str, Any]] = field(default_factory=list)
    bound: dict[str, list[tuple[list[str], int]]] = field(default_factory=dict)


def _plain(definition: InstrumentDefinition, path: str) -> dict[str, Any] | None:
    """A teacher's instrument-level setting that no component reads (it only stores its value)."""
    if path.startswith("ch."):
        return None
    spec = definition.state.get(path)
    if spec is None:
        return None
    return spec.model_dump(exclude_defaults=True, by_alias=True)


def learn(
    template: Template,
    inputs: dict[str, Any],
    res: ScannedResource,
    teachers: list[InstrumentDefinition],
    like: InstrumentDefinition | None = None,
) -> Learned:
    """The command table of a draft (D81): each command of the script, in order, bound as the
    first library model that accepts its header binds it (when the template has, or can switch on,
    what that binding needs), else by the template's mnemonics, else kept as a setting that only
    stores its value — those are listed to confirm."""
    from ...engine.commands import classify

    assert template.drafting is not None
    hooks = template.drafting
    out = Learned()
    seen: set[str] = set()

    def base() -> dict[str, Any]:
        exp = template.expand(inputs, "draft")
        return exp.definition or {}

    def resolve(spec: dict[str, Any], teacher: InstrumentDefinition | None) -> bool:
        """Whether the template has (or can switch on) everything ``spec`` binds; a binding it
        cannot take leaves the inputs as they were."""
        before, filled = copy.deepcopy(inputs), len(out.filled)
        if not _resolve(spec, teacher):
            inputs.clear()
            inputs.update(before)
            del out.filled[filled:]
            return False
        return True

    def _resolve(spec: dict[str, Any], teacher: InstrumentDefinition | None) -> bool:
        states, ops = _targets(spec)
        for target in [*states, *ops]:
            out.filled += hooks.enable(inputs, target, teacher)
        current = base()
        for target in ops:
            if not _has_op(current, target):
                return False
        for target in states:
            if _has_state(current, target):
                continue
            if teacher is None:
                return False
            plain = _plain(teacher, target)
            if plain is None:
                return False
            inputs.setdefault("settings", {})[target] = plain
            out.filled.append(
                Filled(pointer("settings", target), plain, "library", None, f"照 {teacher.id} 的设定")
            )
        return True

    def framing(teacher: InstrumentDefinition) -> None:
        """The model the script talks most like gives the reply framing (header echo, number style)."""
        if inputs.get("syntax"):
            return
        syntax = teacher.syntax.model_dump(exclude_defaults=True)
        if syntax:
            inputs["syntax"] = syntax
            for v in syntax.values():
                if isinstance(v, str) and v.startswith("$") and (plain := _plain(teacher, v[1:])) is not None:
                    inputs.setdefault("settings", {})[v[1:]] = plain
            out.filled.append(
                Filled(pointer("syntax"), syntax, "library", None, f"照 {teacher.id} 的报文写法")
            )
        fmt = teacher.format.model_dump(exclude_defaults=True)
        if fmt and not inputs.get("format"):
            inputs["format"] = fmt
            out.filled.append(Filled(pointer("format"), fmt, "library", None, f"照 {teacher.id} 的应答格式"))

    if like is not None:
        framing(like)
        out.filled += hooks.resemble(inputs, like)
    probe = template.expand(inputs, "draft")
    probe_def = InstrumentDefinition.model_validate(probe.definition) if probe.definition else None
    for cmd in res.commands:
        for u in units([cmd]):
            text = u.header + (" " + ",".join(u.args) if u.args else "")
            if u.header.startswith("*"):
                # A common command the engine answers, unless a library model gives it an operation
                # of its own (*OPC waiting for a pending acquisition): learn that one.
                own = next(
                    (
                        m.spec
                        for t in teachers
                        for m in classify(t, text)
                        if m.kind == "command" and m.spec and m.spec.op
                    ),
                    None,
                )
                if own is None:
                    continue
            if probe_def is not None and any(m.kind == "builtin" for m in classify(probe_def, text)):
                continue  # :SYSTem:ERRor? and the like: the engine answers them
            key = normalised(u.header)
            spec: dict[str, Any] | None = None
            for teacher in teachers:
                hit = next((m for m in classify(teacher, text) if m.kind == "command" and m.spec), None)
                if hit is None or hit.spec is None:
                    continue
                candidate = hit.spec.model_dump(exclude_defaults=True, by_alias=True)
                if resolve(candidate, teacher):
                    spec = candidate
                    states, ops = _targets(candidate)
                    settings = inputs.get("settings") or {}
                    if not ops and states and all(t in settings for t in states):
                        # Only settings copied from that model, which no behaviour reads: no support
                        # a library model could not give as well.
                        out.plain.append({"line": u.line, "command": text, "from": teacher.id})
                    if teacher.id not in out.learned_from:
                        out.learned_from.append(teacher.id)
                    break
            if spec is None:
                for mn in hooks.mnemonics:
                    m = re.fullmatch(mn.pattern, key)
                    if m is None:
                        continue
                    bind: dict[str, Any] | None = dict(mn.bind) if isinstance(mn.bind, dict) else mn.bind(m)
                    if bind is None:
                        continue
                    candidate: dict[str, Any] = {"header": generalised(u.header)}
                    candidate.update(bind)
                    if resolve(candidate, None):
                        spec = candidate
                        break
            if spec is None:
                path = "stored." + "_".join(
                    re.sub(r"\W+", "_", n.lower()) for n in _nodes(u.header) if not CHANNEL_NODE.fullmatch(n)
                )
                spec = {"header": generalised(u.header), "state": path}
                if path not in (inputs.get("settings") or {}):
                    values = [
                        ",".join(v.args)
                        for v in units(res.commands)
                        if normalised(v.header) == key and v.args
                    ]
                    from ..scriptscan import _guess

                    inputs.setdefault("settings", {})[path] = _guess(values)
                    out.stored.append({"line": u.line, "command": text, "setting": path})
            for target in _targets(spec)[0]:
                out.bound.setdefault(target, []).append((u.args, u.line))
            ident = json.dumps({k: spec.get(k) for k in ("header", "when")}, sort_keys=True)
            if ident not in seen:
                seen.add(ident)
                out.table.append(spec)
    rank_of = {t.id: i for i, t in enumerate(teachers)}
    out.learned_from.sort(key=lambda i: rank_of[i])  # the model the script talks most like first
    return out


# ---- suggestions -----------------------------------------------------------------------------------


def suggest(
    res: ScannedResource, definition_id: str, hints: dict[str, Any], library: list[InstrumentDefinition]
) -> dict[str, Any] | None:
    """A template draft for ``res``: each template's inputs filled from the script, with the
    command table learned command by command (D81); the template whose draft supports most of the
    script's commands. None: no template has a draft."""
    by_id = {d.id: d for d in library}
    ranked = [c for c in rank(res, library) if c.supported]
    teachers = [by_id[c.definition_id] for c in ranked]
    # The reply framing (header echo, number style) follows a model the script really talks like:
    # the closest one, when it accepts at least half of the script's commands.
    like = by_id[ranked[0].definition_id] if ranked and 2 * ranked[0].supported >= ranked[0].total else None
    drafts = []
    for template in templates():
        if template.drafting is None or not res.commands:
            continue
        inputs = template.drafting.base()
        filled = template.drafting.basics(inputs, res.commands, hints)
        learned = learn(template, inputs, res, teachers, like)
        inputs.setdefault("commands", {})["table"] = learned.table
        filled += learned.filled + template.drafting.refine(inputs, learned.bound)
        exp = template.expand(inputs, definition_id)
        if exp.definition is None:
            continue
        expanded = InstrumentDefinition.model_validate(exp.definition)
        [after] = rank(res, [expanded])
        bound = after.supported - len(learned.stored)  # a setting kept only to confirm is no support
        drafts.append((bound, template, inputs, filled, exp, expanded, after, learned))
    if not drafts:
        return None
    drafts.sort(key=lambda d: d[0], reverse=True)
    _, template, inputs, filled, exp, expanded, after, learned = drafts[0]
    if not after.supported:
        return None
    return {
        "template": template.id,
        "definition_id": definition_id,
        "inputs": inputs,
        "filled": [f.as_dict() for f in filled],
        "diagnostics": [d.model_dump() for d in exp.diagnostics],
        "learned_from": learned.learned_from,
        "stored_only": learned.stored,
        "plain_from_library": learned.plain,
        "alternatives": [{"template": d[1].id, "supported": d[6].supported} for d in drafts[1:3]],
        "supported": after.supported,
        "checked": after.total,
        "unsupported": [
            {"line": c.line, "command": m.text, "reason": m.reason} for c, m in after.unsupported
        ],
        "instrument_class": expanded.instrument_class,
        "channel_port": next(iter(expanded.ports.channel), None),
    }


def default(path: tuple[str | int, ...], value: Any, note: str = "默认值，待确认") -> Filled:
    return Filled(pointer(*path), value, "default", None, note)


def found(path: tuple[str | int, ...], value: Any, line: int, note: str | None = None) -> Filled:
    return Filled(pointer(*path), value, "script", line, note)


def suggester(source: str, library: list[InstrumentDefinition] | None = None) -> Any:
    """The ``suggest`` callback of ``library.scriptscan.draft_bench`` for one script; ``library``
    is the models the command table is learned from (D81)."""
    hints = model_hints(source)
    models = list(library or [])

    def suggest_one(res: ScannedResource, instance: str) -> dict[str, Any] | None:
        model = hint_for(res, hints)
        slug = re.sub(r"[^a-z0-9]+", "-", model[0].lower()).strip("-") if model else ""
        return suggest(res, slug or f"{instance}-draft", {"model": model, "receiver": res.key}, models)

    return suggest_one
