"""Generate a bench draft from an existing test program (D9 "由脚本生成").

The scanner reads a Python + PyVISA script without running it. It finds the resources the script
opens and the SCPI strings it sends to each, picks the instrument definition that supports most of
those commands, and lists the commands no model supports, so the engineer knows what the virtual
bench will refuse before running anything. Wiring cannot be read from a script; the draft has
instruments only and says so.

The analysis is deliberately simple and explainable: string constants, f-strings (each
placeholder read as ``1``), ``%`` and ``.format`` templates, and plain assignments of
``open_resource(...)`` results to names or attributes.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..models.definition import InstrumentDefinition
from .commands import UnitMatch, classify

RESOURCE = re.compile(r"\b(?:TCPIP|USB|GPIB|ASRL|VXI|PXI)\d*::[^\s\"']+", re.IGNORECASE)
NAME_OK = re.compile(r"[^A-Za-z0-9_-]")
SEND = {
    "write",
    "query",
    "write_raw",
    "query_ascii_values",
    "query_binary_values",
    "write_ascii_values",
    "write_binary_values",
}
# Hints from variable names, used only to break ties between equally good definitions.
CLASS_HINTS = {
    "oscilloscope": ("scope", "osc", "dso", "mso"),
    "function_generator": ("awg", "gen", "fg", "afg", "src", "source", "sig"),
    "spectrum_analyzer": ("sa", "spec", "fsv", "analyzer"),
    "smu": ("smu", "keithley"),
    "lockin": ("lockin", "lia", "sr8"),
}


@dataclass
class ScannedCommand:
    text: str
    line: int


@dataclass
class ScannedResource:
    key: str  # receiver the script uses, e.g. "scope" or "self.awg"
    address: str | None
    line: int | None
    commands: list[ScannedCommand] = field(default_factory=list)


@dataclass
class Candidate:
    definition_id: str
    supported: int
    total: int
    unsupported: list[tuple[ScannedCommand, UnitMatch]]

    @property
    def ratio(self) -> float:
        return self.supported / self.total if self.total else 0.0


@dataclass
class ScanResult:
    resources: list[ScannedResource]
    notes: list[str]


def _template(node: ast.AST, consts: dict[str, str]) -> str | None:
    """A string the expression evaluates to, with every unknown part replaced by ``1``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in consts:
        return consts[node.id]
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                parts.append(str(v.value))
            else:
                parts.append("1")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        left = _template(node.left, consts)
        if left is not None:
            return re.sub(r"%[-+ #0]*\d*(?:\.\d+)?[sdifgeExX]", "1", left)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _template(node.left, consts), _template(node.right, consts)
        if left is not None or right is not None:
            return (left if left is not None else "1") + (right if right is not None else "1")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        base = _template(node.func.value, consts)
        if base is not None:
            return re.sub(r"\{[^{}]*\}", "1", base)
    return None


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def scan_python(source: str) -> ScanResult:
    notes: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        notes.append(f"脚本无法解析为 Python（第 {exc.lineno} 行）；只按文本查找资源地址")
        found = [
            ScannedResource(key=f"res{i + 1}", address=m.group(0), line=source[: m.start()].count("\n") + 1)
            for i, m in enumerate(RESOURCE.finditer(source))
        ]
        return ScanResult(found, notes)

    consts: dict[str, str] = {}
    resources: dict[str, ScannedResource] = {}
    # Pass 1: string constants and open_resource assignments, in source order.
    for node in sorted(ast.walk(tree), key=lambda n: getattr(n, "lineno", 0)):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        text = _template(value, consts)
        for t in targets:
            if isinstance(t, ast.Name) and text is not None:
                consts[t.id] = text
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr == "open_resource"
        ):
            addr = _template(value.args[0], consts) if value.args else None
            for t in targets:
                key = _dotted(t)
                if key:
                    resources[key] = ScannedResource(key, addr, node.lineno)
    # `with rm.open_resource(...) as x:`
    for node in ast.walk(tree):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                call = item.context_expr
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "open_resource"
                    and item.optional_vars is not None
                ):
                    key = _dotted(item.optional_vars)
                    addr = _template(call.args[0], consts) if call.args else None
                    if key:
                        resources[key] = ScannedResource(key, addr, node.lineno)

    # Pass 2: SCPI strings sent to each receiver.
    unknown: set[str] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in SEND
            and node.args
        ):
            continue
        key = _dotted(node.func.value)
        text = _template(node.args[0], consts)
        if key is None or text is None:
            continue
        if key not in resources:
            # A receiver opened elsewhere (a helper, a fixture): keep its commands anyway.
            resources[key] = ScannedResource(key, None, None)
            unknown.add(key)
        resources[key].commands.append(ScannedCommand(text.strip(), node.lineno))
    for res in resources.values():
        res.commands.sort(key=lambda c: c.line)
    if unknown:
        notes.append(f"未找到 {', '.join(sorted(unknown))} 的 open_resource，地址需要手动填写")
    if not resources:
        notes.append("没有找到 open_resource 调用或 SCPI 字符串")
    # Addresses mentioned in the script but never opened directly (e.g. in a config dict).
    opened = {r.address for r in resources.values()}
    for m in RESOURCE.finditer(source):
        if m.group(0) not in opened:
            notes.append(f"地址 {m.group(0)} 出现在脚本中，但没有找到打开它的语句")
    return ScanResult(list(resources.values()), notes)


