"""Bench validation with actionable diagnostics (architecture.md 3.5 and 4.4).

Every diagnostic carries a stable code, the location in the file as a JSON pointer, a message and,
when there is one, a suggestion. The UI marks the place on the canvas and agents use the
suggestion to correct their edits (D9).
"""

from __future__ import annotations

import difflib
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ValidationError

from ..models.bench import BenchDefinition
from ..models.definition import InstrumentDefinition
from .component import component_types

Severity = Literal["error", "warning"]


@dataclass
class Diagnostic:
    severity: Severity
    code: str
    pointer: str
    message: str
    suggestion: str | None = None
    subject: str | None = None  # instrument, DUT or wire the diagnostic is about

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "pointer": self.pointer,
            "message": self.message,
            "suggestion": self.suggestion,
            "subject": self.subject,
        }


@dataclass
class Report:
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(d.severity == "error" for d in self.diagnostics)

    def add(self, *args: Any, **kwargs: Any) -> None:
        self.diagnostics.append(Diagnostic(*args, **kwargs))


def _closest(word: str, options: list[str]) -> str | None:
    hit = difflib.get_close_matches(word, options, n=1, cutoff=0.5)
    return hit[0] if hit else None


def _bound(value: float | None, open_end: str = "-∞") -> str:
    return open_end if value is None else f"{value:g}"


def _ptr(*parts: object) -> str:
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def validate_data(
    data: Any, definitions: Callable[[str], InstrumentDefinition]
) -> tuple[BenchDefinition | None, Report]:
    """Validate raw bench data (parsed YAML/JSON); return the model when the schema accepts it."""
    report = Report()
    try:
        bench = BenchDefinition.model_validate(data)
    except ValidationError as exc:
        for err in exc.errors():
            loc = [p for p in err["loc"] if not (isinstance(p, str) and p.startswith("function-"))]
            report.add("error", "E-SCHEMA", _ptr(*loc), err["msg"], None)
        return None, report
    validate_bench(bench, definitions, report)
    return bench, report


