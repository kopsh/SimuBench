"""Projections the MCP tools, the assistant's tools and the HTTP routes share (#1382): one shape
per thing, every seam re-uses it instead of keeping its own copy. A parameter limit (how many
commands a batch may carry) and the field names of a tool's result stay with the caller."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from ..engine.record import jsonable
from .drive import scpi_response

if TYPE_CHECKING:
    from .benchfiles import BenchFiles
    from .manager import ServedSession, SessionManager


def scpi_payload(s: Any, instrument: str, command: str, client: str) -> dict[str, Any]:
    """The same SCPI-exchange shape the web API returns (scpi_response in server.drive): one DTO,
    two seams (the tools used to carry their own, with seconds instead of ns)."""
    return scpi_response(s, instrument, command, client).model_dump(mode="json")


def definition_spec(path: str, s: Any) -> dict[str, Any]:
    """One setting of a definition, as a tool or the web shows it: unit, range, options, default,
    and its labels — the assistant's copy had dropped ``label``/``label_en`` (#1382)."""
    out: dict[str, Any] = {"path": path, "type": s.type}
    for key in ("unit", "min", "max", "options", "default", "label", "label_en", "readonly"):
        v = getattr(s, key)
        if v not in (None, False):
            out[key] = jsonable(v)
    return out


def instrument_state(served: ServedSession, instrument: str) -> dict[str, Any]:
    """The current settings and error queue of an instrument of a running session (what its front
    panel shows). A unknown instrument is a ``KeyError`` for the seam to map."""
    snap = served.session.instrument(instrument).snapshot()
    snap["state"] = {k: jsonable(v) for k, v in snap["state"].items()}
    return snap  # type: ignore[no-any-return]


async def try_commands(
    manager: SessionManager, definition_id: str, commands: list[str], client: str
) -> list[dict[str, Any]]:
    """Send program messages to a definition's instrument alone, without a bench: each command's
    reply, the errors it added to the queue and the virtual time, as :func:`scpi_payload` returns
    them. The pool owns the try semantics (no ports, no run record, scratch bench) and the reuse: a
    second batch for the same definition runs on the same session instead of cycling a bench per
    call (issue 636)."""

    def run() -> list[dict[str, Any]]:
        with manager.tries.using(definition_id) as entry:
            return [
                {
                    "command": command,
                    **scpi_payload(entry.served.session, definition_id, command, client=client),
                }
                for command in commands
            ]

    return await asyncio.to_thread(run)


def run_cases_file(files: BenchFiles, path: str) -> list[Any]:
    """Run a cases file the way ``simubench cases`` does: each case on a freshly powered bench, every
    step checked against its expectation. A missing or broken file is a domain error for the seam
    to map; what the result rows are called stays with the caller."""
    from ..library.coverage import run_cases

    return run_cases(files.resolve(path), [files.workspace / "instruments"])  # type: ignore[no-any-return]
