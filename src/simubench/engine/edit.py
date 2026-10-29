"""Bench edit operations, shared by the Web Bench canvas and the MCP service (D9, architecture.md 4.3).

Operations act on a bench definition and return a new one; they never touch a running session
(topology changes take effect when the bench is started again, D8). Each operation checks what it
can check locally and raises :class:`EditError` with a message an agent can act on; whole-bench
checks are left to :mod:`simubench.engine.validate`.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..models.bench import Accessory, BenchDefinition, ComponentInstance, InstrumentInstance, LayoutItem, Wire
from ..models.deviation import DeviationRange

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


class AddDut(_Op):
    op: Literal["add_dut"] = "add_dut"
    name: str
    type: str
    label: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    x: float | None = None
    y: float | None = None


class Remove(_Op):
    op: Literal["remove"] = "remove"
    name: str = Field(..., description="Instrument or DUT; its wires are removed too")


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
    dut: str
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
    """Individual deviations (个体偏差): an instrument's mode, or one DUT parameter's range."""

    op: Literal["set_deviation"] = "set_deviation"
    instrument: str | None = Field(None, description="With `mode`: the instrument")
    mode: Literal["nominal", "from_specs"] | None = Field(
        None, description="nominal: the model's values; from_specs: drawn within the definition's deviations"
    )
    dut: str | None = Field(None, description="With `param`: the DUT")
    param: str | None = None
    range: DeviationRange | None = Field(None, description="The parameter's range; null removes it")


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
    clock: Literal["command", "wall", "stepped"] | None = None
    reveal_true_values: bool | None = None


EditOp = Annotated[
    AddInstrument
    | AddDut
    | Remove
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
    | Move
    | SetMeta
    | SetRun,
    Field(discriminator="op"),
]


def _names(b: BenchDefinition) -> set[str]:
    return set(b.instruments) | set(b.duts)


def _check_new_name(b: BenchDefinition, name: str) -> None:
    if not NAME.match(name):
        raise EditError(
            f"名称 {name!r} 无效：以字母或下划线开头，只含字母、数字、下划线和连字符，最长 48 个字符"
        )
    if name in _names(b):
        raise EditError(f"名称 {name!r} 已被使用")


def _owner(ref: str) -> str:
    owner, _, port = ref.rpartition(".")
    if not owner or not port:
        raise EditError(f"端口引用 {ref!r} 应写作 <名称>.<端口>")
    return owner


def _free_position(b: BenchDefinition) -> LayoutItem:
    xs = [item.x for item in b.layout.values()] or [-320.0]
    return LayoutItem(x=max(xs) + 320, y=60)


def apply(bench: BenchDefinition, ops: list[Any]) -> BenchDefinition:
    """Apply operations in order to a copy of ``bench``; all or nothing."""
    b = bench.model_copy(deep=True)
    for i, op in enumerate(ops):
        try:
            _apply_one(b, op)
        except EditError as exc:
            raise EditError(f"第 {i + 1} 个操作 {getattr(op, 'op', '?')}：{exc}") from None
    return b


def _apply_one(b: BenchDefinition, op: Any) -> None:
    if isinstance(op, AddInstrument):
        _check_new_name(b, op.name)
        b.instruments[op.name] = InstrumentInstance(model=op.model, label=op.label, address=op.address)
        b.layout[op.name] = (
            LayoutItem(x=op.x, y=op.y) if op.x is not None and op.y is not None else _free_position(b)
        )
    elif isinstance(op, AddDut):
        _check_new_name(b, op.name)
        b.duts[op.name] = ComponentInstance(type=op.type, label=op.label, params=op.params)
        b.layout[op.name] = (
            LayoutItem(x=op.x, y=op.y) if op.x is not None and op.y is not None else _free_position(b)
        )
    elif isinstance(op, Remove):
        if op.name not in _names(b):
            raise EditError(f"没有名为 {op.name!r} 的仪表或 DUT")
        b.instruments.pop(op.name, None)
        b.duts.pop(op.name, None)
        b.layout.pop(op.name, None)
        b.wires = [w for w in b.wires if _owner(w.from_) != op.name and _owner(w.to) != op.name]
    elif isinstance(op, Rename):
        if op.name not in _names(b):
            raise EditError(f"没有名为 {op.name!r} 的仪表或 DUT")
        _check_new_name(b, op.new_name)
        if op.name in b.instruments:
            b.instruments = {(op.new_name if k == op.name else k): v for k, v in b.instruments.items()}
        else:
            b.duts = {(op.new_name if k == op.name else k): v for k, v in b.duts.items()}
        if op.name in b.layout:
            b.layout[op.new_name] = b.layout.pop(op.name)

        def ren(ref: str) -> str:
            owner, _, port = ref.rpartition(".")
            return f"{op.new_name}.{port}" if owner == op.name else ref

        for w in b.wires:
            w.from_ = ren(w.from_)
            w.to = ren(w.to)
    elif isinstance(op, Connect):
        for ref in (op.from_, op.to):
            if _owner(ref) not in _names(b):
                raise EditError(f"端口引用 {ref!r}：没有名为 {_owner(ref)!r} 的仪表或 DUT")
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
        if op.dut not in b.duts:
            raise EditError(f"没有名为 {op.dut!r} 的 DUT")
        b.duts[op.dut].params.update(op.params)
    elif isinstance(op, SetAccessories):
        if op.index >= len(b.wires):
            raise EditError(f"没有第 {op.index} 根连线")
        b.wires[op.index].accessories = op.accessories
    elif isinstance(op, SetLabel):
        if op.name in b.instruments:
            b.instruments[op.name].label = op.label
        elif op.name in b.duts:
            b.duts[op.name].label = op.label
        else:
            raise EditError(f"没有名为 {op.name!r} 的仪表或 DUT")
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
            if op.instrument not in b.instruments or op.mode is None or op.dut is not None:
                raise EditError(
                    f"set_deviation 需要已有的仪表与 mode（没有名为 {op.instrument!r} 的仪表或缺少 mode）"
                )
            b.instruments[op.instrument].deviation = op.mode
        elif op.dut is not None:
            if op.dut not in b.duts or not op.param:
                raise EditError(
                    f"set_deviation 需要已有的 DUT 与 param（没有名为 {op.dut!r} 的 DUT 或缺少 param）"
                )
            if op.range is None:
                b.duts[op.dut].deviation.pop(op.param, None)
            else:
                b.duts[op.dut].deviation[op.param] = op.range
        else:
            raise EditError("set_deviation 需要 instrument（与 mode）或 dut（与 param）")
    elif isinstance(op, Move):
        if op.name not in _names(b):
            raise EditError(f"没有名为 {op.name!r} 的仪表或 DUT")
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
        if op.reveal_true_values is not None:
            b.run.reveal_true_values = op.reveal_true_values
    else:
        raise EditError(f"不支持的操作 {op!r}")


def to_yaml_data(bench: BenchDefinition) -> dict[str, Any]:
    """A tidy mapping for writing bench files: defaults and empty sections are left out."""
    data = bench.model_dump(mode="json", by_alias=True, exclude_none=True, exclude_defaults=True)
    out: dict[str, Any] = {"schema": "simubench.bench/1", "name": bench.name}
    for key in ("description", "instruments", "duts", "wires", "run", "faults", "assets", "layout"):
        if data.get(key):
            out[key] = data[key]
    if bench.layout:  # coordinates are always written, 0 included
        out["layout"] = {k: v.model_dump(exclude_none=True) for k, v in bench.layout.items()}
    for w in out.get("wires", []):
        if not w.get("accessories"):
            w.pop("accessories", None)
    return out
