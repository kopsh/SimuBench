"""From a script to a template draft (#145, instrument-authoring.md 7.4): which template and command
set an instrument of a PyVISA script speaks, and the template's inputs filled in from what the script
writes.

Each template variant is recognised by its own commands: the variant's inputs expand into a
definition, and the script's commands are checked against it the way library definitions are
ranked (``engine.scriptscan.rank``), so the choice is explained by the commands it supports, with
their lines. Inputs the script shows are filled in with the line they came from; the rest are the
variant's defaults, marked to confirm.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from functools import cache
from typing import Any

from ...engine.scriptscan import ScannedCommand, ScannedResource, rank
from ...models.definition import InstrumentDefinition
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


# ---- suggestions -----------------------------------------------------------------------------------


@cache
def _variant_definitions() -> tuple[tuple[Template, str, InstrumentDefinition], ...]:
    out = []
    for t in templates():
        for name, inputs in t.variants().items():
            exp = t.expand(inputs, f"template-{t.id}-{name}")
            if exp.definition is not None:
                out.append((t, name, InstrumentDefinition.model_validate(exp.definition)))
    return tuple(out)


def suggest(res: ScannedResource, definition_id: str, hints: dict[str, Any]) -> dict[str, Any] | None:
    """The template variant that supports most of ``res``'s commands, with pre-filled inputs and the
    commands the expanded definition still does not support. None: no variant speaks them."""
    variants = _variant_definitions()
    if not variants or not res.commands:
        return None
    ranked = rank(res, [d for _, _, d in variants])
    by_id = {d.id: (t, name) for t, name, d in variants}
    best = ranked[0]
    if not best.supported:
        return None
    template, variant = by_id[best.definition_id]
    if template.prefill is None:
        return None
    inputs, filled = template.prefill(variant, res.commands, hints)
    exp = template.expand(inputs, definition_id)
    out: dict[str, Any] = {
        "template": template.id,
        "variant": variant,
        "definition_id": definition_id,
        "inputs": inputs,
        "filled": [f.as_dict() for f in filled],
        "diagnostics": [d.as_dict() for d in exp.diagnostics],
        "evidence": {"supported": best.supported, "checked": best.total},
        "alternatives": [
            {
                "template": by_id[c.definition_id][0].id,
                "variant": by_id[c.definition_id][1],
                "supported": c.supported,
            }
            for c in ranked[1:3]
        ],
    }
    if exp.definition is not None:
        expanded = InstrumentDefinition.model_validate(exp.definition)
        [after] = rank(res, [expanded])
        out["supported"], out["checked"] = after.supported, after.total
        out["unsupported"] = [
            {"line": c.line, "command": m.text, "reason": m.reason} for c, m in after.unsupported
        ]
        out["instrument_class"] = expanded.instrument_class
        out["channel_port"] = next(iter(expanded.ports.channel), None)
    return out


def default(path: tuple[str | int, ...], value: Any, note: str = "默认值，待确认") -> Filled:
    return Filled(pointer(*path), value, "default", None, note)


def found(path: tuple[str | int, ...], value: Any, line: int, note: str | None = None) -> Filled:
    return Filled(pointer(*path), value, "script", line, note)


def suggester(source: str) -> Any:
    """The ``suggest`` callback of ``engine.scriptscan.draft_bench`` for one script."""
    hints = model_hints(source)

    def suggest_one(res: ScannedResource, instance: str) -> dict[str, Any] | None:
        model = hint_for(res, hints)
        slug = re.sub(r"[^a-z0-9]+", "-", model[0].lower()).strip("-") if model else ""
        return suggest(res, slug or f"{instance}-draft", {"model": model, "receiver": res.key})

    return suggest_one
