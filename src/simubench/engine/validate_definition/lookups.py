"""Lookups the checks share: a state reference's spec and options, a command header's pattern, an operation's names, a component class's protocol gaps."""

from __future__ import annotations

import re

from typing_extensions import get_protocol_members

from ...models.definition import InstrumentDefinition, StateSpec
from ...scpi.parse import Pattern
from ..component import Component
from ..validate import Report, unknown_name

_NODE = re.compile(r"\[:?[A-Za-z_][A-Za-z0-9_]*(?:<\w+>)?\]|:?[A-Za-z_][A-Za-z0-9_]*(?:<\w+>)?")


def _compile(header: str) -> Pattern | None:
    """The header's pattern, or None when part of the text is not a header node."""
    text = header.strip()
    if text.startswith("*"):
        return Pattern.compile(text) if re.fullmatch(r"\*[A-Za-z]+", text) else None
    if not text or _NODE.sub("", text):
        return None
    try:
        return Pattern.compile(text)
    except ValueError:
        return None


def _state_spec(d: InstrumentDefinition, path: str, per_channel: bool) -> tuple[StateSpec | None, str | None]:
    """The state variable a binding names, and a message when it does not resolve."""
    if path.startswith("ch."):
        if not per_channel:
            return (
                None,
                f"{path!r} 是通道状态，这里没有通道号（命令头需要 <ch>，或绑定写作 ch<n>.{path[3:]}）",
            )
        spec = d.channel_state.get(path[3:])
        return spec, None if spec else f"没有通道状态 {path[3:]!r}"
    if path.startswith("ch{n}."):  # a channel's setting, the channel chosen by the component
        spec = d.channel_state.get(path[6:])
        return spec, None if spec else f"没有通道状态 {path[6:]!r}"
    m = re.fullmatch(r"ch(\d+)\.(.+)", path)
    if m and m.group(2) in d.channel_state:
        if not 1 <= int(m.group(1)) <= d.channels.count:
            return None, f"{path!r}：仪表只有 {d.channels.count} 个通道"
        return d.channel_state[m.group(2)], None
    spec = d.state.get(path)
    return spec, None if spec else f"没有状态 {path!r}"


def _state_options(d: InstrumentDefinition, per_channel: bool) -> list[str]:
    return list(d.state) + ([f"ch.{k}" for k in d.channel_state] if per_channel else [])


def _check_state_ref(
    d: InstrumentDefinition, path: str, per_channel: bool, pointer: str, subject: str, report: Report
) -> StateSpec | None:
    spec, problem = _state_spec(d, path, per_channel)
    if problem:
        unknown_name(
            report, "E-DEF-STATE-UNKNOWN", pointer, problem, path, _state_options(d, per_channel), subject
        )
    return spec


def _declared(d: InstrumentDefinition, name: str, channel: bool) -> bool:
    specs = d.channel_components if channel else d.components
    if not channel and name.startswith("ch"):
        name = name.partition(".")[2]
        specs = d.channel_components
    return any(s.name == name for s in specs)


def _has_port(cls: type[Component], port: str) -> bool:
    return port in cls.ports or bool(cls.port_pattern and re.fullmatch(cls.port_pattern, port))


def operations(cls: type[Component]) -> list[str]:
    """Names of the operations a component type offers (``@op``)."""
    return sorted({n for a in dir(cls) if (n := getattr(getattr(cls, a), "__op_name__", None))})


def _protocol_gaps(cls: type, protocol: type) -> list[str]:
    """The methods of a Protocol (a refs target, see ``Component.refs``) a component class does
    not have. The check sees the class, not an instance, so an attribute member such as
    ``Acquisition.acq_count`` is left to the run-time isinstance in the component's accessor.

    ``issubclass`` against a ``runtime_checkable`` Protocol is not an option here: it raises
    ``TypeError`` for a Protocol with data members, which these have. The members come from
    ``get_protocol_members`` (the typing module's own definition of them), not a walk of
    ``__mro__`` (#929)."""
    methods = [
        n
        for n in get_protocol_members(protocol)
        if not n.startswith("_") and callable(getattr(protocol, n, None))
    ]
    return sorted(n for n in methods if not callable(getattr(cls, n, None)))
