"""The operating point of the bench (D34, D52, #1508).

The circuit is rebuilt when the electrical version moves; its solution is memoized per signal
version and refreshed lazily, with the mode iteration of the piecewise-linear devices around it.
The solver reads the session only through :class:`SolveHost`: what it needs of a session is a
short list, not the session.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Protocol

from .circuit import Circuit, Solution
from .signal import Signal

if TYPE_CHECKING:
    from .component import Component

MAX_MODE_ITERATIONS = 40
log = logging.getLogger(__name__)


class SolveHost(Protocol):
    """What the solver asks of its session."""

    stats: dict[str, float]

    @property
    def signal_version(self) -> int: ...

    @property
    def electrical_version(self) -> int: ...

    def all_components(self) -> list[Component]: ...

    def node_of(self, comp: Component, port: str) -> str: ...

    def revise(self, *, electrical: bool) -> None:
        """Count a change of the signals, and of the circuit if ``electrical``, without the sync
        an invalidation runs: the mode loop is inside a solve."""

    def emit(self, event: dict[str, Any]) -> None: ...


class Solver:
    def __init__(self, host: SolveHost) -> None:
        self._host = host
        self._circuit: tuple[int, Circuit] | None = None
        self._solution: tuple[int, Solution, dict[str, Signal]] | None = None
        #: The last solve ended without the piecewise-linear devices agreeing on a mode (D52): the
        #: operating point is the last iteration's, consistent but not regulated as asked.
        self.unsettled = False

    def circuit(self) -> Circuit:
        host = self._host
        if self._circuit is None or self._circuit[0] != host.electrical_version:
            c = Circuit()
            for comp in host.all_components():
                comp.stamp(c, lambda p, comp=comp: host.node_of(comp, p))  # type: ignore[misc]
            self._circuit = (host.electrical_version, c)
        return self._circuit[1]

    def solve(self) -> tuple[Solution, dict[str, Signal]]:
        """Bring the operating point up to date and return it: the solved circuit and every
        source's EMF signal, at the current instant.

        Solving is lazy and memoized: nothing happens when nothing changed since the last solve.
        What a reading cannot get lazily is the mode iteration: piecewise-linear devices
        (D34/D52) re-derive their mode against the solved operating point inside this loop, so a
        component judging its own state (regulating CC or CV, tripped, conducting) calls this
        first and reads afterwards — the readings and the state flags then answer from one
        operating point, and repeat identically until something changes."""
        host = self._host
        if self._solution is None or self._solution[0] != host.signal_version:
            piecewise = [c for c in host.all_components() if c.piecewise]
            for _ in range(MAX_MODE_ITERATIONS):
                # The calling thread's CPU, not wall time: a solve that was merely descheduled or
                # waited on a busy machine is not a slower solve, and BLAS pool threads spinning
                # beside it are not solving (issue 209: the suite runs four-wide under xdist).
                started = time.thread_time()
                sol, emf = self._solve_once()
                host.stats["solve_seconds"] = (
                    host.stats.get("solve_seconds", 0.0) + time.thread_time() - started
                )
                # Piecewise-linear devices check their mode against the DC operating point.
                changed = False
                for comp in piecewise:

                    def dc(
                        port: str, comp: Component = comp, sol: Solution = sol, emf: dict[str, Signal] = emf
                    ) -> float:
                        node = host.node_of(comp, port)
                        return 0.0 if node not in sol.node_transfer else sol.node_signal(node, emf).mean()

                    def port_signal(
                        port: str, comp: Component = comp, sol: Solution = sol, emf: dict[str, Signal] = emf
                    ) -> Signal:
                        node = host.node_of(comp, port)
                        return Signal.zero() if node not in sol.node_transfer else sol.node_signal(node, emf)

                    dc.signal = port_signal  # type: ignore[attr-defined]  # the whole signal (AC limits, #570)
                    changed |= comp.update_mode(dc)
                if not changed:
                    self.unsettled = False
                    return sol, emf  # the memo holds the same pair this iteration wrote
                host.revise(electrical=True)
            else:
                return self._settle_unsettled()
        return self._solution[1], self._solution[2]

    def _solve_once(self) -> tuple[Solution, dict[str, Signal]]:
        """One pass of the operating point, without the mode iteration around it (:meth:`solve`):
        every source's EMF, the frequencies they carry, one circuit solve, the solve counter and
        the memo — the block both the mode loop and the last-resort settle run."""
        host = self._host
        emf: dict[str, Signal] = {}
        for comp in host.all_components():
            emf.update(comp.emf())
        freqs: set[float] = {0.0}
        for sig in emf.values():
            freqs.update(float(f) for f in set(sig.freq.tolist()))
        sol = self.circuit().solve(sorted(freqs))
        host.stats["solves"] = host.stats.get("solves", 0) + 1
        self._solution = (host.signal_version, sol, emf)
        return sol, emf

    def _settle_unsettled(self) -> tuple[Solution, dict[str, Signal]]:
        """No mode agreed within MAX_MODE_ITERATIONS (D52): solve once more with the modes the
        last iteration chose and keep that, without asking the devices again. Every reading then
        comes from one operating point that satisfies the circuit (V and I agree with the source,
        P = V·I) and repeated queries answer the same until something changes. The first time it
        happens a ``solver`` event (reason ``unsettled``) goes into the run record (#258)."""
        sol, emf = self._solve_once()
        if not self.unsettled:
            self.unsettled = True
            log.warning("piecewise-linear devices did not settle on a mode")
            self._host.emit(
                {
                    "kind": "solver",
                    "reason": "unsettled",
                    "message": "piecewise-linear devices did not settle on a mode; "
                    "the readings are the last iteration's operating point",
                }
            )
        return sol, emf