def validate_bench(
    bench: BenchDefinition,
    definitions: Callable[[str], InstrumentDefinition],
    report: Report | None = None,
) -> Report:
    report = report or Report()
    known_types = component_types()
    port_info: dict[
        str, dict[str, tuple[str, str, int | None]]
    ] = {}  # owner -> port -> (medium, dir, channel)
    defs: dict[str, InstrumentDefinition] = {}

    # ---- instruments ----
    for name, inst in bench.instruments.items():
        p = ("instruments", name)
        try:
            d = definitions(inst.model)
        except KeyError:
            from ..library.loader import DefinitionLibrary

            options = DefinitionLibrary().ids()
            hint = _closest(inst.model, options)
            report.add(
                "error",
                "E-MODEL-UNKNOWN",
                _ptr(*p, "model"),
                f"仪表定义 {inst.model!r} 不存在",
                f"改为 {hint!r}" if hint else f"可用的仪表定义：{', '.join(options)}",
                name,
            )
            continue
        defs[name] = d
        ports: dict[str, tuple[str, str, int | None]] = {}
        for n in range(1, d.channels.count + 1):
            for pname in d.ports.channel:
                ports[pname.replace("{n}", str(n))] = ("electrical", "inout", n)
        for pname in d.ports.instrument:
            ports[pname] = ("electrical", "inout", None)
        port_info[name] = ports
        specs = {**d.state}
        for n in range(1, d.channels.count + 1):
            for k, s in d.channel_state.items():
                specs[f"ch{n}.{k}"] = s
        for path, value in inst.state.items():
            sp = ("instruments", name, "state", path)
            spec = specs.get(path)
            if spec is None:
                hint = _closest(path, list(specs))
                report.add(
                    "error",
                    "E-STATE-UNKNOWN",
                    _ptr(*sp),
                    f"{d.model} 没有状态 {path!r}",
                    f"改为 {hint!r}" if hint else None,
                    name,
                )
                continue
            if spec.type in ("float", "int"):
                try:
                    v = float(value)
                except (TypeError, ValueError):
                    report.add(
                        "error", "E-STATE-TYPE", _ptr(*sp), f"{path} 需要数值，得到 {value!r}", None, name
                    )
                    continue
                special = set((spec.special or {}).values())
                if v not in special and (
                    (spec.min is not None and v < spec.min) or (spec.max is not None and v > spec.max)
                ):
                    report.add(
                        "error",
                        "E-STATE-RANGE",
                        _ptr(*sp),
                        f"{path} = {v:g} 超出范围 [{_bound(spec.min)}, {_bound(spec.max, '∞')}]",
                        f"取 {_bound(spec.min)} 到 {_bound(spec.max, '∞')} 之间的值",
                        name,
                    )
            elif spec.type == "enum":
                opts = spec.options or []
                ok = any(
                    str(value).upper() in (o.upper(), "".join(c for c in o if c.isupper() or c.isdigit()))
                    for o in opts
                )
                if not ok:
                    report.add(
                        "error",
                        "E-STATE-OPTION",
                        _ptr(*sp),
                        f"{path} 不接受 {value!r}",
                        f"可选值：{', '.join(opts)}",
                        name,
                    )

    # ---- DUTs ----
    for name, dut in bench.duts.items():
        p = ("duts", name)
        if name in bench.instruments:
            report.add(
                "error",
                "E-NAME-DUPLICATE",
                _ptr(*p),
                f"名称 {name!r} 同时用于仪表与 DUT",
                "为 DUT 换一个名称",
                name,
            )
        cls = known_types.get(dut.type)
        if cls is None:
            hint = _closest(dut.type, list(known_types))
            report.add(
                "error",
                "E-COMPONENT-UNKNOWN",
                _ptr(*p, "type"),
                f"组件类型 {dut.type!r} 不存在",
                f"改为 {hint!r}" if hint else None,
                name,
            )
            continue
        for k in dut.params:
            if k not in cls.defaults:
                hint = _closest(k, list(cls.defaults))
                report.add(
                    "warning",
                    "W-PARAM-UNKNOWN",
                    _ptr(*p, "params", k),
                    f"{dut.type} 没有参数 {k!r}，它不会起作用",
                    f"改为 {hint!r}" if hint else None,
                    name,
                )
        port_info[name] = {pn: (ps.medium, ps.direction, None) for pn, ps in cls.ports.items()}

    # ---- wires ----
    connected: set[str] = set()
    for i, w in enumerate(bench.wires):
        for end, ref in (("from", w.from_), ("to", w.to)):
            owner, _, port = ref.rpartition(".")
            pp = _ptr("wires", i, end)
            if not owner or owner not in port_info:
                names = list(port_info)
                hint = _closest(owner or ref, names)
                report.add(
                    "error",
                    "E-WIRE-OWNER",
                    pp,
                    f"连线端点 {ref!r}：没有名为 {owner or ref!r} 的仪表或 DUT",
                    f"改为 {hint}.{port}" if hint else "端点写作 <名称>.<端口>",
                    f"wire:{i}",
                )
                continue
            ports = port_info[owner]
            if port not in ports:
                hint = _closest(port, list(ports))
                report.add(
                    "error",
                    "E-WIRE-PORT",
                    pp,
                    f"连线端点 {ref!r}：{owner} 没有端口 {port!r}",
                    f"改为 {owner}.{hint}" if hint else f"可用端口：{', '.join(ports)}",
                    f"wire:{i}",
                )
                continue
            if ref in connected:
                report.add(
                    "warning",
                    "W-PORT-SHARED",
                    pp,
                    f"端口 {ref} 连了多根线，它们会并在同一个网络上",
                    None,
                    f"wire:{i}",
                )
            connected.add(ref)
        for k, acc in enumerate(w.accessories):
            if acc.type not in known_types:
                hint = _closest(acc.type, list(known_types))
                report.add(
                    "error",
                    "E-ACCESSORY-UNKNOWN",
                    _ptr("wires", i, "accessories", k, "type"),
                    f"配件类型 {acc.type!r} 不存在",
                    f"改为 {hint!r}" if hint else None,
                    f"wire:{i}",
                )

    # ---- preset faults ----
    for i, f in enumerate(bench.faults):
        fp = ("faults", i)
        if f.instrument is not None and f.instrument not in bench.instruments:
            hint = _closest(f.instrument, list(bench.instruments))
            report.add(
                "error",
                "E-FAULT-TARGET",
                _ptr(*fp, "instrument"),
                f"故障指向不存在的仪表 {f.instrument!r}",
                f"改为 {hint!r}" if hint else None,
            )
        if f.kind in ("drift", "hidden"):
            ref = f.component or ""
            owner = ref.partition("/")[0] if "/" in ref else ref
            if not f.param or owner not in (bench.instruments if "/" in ref else bench.duts):
                report.add(
                    "error",
                    "E-FAULT-TARGET",
                    _ptr(*fp, "component"),
                    f"{f.kind} 故障需要 component（DUT 名称或 <仪表>/<组件>）与 param，得到 {ref!r}",
                )
        elif f.instrument is None:
            report.add("error", "E-FAULT-TARGET", _ptr(*fp), f"{f.kind} 故障需要 instrument")

    # ---- nets: one driver per net, impedance hints ----
    if report.ok:
        _check_nets(bench, defs, report)
    return report


