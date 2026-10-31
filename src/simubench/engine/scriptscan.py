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


#: A template suggestion for a scanned instrument: (resource, instance name) -> the suggestion, or None.
Suggest = Callable[[ScannedResource, str], "dict[str, Any] | None"]
CHANNEL_NUMBER = re.compile(
    r"(?:^|[:;])\s*(?:C|CH|CHAN|CHANNEL|SOUR|SOURCE|OUTP|OUTPUT)(\d+)\b", re.IGNORECASE
)


def _first_channel(res: ScannedResource) -> int:
    found = [int(m.group(1)) for c in res.commands for m in CHANNEL_NUMBER.finditer(c.text)]
    return min(found) if found else 1


def draft_bench(
    source: str,
    definitions: Callable[[], list[InstrumentDefinition]],
    name: str = "draft",
    suggest: Suggest | None = None,
) -> dict[str, Any]:
    """Scan ``source`` and return ``{"bench": <bench data>, "report": [...], "notes": [...]}``.

    An instrument no library definition speaks gets a template suggestion from ``suggest`` (#145):
    the template, its command set and pre-filled inputs; the bench names the definition the agent
    then creates with create_definition_from_template."""
    scan = scan_python(source)
    defs = definitions()
    by_id = {d.id: d for d in defs}
    taken: set[str] = set()
    instruments: dict[str, Any] = {}
    layout: dict[str, Any] = {}
    report = []
    notes = [*scan.notes]
    # instance -> (instrument class, channel port pattern, first channel the script names)
    roles: dict[str, tuple[str, str | None, int]] = {}
    for res in scan.resources:
        ranked = rank(res, defs)
        best = ranked[0] if ranked else None
        if best is not None and best.total and not best.supported:
            best = None  # nothing in the library speaks this instrument's commands
        if best is not None and not best.total and res.commands:
            # Only common commands (*IDN?, *RST): they say nothing about which instrument it is.
            notes.append(f"{res.key}：只发了公共命令（如 *IDN?），看不出是哪台仪表，未加入草稿")
            continue
        inst = instance_name(res.key, taken)
        template = None
        if suggest is not None and (best is None or best.supported < best.total):
            template = suggest(res, inst)
            if template is not None and best is not None and template.get("supported", 0) <= best.supported:
                template = None  # the library already does as well
        if template is not None:
            entry: dict[str, Any] = {"model": template["definition_id"]}
            if res.address:
                entry["address"] = res.address
            instruments[inst] = entry
            layout[inst] = {"x": len(layout) * 320.0, "y": 60.0}
            roles[inst] = (
                template.get("instrument_class", ""),
                template.get("channel_port"),
                _first_channel(res),
            )
            notes.append(
                f"{res.key}：仪表定义库中没有完整支持其命令的仪表；建议用模板 {template['template']}"
                f"（{template['variant']}）创建 {template['definition_id']}：create_definition_from_template 直接接收"
                "报告中的 inputs，其中来源为 default 的项待确认"
            )
        elif best is not None:
            entry = {"model": best.definition_id}
            if res.address:
                entry["address"] = res.address
            instruments[inst] = entry
            layout[inst] = {"x": len(layout) * 320.0, "y": 60.0}
            d = by_id.get(best.definition_id)
            if d is not None:
                roles[inst] = (d.instrument_class, next(iter(d.ports.channel), None), _first_channel(res))
        else:
            notes.append(f"{res.key}：仪表定义库中没有支持其命令的仪表，未加入草稿")
        if template is not None:
            report.append(
                {
                    "instance": inst,
                    "receiver": res.key,
                    "address": res.address,
                    "line": res.line,
                    "model": template["definition_id"],
                    "commands": len(res.commands),
                    "supported": template.get("supported", 0),
                    "checked": template.get("checked", 0),
                    "unsupported": template.get("unsupported", []),
                    "template": template,
                    "alternatives": [
                        {"model": c.definition_id, "supported": c.supported, "checked": c.total}
                        for c in ranked[:2]
                    ],
                }
            )
            continue
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
    wires: list[dict[str, Any]] = []
    inferred: list[dict[str, Any]] = []
    scopes = [i for i, r in roles.items() if r[0] == "oscilloscope" and r[1]]
    sources = [i for i, r in roles.items() if r[0] in ("function_generator", "calibrator") and r[1]]
    if len(scopes) == 1 and len(sources) == 1:
        (s_inst,), (g_inst,) = scopes, sources
        g_port = str(roles[g_inst][1]).replace("{n}", str(roles[g_inst][2]))
        s_port = str(roles[s_inst][1]).replace("{n}", str(roles[s_inst][2]))
        wires.append({"from": f"{g_inst}.{g_port}", "to": f"{s_inst}.{s_port}"})
        inferred.append(
            {
                "from": f"{g_inst}.{g_port}",
                "to": f"{s_inst}.{s_port}",
                "why": f"推断：脚本中信号源用通道 {roles[g_inst][2]}，示波器读通道 {roles[s_inst][2]}，按直连线缆接上",
            }
        )
        notes.append(
            f"连线 {g_inst}.{g_port} → {s_inst}.{s_port} 是推断的（脚本中看不出接线）；有 DUT 时请改为经过 DUT"
        )
    elif instruments:
        notes.append("脚本中看不出接线：草稿只有仪表，请补充 DUT 与连线")
    bench: dict[str, Any] = {
        "schema": "simubench.bench/2",
        "name": name,
        "description": "由脚本生成的草稿",
        "instruments": instruments,
        "layout": layout,
    }
    if wires:
        bench["wires"] = wires
    if instruments:
        # The same script gives the same numbers: a fixed seed, time moving with the commands, no idle drift.
        bench["run"] = {"seed": 1, "clock": "command", "idle": "off"}
        notes.append("运行方式取确定性的默认：固定种子 1、clock: command（虚拟时间随命令推进）、idle: off")
    return {"bench": bench, "report": report, "notes": notes, "inferred_wires": inferred}