def rank(res: ScannedResource, definitions: list[InstrumentDefinition]) -> list[Candidate]:
    """Definitions ordered by how many of the resource's commands they support."""
    out = []
    for d in definitions:
        supported = total = 0
        unsupported: list[tuple[ScannedCommand, UnitMatch]] = []
        for cmd in res.commands:
            for m in classify(d, cmd.text):
                if m.kind in ("common", "builtin"):
                    continue  # every model answers these; they say nothing about the model
                total += 1
                if m.supported:
                    supported += 1
                else:
                    unsupported.append((cmd, m))
        out.append(Candidate(d.id, supported, total, unsupported))
    leaf = res.key.rsplit(".", 1)[-1].lower()
    cls = {d.id: d.instrument_class for d in definitions}

    def hint(c: Candidate) -> int:
        words = CLASS_HINTS.get(cls[c.definition_id], ())
        return 1 if any(w in leaf for w in words) else 0

    out.sort(key=lambda c: (c.supported, c.ratio, hint(c)), reverse=True)
    return out


def instance_name(key: str, taken: set[str]) -> str:
    leaf = NAME_OK.sub("_", key.rsplit(".", 1)[-1]) or "inst"
    if not leaf[0].isalpha() and leaf[0] != "_":
        leaf = f"i_{leaf}"
    name, n = leaf[:40], 2
    while name in taken:
        name, n = f"{leaf[:40]}_{n}", n + 1
    taken.add(name)
    return name


def draft_bench(
    source: str,
    definitions: Callable[[], list[InstrumentDefinition]],
    name: str = "draft",
) -> dict[str, Any]:
    """Scan ``source`` and return ``{"bench": <bench data>, "report": [...], "notes": [...]}``."""
    scan = scan_python(source)
    defs = definitions()
    taken: set[str] = set()
    instruments: dict[str, Any] = {}
    layout: dict[str, Any] = {}
    report = []
    notes = [*scan.notes]
    for res in scan.resources:
        ranked = rank(res, defs)
        best = ranked[0] if ranked else None
        if best is not None and best.total and not best.supported:
            best = None  # nothing in the library speaks this instrument's commands
        inst = instance_name(res.key, taken)
        if best is not None:
            entry: dict[str, Any] = {"model": best.definition_id}
            if res.address:
                entry["address"] = res.address
            instruments[inst] = entry
            layout[inst] = {"x": len(layout) * 320.0, "y": 60.0}
        else:
            notes.append(f"{res.key}：仪表定义库中没有支持其命令的仪表，未加入草稿")
        report.append(
            {
                "instance": inst,
                "receiver": res.key,
                "address": res.address,
                "line": res.line,
                "model": best.definition_id if best else None,
                "commands": len(res.commands),
                "supported": best.supported if best else 0,
                "checked": best.total if best else 0,
                "unsupported": [
                    {"line": c.line, "command": m.text, "reason": m.reason}
                    for c, m in (best.unsupported if best else [])
                ]
                if best
                else [{"line": c.line, "command": c.text, "reason": "没有匹配的仪表"} for c in res.commands],
                "alternatives": [
                    {"model": c.definition_id, "supported": c.supported, "checked": c.total}
                    for c in ranked[1:3]
                ],
            }
        )
    if instruments:
        notes.append("脚本中看不出接线：草稿只有仪表，请补充 DUT 与连线")
    bench = {
        "schema": "simubench.bench/1",
        "name": name,
        "description": "由脚本生成的草稿",
        "instruments": instruments,
        "layout": layout,
    }
    return {"bench": bench, "report": report, "notes": notes}