def _check_nets(
    bench: BenchDefinition,
    defs: dict[str, InstrumentDefinition],
    report: Report,
) -> None:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(b)] = find(a)

    for i, w in enumerate(bench.wires):
        chain = [w.from_]
        for k, _acc in enumerate(w.accessories):
            chain += [f"wire{i}.acc{k}.IN", f"wire{i}.acc{k}.OUT"]
        chain.append(w.to)
        for a, b in zip(chain[0::2], chain[1::2], strict=True):
            union(a, b)

    def is_driver(ref: str) -> bool:
        owner, _, port = ref.rpartition(".")
        if owner in defs:
            d = defs[owner]
            comp_names = {c.name: c.type for c in d.channel_components} | {
                c.name: c.type for c in d.components
            }
            target = None
            for pname, t in d.ports.channel.items():
                for n in range(1, d.channels.count + 1):
                    if pname.replace("{n}", str(n)) == port:
                        target = t
            target = target or d.ports.instrument.get(port)
            if target:
                comp = target.rsplit(".", 1)[0]
                return comp_names.get(comp) in ("output_stage",)
            return False
        dut = bench.duts.get(owner)
        if dut is not None:
            return dut.type == "unity_buffer" and port == "OUT"
        return False

    nets: dict[str, list[str]] = {}
    for ref in list(parent):
        nets.setdefault(find(ref), []).append(ref)
    for members in nets.values():
        srcs = sorted(m for m in members if is_driver(m))
        if len(srcs) > 1:
            idx = next(
                (i for i, w in enumerate(bench.wires) if w.from_ in members or w.to in members),
                0,
            )
            report.add(
                "error",
                "E-NET-MULTIDRIVER",
                _ptr("wires", idx),
                f"一个网络只能有一个驱动源，这里有 {len(srcs)} 个：{', '.join(srcs)}",
                "断开其中一个输出，或在两者之间加入器件",
                f"wire:{idx}",
            )
        _impedance_hints(bench, defs, members, report)


def _impedance_hints(
    bench: BenchDefinition, defs: dict[str, InstrumentDefinition], members: list[str], report: Report
) -> None:
    """Warn when a source's amplitude assumes a 50 ohm load but the net has no 50 ohm termination."""
    for ref in members:
        owner, _, port = ref.rpartition(".")
        d = defs.get(owner)
        if d is None or d.instrument_class != "function_generator":
            continue
        inst = bench.instruments[owner]
        ch = "".join(c for c in port if c.isdigit()) or "1"
        load = inst.state.get(
            f"ch{ch}.load", d.channel_state.get("load").default if d.channel_state.get("load") else None
        )
        try:
            load_v = float(load) if load is not None else math.inf
        except (TypeError, ValueError):
            load_v = math.inf
        if not math.isfinite(load_v) or load_v > 1000:
            continue
        terminated = any(
            m != ref
            and (m.rpartition(".")[0] in defs)
            and bench.instruments[m.rpartition(".")[0]].state.get(
                f"ch{''.join(c for c in m.rpartition('.')[2] if c.isdigit()) or '1'}.impedance"
            )
            in ("FIFTy", "FIFT", 50)
            for m in members
        )
        if not terminated:
            report.add(
                "warning",
                "W-IMPEDANCE-MISMATCH",
                _ptr("instruments", owner, "state", f"ch{ch}.load"),
                f"{owner}.{port} 的幅度按 {load_v:g} Ω 负载设定，但所在网络没有 {load_v:g} Ω 端接；实际电压会偏高（接近高阻时约为两倍）",
                f"把 {owner} CH{ch} 的输出负载设为 INF（高阻），或在接收端使用 50 Ω 输入",
                owner,
            )
