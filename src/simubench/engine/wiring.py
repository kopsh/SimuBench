"""The signal wires inside one instrument (#895): which component port feeds which.

Built from the definition's ``wiring`` once the components exist (settings.py's one expansion of
the channel wires, issue 1381); read by a component asking whether its port is fed
(``has_upstream``) and for the signal there (``upstream_signal`` is resolved by the instrument,
which owns the session's output cache).
"""

from __future__ import annotations

from ..models.definition import InstrumentDefinition
from .portref import split
from .settings import channel_wiring


class SignalWiring:
    def __init__(
        self, definition: InstrumentDefinition, channel_count: int, components: set[str] | dict, name: str
    ) -> None:
        #: (dst component, dst port) -> (src component, src port).
        self.upstream: dict[tuple[str, str], tuple[str, str]] = {}
        self.name = name
        self._components = components
        for src, dst in channel_wiring(definition, channel_count):
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