# ---- definition drafts (instrument-authoring.md 5.4) --------------------------------------------------

NUMBER = re.compile(r"^([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([A-Za-z]*)$")
UNITS = ("HZ", "V", "A", "S", "OHM", "W", "DBM", "DB", "PCT")
PREFIXES = "PNUMKG"
DRAFT = "草稿（待确认）"


def _guess(values: list[str]) -> dict[str, Any]:
    """A state variable for the values a script writes: float (with its unit), bool, enum or string."""
    words = [v.strip() for v in values]
    if (
        words
        and all(w.upper() in ("ON", "OFF", "0", "1") for w in words)
        and any(w.upper() in ("ON", "OFF") for w in words)
    ):
        return {"type": "bool", "default": words[0].upper() in ("ON", "1")}
    numbers = [NUMBER.match(w) for w in words]
    if words and all(numbers):
        unit = None
        for m in numbers:
            suffix = m.group(2).upper()  # type: ignore[union-attr]
            for u in UNITS:
                if suffix == u or (suffix.endswith(u) and suffix[: -len(u)] in PREFIXES):
                    unit = u
        spec: dict[str, Any] = {"type": "float", "default": float(numbers[0].group(1))}  # type: ignore[union-attr]
        if unit:
            spec["unit"] = unit
            if numbers[0].group(2).upper() != unit:  # type: ignore[union-attr]
                spec["default"] = 0.0  # the first value carries a prefix: no default guessed from it
        return spec
    if words and all(w.startswith(("'", '"')) for w in words):
        return {"type": "string", "default": words[0].strip("'\"")}
    if words and all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", w) for w in words):
        options = list(dict.fromkeys(w.upper() for w in words))
        return {"type": "enum", "options": options, "default": options[0]}
    return {"type": "string", "default": words[0] if words else ""}


