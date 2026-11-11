"""The signal wires inside one instrument (#895): which component port feeds which.

Built from the definition's ``wiring`` once the components exist; read by a component asking
whether its port is fed (``has_upstream``) and for the signal there (``upstream_signal`` is
resolved by the instrument, which owns the session's output cache).
"""

from __future__ import annotations

from ..models.definition import InstrumentDefinition
from .portref import split


class SignalWiring:
    def __init__(
        self, definition: InstrumentDefinition, channel_count: int, components: set[str] | dict, name: str
    ) -> None:
        #: (dst component, dst port) -> (src component, src port).
        self.upstream: dict[tuple[str, str], tuple[str, str]] = {}
        self.name = name
        self._components = components
        for n in range(1, channel_count + 1):
            for src, dst in definition.wiring.channel:
                self._wire(f"ch{n}.{src}", f"ch{n}.{dst}")
        for src, dst in definition.wiring.instrument:
            if "{n}" in src or "{n}" in dst:
                for n in range(1, channel_count + 1):
                    self._wire(src.replace("{n}", str(n)), dst.replace("{n}", str(n)))
            else:
                self._wire(src, dst)

    def _wire(self, src: str, dst: str) -> None:
        sc, sp = split(src)
        dc, dp = split(dst)
        for whole, c in ((src, sc), (dst, dc)):
            if not c:  # a dotless reference splits into an empty owner; echo the whole reference
                raise ValueError(f"instrument {self.name!r}: wiring end {whole!r} is not <component>.<port>")
            if c not in self._components:
                raise ValueError(f"instrument {self.name!r}: wiring refers to unknown component {c!r}")
        self.upstream[(dc, dp)] = (sc, sp)

    def feeds(self, comp: str, port: str) -> bool:
        """Whether a signal wire inside the instrument feeds this component's port."""
        return (comp, port) in self.upstream

    def source(self, comp: str, port: str) -> tuple[str, str] | None:
        return self.upstream.get((comp, port))

    def wires(self) -> list[tuple[str, str]]:
        """Signal wiring as ``('<comp>.<port>', '<comp>.<port>')`` (from, to)."""
        return [(f"{sc}.{sp}", f"{dc}.{dp}") for (dc, dp), (sc, sp) in self.upstream.items()]
