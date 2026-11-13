"""Bench edit operations, shared by the Web Bench canvas and the MCP service (D9, architecture.md 4.3).

Operations act on a bench definition and return a new one; they never touch a running session
(topology changes take effect when the bench is started again, D8). Each operation checks what it
can check locally and raises :class:`EditError` with a message an agent can act on; whole-bench
checks are left to :mod:`simubench.engine.validate`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from ..models.bench import Accessory, BenchDefinition, ComponentInstance, InstrumentInstance, LayoutItem, Wire
from ..models.deviation import DeviationRange
from .portref import closest, parse, split

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,47}$")


class EditError(ValueError):
    """An operation that cannot be applied, with the reason."""


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AddInstrument(_Op):
    op: Literal["add_instrument"] = "add_instrument"
    name: str
    model: str
    label: str | None = None
    address: str | None = None
    x: float | None = None
    y: float | None = None


class AddPart(_Op):
    """A part (元件) as a node of its own: an RC network, a resistor, a buffer."""

    op: Literal["add_part"] = "add_part"
    name: str
    type: str
    label: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    dut: bool = Field(False, description="Mark it as a DUT (the object under test)")
    x: float | None = None
    y: float | None = None


class AddDut(AddPart):
    """Older name of add_part that marks the part as a DUT (bench/1, before DUT became a role)."""

    op: Literal["add_dut"] = "add_dut"  # type: ignore[assignment]
    dut: bool = True


class SetDut(_Op):
    """Mark an instrument or a part as a DUT, or not (DUT is a role, #100)."""

    op: Literal["set_dut"] = "set_dut"
    name: str
    dut: bool = True


class Remove(_Op):
    op: Literal["remove"] = "remove"
    name: str = Field(..., description="Instrument or part; its wires are removed too")


class ReplaceInstrument(_Op):
    """Swap an instrument's model for another, keeping its name, label, address and place: wires
    on ports the new model also has stay, the others are removed; settings the new model also has
    stay, the rest (and the identity overrides of the old unit) are dropped; so are options the new
    model does not declare."""

    op: Literal["replace_instrument"] = "replace_instrument"
    name: str
    model: str


class Rename(_Op):
    op: Literal["rename"] = "rename"
    name: str
    new_name: str


class Connect(_Op):
    op: Literal["connect"] = "connect"
    from_: str = Field(..., alias="from", description="Port reference, e.g. 'awg.OUT1'")
    to: str
    accessories: list[Accessory] = Field(default_factory=list)


class Disconnect(_Op):
    op: Literal["disconnect"] = "disconnect"
    index: int = Field(..., ge=0, description="Wire index")


class SetState(_Op):
    op: Literal["set_state"] = "set_state"
    instrument: str
    path: str
    value: Any = Field(None, description="Initial value; null removes the entry")


class SetParams(_Op):
    op: Literal["set_params"] = "set_params"
    part: str = Field(
        ..., validation_alias=AliasChoices("part", "dut"), description="The part (older name: dut)"
    )
    params: dict[str, Any]


class SetAccessories(_Op):
    op: Literal["set_accessories"] = "set_accessories"
    index: int = Field(..., ge=0)
    accessories: list[Accessory]


class SetLabel(_Op):
    op: Literal["set_label"] = "set_label"
    name: str
    label: str | None = None


class SetAddress(_Op):
    op: Literal["set_address"] = "set_address"
    instrument: str
    address: str | None = None


class SetIdentity(_Op):
    op: Literal["set_identity"] = "set_identity"
    instrument: str
    idn: str | None = Field(
        None,
        description="*IDN? reply, e.g. 'LECROY,WAVEPRO 254HD,{serial},{firmware}'; null: the definition's",
    )
    serial: str | None = None
    firmware: str | None = None


class SetDeviation(_Op):
    """Individual deviations (个体偏差): an instrument's mode, or the range of one parameter of a part."""

    op: Literal["set_deviation"] = "set_deviation"
    instrument: str | None = Field(None, description="With `mode`: the instrument")
    mode: Literal["nominal", "from_specs"] | None = Field(
        None, description="nominal: the model's values; from_specs: drawn within the definition's deviations"
    )
    part: str | None = Field(
        None,
        validation_alias=AliasChoices("part", "dut"),
        description="With `param`: the part (older name: dut)",
    )
    param: str | None = None
    range: DeviationRange | None = Field(None, description="The parameter's range; null removes it")


class SetOptions(_Op):
    """The hardware options (选件) an instrument's unit has, replacing the list (D68): ids of the
    definition's ``options``. They take effect when the bench is opened, like any structural edit."""

    op: Literal["set_options"] = "set_options"
    instrument: str
    options: list[str] = Field(default_factory=list, description="Option ids, e.g. ['P03']; [] for none")


class Move(_Op):
    op: Literal["move"] = "move"
    name: str
    x: float
    y: float


class SetMeta(_Op):
    op: Literal["set_meta"] = "set_meta"
    name: str | None = None
    description: str | None = None


class SetRun(_Op):
    op: Literal["set_run"] = "set_run"
    seed: int | None = None
    clock: Literal["command", "wall", "stepped", "scaled"] | None = None
    time_scale: float | None = Field(None, gt=0, le=1_000_000)
    reveal_true_values: bool | None = None


EditOp = Annotated[
    AddInstrument
    | AddPart
    | AddDut
    | SetDut
    | Remove
    | ReplaceInstrument
    | Rename
    | Connect
    | Disconnect
    | SetState
    | SetParams
    | SetAccessories
    | SetLabel
    | SetAddress
    | SetIdentity
    | SetDeviation
    | SetOptions
    | Move
    | SetMeta
    | SetRun,
    Field(discriminator="op"),
]


def _names(b: BenchDefinition) -> set[str]:
    return set(b.instruments) | set(b.parts)


def _check_new_name(b: BenchDefinition, name: str) -> None:
    if not NAME.match(name):
        raise EditError(
            f"名称 {name!r} 无效：以字母或下划线开头，只含字母、数字、下划线和连字符，最长 48 个字符"
        )
    if name in _names(b):
        raise EditError(f"名称 {name!r} 已被使用")


def _owner(ref: str) -> str:
    r = parse(ref)
    if r is None:
        raise EditError(f"端口引用 {ref!r} 应写作 <名称>.<端口>")
    return r.owner


def _free_position(b: BenchDefinition) -> LayoutItem:
    xs = [item.x for item in b.layout.values()] or [-320.0]
    return LayoutItem(x=max(xs) + 320, y=60)


def model_surface(d: Any) -> tuple[set[str], set[str]]:
    """The port names and state paths of an instrument definition, channels expanded."""
    count = d.channels.count
    ports = {p.replace("{n}", str(n)) for n in range(1, count + 1) for p in d.ports.channel}
    ports |= set(d.ports.instrument)
    paths = set(d.state) | {f"ch{n}.{k}" for n in range(1, count + 1) for k in d.channel_state}
    return ports, paths


def apply(
    bench: BenchDefinition, ops: list[Any], library: Callable[[str], Any] | None = None
) -> BenchDefinition:
    """Apply operations in order to a copy of ``bench``; all or nothing. ``library`` resolves a
    definition id; only ``replace_instrument`` needs it (which ports and settings carry over)."""
    b = bench.model_copy(deep=True)
    for i, op in enumerate(ops):
        try:
            if isinstance(op, ReplaceInstrument):
                _replace(b, op, library)
                continue
            if isinstance(op, SetOptions):
                _set_options(b, op, library)
                continue
            _apply_one(b, op)
        except EditError as exc:
            raise EditError(f"第 {i + 1} 个操作 {getattr(op, 'op', '?')}：{exc}") from None
    return b


def _set_options(b: BenchDefinition, op: SetOptions, library: Callable[[str], Any] | None) -> None:
    inst = b.instruments.get(op.instrument)
    if inst is None:
        raise EditError(f"没有名为 {op.instrument!r} 的仪表")
    wanted = list(dict.fromkeys(op.options))
    if library is not None:
        try:
            declared = [o.id for o in library(inst.model).options]
        except (KeyError, FileNotFoundError, ValueError):
            declared = None  # an unknown model is the bench's own diagnostic (E-MODEL-UNKNOWN)
        if declared is not None:
            if unknown := [o for o in wanted if o not in declared]:
                hint = f"可用的选件：{', '.join(declared)}" if declared else f"{inst.model} 没有声明选件"
                raise EditError(
                    f"E-OPTION-UNKNOWN：仪表定义 {inst.model} 没有选件 {', '.join(unknown)}；{hint}"
                )
            wanted = [o for o in declared if o in wanted]  # the definition's order
    inst.options = wanted


def _replace(b: BenchDefinition, op: ReplaceInstrument, library: Callable[[str], Any] | None) -> None:
    inst = b.instruments.get(op.name)
    if inst is None:
        raise EditError(f"没有名为 {op.name!r} 的仪表")
    if library is None:
        raise EditError("替换仪表需要仪表库，在工作区中编辑测试台文件时可用")
    try:
        definition = library(op.model)
        ports, paths = model_surface(definition)
    except (KeyError, FileNotFoundError, ValueError):
        raise EditError(f"没有仪表定义 {op.model!r}") from None
    inst.model = op.model
    inst.options = [o for o in inst.options if o in {opt.id for opt in definition.options}]
    inst.state = {k: v for k, v in inst.state.items() if k in paths}
    inst.idn = inst.serial = inst.firmware = None
    inst.hidden = {}

    def kept(ref: str) -> bool:
        r = parse(ref)
        return r is None or r.owner != op.name or r.port in ports

    b.wires = [w for w in b.wires if kept(w.from_) and kept(w.to)]


_IPV4 = re.compile(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.)(\d{1,3})")
_TRAILING_DIGITS = re.compile(r"^(.*?)(\d*)$")


def _next_address(address: str) -> str | None:
    """The address one step on: the next GPIB primary address (1–30), the next IPv4 host of a
    TCPIP resource, else the next number at the end of the resource's identifying field (a USB
    serial, a host name). None when it cannot step."""
    parts = address.split("::")
    if len(parts) < 2:
        return None
    board = parts[0].upper()
    if board.startswith("GPIB") and parts[1].isdigit():
        primary = int(parts[1]) % 30 + 1
        return "::".join([parts[0], str(primary), *parts[2:]])
    if board.startswith("TCPIP") and (m := _IPV4.fullmatch(parts[1])):
        host = int(m.group(2)) % 254 + 1
        return "::".join([parts[0], f"{m.group(1)}{host}", *parts[2:]])
    i = len(parts) - 2 if parts[-1].upper() in ("INSTR", "SOCKET") and len(parts) > 2 else len(parts) - 1
    m = _TRAILING_DIGITS.match(parts[i])
    assert m is not None  # (.*?)(\d*)$ matches anything, even the empty string
    stem, digits = m.groups()
    width = len(digits)
    parts[i] = f"{stem}{int(digits or '1') + 1:0{width}d}"
    return "::".join(parts)


def free_address(b: BenchDefinition, address: str | None) -> str | None:
    """``address``, or — when another instrument of the bench already has it — the first free one
    after it (#874): the platform resolves the conflict, the user only needs an address that
    works. None stays None (the instrument answers at its virtual address)."""
    if address is None:
        return None
    taken = {i.address.upper() for i in b.instruments.values() if i.address}
    candidate: str | None = address
    for _ in range(1000):
        if candidate is None or candidate.upper() not in taken:
            break
        candidate = _next_address(candidate)
    if candidate is None or candidate.upper() in taken:
        raise EditError(f"地址 {address} 已被使用，且找不到空闲地址；用 set_address 指定一个")
    return candidate


def _apply_one(b: BenchDefinition, op: Any) -> None:
    if isinstance(op, AddInstrument):
        _check_new_name(b, op.name)
        b.instruments[op.name] = InstrumentInstance(
            model=op.model, label=op.label, address=free_address(b, op.address)
        )
        b.layout[op.name] = (
            LayoutItem(x=op.x, y=op.y) if op.x is not None and op.y is not None else _free_position(b)
        )
    elif isinstance(op, AddPart):
        _check_new_name(b, op.name)
        b.parts[op.name] = ComponentInstance(type=op.type, label=op.label, params=op.params, dut=op.dut)
        b.layout[op.name] = (
            LayoutItem(x=op.x, y=op.y) if op.x is not None and op.y is not None else _free_position(b)
        )
    elif isinstance(op, Remove):
        if op.name not in _names(b):
            raise EditError(f"没有名为 {op.name!r} 的仪表或元件")
        b.instruments.pop(op.name, None)
        b.parts.pop(op.name, None)
        b.layout.pop(op.name, None)
        b.wires = [w for w in b.wires if _owner(w.from_) != op.name and _owner(w.to) != op.name]
    elif isinstance(op, Rename):
        if op.name not in _names(b):
            raise EditError(f"没有名为 {op.name!r} 的仪表或元件")
        _check_new_name(b, op.new_name)
        if op.name in b.instruments:
            b.instruments = {(op.new_name if k == op.name else k): v for k, v in b.instruments.items()}
        else:
            b.parts = {(op.new_name if k == op.name else k): v for k, v in b.parts.items()}
        if op.name in b.layout:
            b.layout[op.new_name] = b.layout.pop(op.name)

        def ren(ref: str) -> str:
            r = split(ref)
            return f"{op.new_name}.{r.port}" if r.owner == op.name else ref

        for w in b.wires:
            w.from_ = ren(w.from_)
            w.to = ren(w.to)
    elif isinstance(op, Connect):
        for ref in (op.from_, op.to):
            if _owner(ref) not in _names(b):
                r = split(ref)
                hint = closest(r.owner, sorted(_names(b)))
                raise EditError(
                    f"端口引用 {ref!r}：没有名为 {r.owner!r} 的仪表或元件"
                    + (f"，改为 {hint}.{r.port}" if hint else "")
                )
        if op.from_ == op.to:
            raise EditError("连线的两端不能是同一个端口")
        if any({w.from_, w.to} == {op.from_, op.to} for w in b.wires):
            raise EditError(f"{op.from_} 与 {op.to} 之间已有连线")
        b.wires.append(Wire(**{"from": op.from_}, to=op.to, accessories=op.accessories))
    elif isinstance(op, Disconnect):
        if op.index >= len(b.wires):
            raise EditError(f"没有第 {op.index} 根连线（共 {len(b.wires)} 根）")
        b.wires.pop(op.index)
    elif isinstance(op, SetState):
        if op.instrument not in b.instruments:
            raise EditError(f"没有名为 {op.instrument!r} 的仪表")
        st = b.instruments[op.instrument].state
        if op.value is None:
            st.pop(op.path, None)
        else:
            st[op.path] = op.value
    elif isinstance(op, SetParams):
        if op.part not in b.parts:
            raise EditError(f"没有名为 {op.part!r} 的元件")
        b.parts[op.part].params.update(op.params)
    elif isinstance(op, SetAccessories):
        if op.index >= len(b.wires):
            raise EditError(f"没有第 {op.index} 根连线")
        b.wires[op.index].accessories = op.accessories
    elif isinstance(op, SetLabel):
        if op.name in b.instruments:
            b.instruments[op.name].label = op.label
        elif op.name in b.parts:
            b.parts[op.name].label = op.label
        else:
            raise EditError(f"没有名为 {op.name!r} 的仪表或元件")
    elif isinstance(op, SetAddress):
        if op.instrument not in b.instruments:
            raise EditError(f"没有名为 {op.instrument!r} 的仪表")
        b.instruments[op.instrument].address = op.address
    elif isinstance(op, SetIdentity):
        if op.instrument not in b.instruments:
            raise EditError(f"没有名为 {op.instrument!r} 的仪表")
        inst = b.instruments[op.instrument]
        inst.idn, inst.serial, inst.firmware = op.idn, op.serial, op.firmware
    elif isinstance(op, SetDeviation):
        if op.instrument is not None:
            if op.instrument not in b.instruments or op.mode is None or op.part is not None:
                raise EditError(
                    f"set_deviation 需要已有的仪表与 mode（没有名为 {op.instrument!r} 的仪表或缺少 mode）"
                )
            b.instruments[op.instrument].deviation = op.mode
        elif op.part is not None:
            if op.part not in b.parts or not op.param:
                raise EditError(
                    f"set_deviation 需要已有的元件与 param（没有名为 {op.part!r} 的元件或缺少 param）"
                )
            if op.range is None:
                b.parts[op.part].deviation.pop(op.param, None)
            else:
                b.parts[op.part].deviation[op.param] = op.range
        else:
            raise EditError("set_deviation 需要 instrument（与 mode）或 part（与 param）")
    elif isinstance(op, SetDut):
        if op.name in b.instruments:
            b.instruments[op.name].dut = op.dut
        elif op.name in b.parts:
            b.parts[op.name].dut = op.dut
        else:
            raise EditError(f"没有名为 {op.name!r} 的仪表或元件")
    elif isinstance(op, Move):
        if op.name not in _names(b):
            raise EditError(f"没有名为 {op.name!r} 的仪表或元件")
        b.layout[op.name] = LayoutItem(x=op.x, y=op.y)
    elif isinstance(op, SetMeta):
        if op.name is not None:
            b.name = op.name
        if op.description is not None:
            b.description = op.description or None
    elif isinstance(op, SetRun):
        if op.seed is not None:
            b.run.seed = op.seed
        if op.clock is not None:
            b.run.clock = op.clock
        if op.time_scale is not None:
            b.run.time_scale = op.time_scale
        if op.reveal_true_values is not None:
            b.run.reveal_true_values = op.reveal_true_values
    else:
        raise EditError(f"不支持的操作 {op!r}")


def to_yaml_data(bench: BenchDefinition) -> dict[str, Any]:
    """A tidy mapping for writing bench files: defaults and empty sections are left out."""
    data = bench.model_dump(mode="json", by_alias=True, exclude_none=True, exclude_defaults=True)
    out: dict[str, Any] = {"schema": "simubench.bench/2", "name": bench.name}
    for key in ("description", "instruments", "parts", "wires", "run", "faults", "assets", "layout"):
        if data.get(key):
            out[key] = data[key]
    if bench.layout:  # coordinates are always written, 0 included
        out["layout"] = {k: v.model_dump(exclude_none=True) for k, v in bench.layout.items()}
    for w in out.get("wires", []):
        if not w.get("accessories"):
            w.pop("accessories", None)
    return out