def draft_definition(
    source: str,
    definition_id: str,
    *,
    receiver: str | None = None,
    base: InstrumentDefinition | None = None,
) -> dict[str, Any]:
    """A definition draft for one instrument of a script: ``base`` (or an empty skeleton) plus a
    setting and a command for every header it does not support, typed from the values the script
    writes. Drafted entries are marked ``草稿（待确认）`` in their ``doc`` and listed in ``drafted``.

    Returns ``{"definition": <data>, "drafted": [...], "notes": [...]}``."""
    from ..scpi.errors import ScpiError
    from ..scpi.parse import Token, parse_unit, resolve_tokens, split_params, split_units

    scan = scan_python(source)
    notes = [*scan.notes]
    resources = {r.key: r for r in scan.resources if r.commands}
    if receiver is None:
        if len(resources) != 1:
            names = ", ".join(resources) or "（无）"
            raise ValueError(f"脚本中有多台仪器发送命令：{names}；用 receiver 指定其中一台")
        receiver = next(iter(resources))
    if receiver not in resources:
        raise ValueError(f"脚本中没有向 {receiver!r} 发送的命令；可选：{', '.join(resources)}")
    res = resources[receiver]
    if base is not None:
        data = base.model_dump(mode="json", by_alias=True, exclude_defaults=True)
        data.update(id=definition_id, schema="simubench.instrument/1")
        data.pop("reference", None)  # a contract definition, not the real model's
        known = base
    else:
        leaf = receiver.rsplit(".", 1)[-1].lower()
        cls = next((c for c, words in CLASS_HINTS.items() if any(w in leaf for w in words)), "other")
        data = {
            "schema": "simubench.instrument/1",
            "id": definition_id,
            "manufacturer": "TODO",
            "model": definition_id.upper(),
            "class": cls,
            "description": f"由脚本生成的{DRAFT}：命令与状态量按脚本中的写法推测",
            "channels": {"count": 1},
        }
        known = InstrumentDefinition.model_validate(data)

    # Every unsupported header with the parameter lists the script writes and the lines it is on.
    groups: dict[str, dict[str, Any]] = {}
    for cmd in res.commands:
        try:
            units = split_units(cmd.text.encode())
        except ScpiError:
            continue
        path: tuple[Token, ...] = ()
        for raw, match in zip(units, classify(known, cmd.text), strict=False):
            try:
                unit = parse_unit(raw)
            except ScpiError:
                continue
            tokens = resolve_tokens(unit, path)
            if not unit.common:
                path = tokens[:-1]
            if match.supported or unit.common:
                continue
            channel = any(t.suffix is not None for t in tokens)
            header = ("*" if unit.common else ":") + ":".join(
                t.keyword + ("<ch>" if t.suffix is not None else "") for t in tokens
            )
            g = groups.setdefault(
                header,
                {"tokens": tokens, "channel": channel, "sets": [], "query": False, "lines": [], "max_ch": 1},
            )
            g["lines"].append(cmd.line)
            g["max_ch"] = max([g["max_ch"], *[t.suffix or 1 for t in tokens]])
            if unit.query:
                g["query"] = True
            else:
                g["sets"].append([p.decode(errors="replace") for p in split_params(b",".join(unit.params))])
    state = data.setdefault("state", {})
    channel_state = data.setdefault("channel_state", {})
    commands = data.setdefault("commands", [])
    drafted = []
    for header, g in groups.items():
        target = channel_state if g["channel"] else state
        stem = "_".join(t.keyword.lower() for t in g["tokens"])
        width = max((len(s) for s in g["sets"]), default=1)
        names = []
        for k in range(width):
            name = stem if width == 1 else f"{stem}_{k + 1}"
            n = 2
            while name in target:
                name, n = f"{stem}_{n}", n + 1
            spec = _guess([s[k] for s in g["sets"] if len(s) > k])
            spec["label"] = f"{DRAFT} {name}"
            target[name] = spec
            names.append(name)
        prefix = "ch." if g["channel"] else ""
        lines = ", ".join(str(n) for n in sorted(set(g["lines"])))
        entry: dict[str, Any] = {"header": header, "doc": f"{DRAFT}：由脚本第 {lines} 行推测"}
        if width == 1:
            entry["state"] = prefix + names[0]
        else:
            entry["params"] = [
                {
                    "name": n,
                    "type": target[n]["type"],
                    "state": prefix + n,
                    **({"options": target[n]["options"]} if "options" in target[n] else {}),
                }
                for n in names
            ]
        if not g["sets"]:
            entry["set"] = False
        commands.append(entry)
        if g["channel"]:  # enough channels for the highest suffix the script uses
            channels = data.setdefault("channels", {"count": 1})
            channels["count"] = max(int(channels.get("count", 1)), g["max_ch"])
        drafted.append(
            {
                "header": header,
                "states": [prefix + n for n in names],
                "types": [target[n]["type"] for n in names],
                "lines": sorted(set(g["lines"])),
                "query": g["query"],
                "set": bool(g["sets"]),
            }
        )
    if not state:
        data.pop("state")
    if not channel_state:
        data.pop("channel_state")
    if drafted:
        notes.append(
            f"为 {len(drafted)} 条命令起草了设置与命令（标为“{DRAFT}”）：类型、范围与默认值由脚本中的写法推测，"
            "请核对后用 edit_definition 修改；电路行为（组件与连线）需要另行添加"
        )
    else:
        notes.append("脚本中的命令都已被支持，没有需要起草的部分")
    return {"definition": data, "drafted": drafted, "notes": notes}
