"""Projections the MCP tools, the assistant's tools and the HTTP routes share (#1382): one shape
per thing, every seam re-uses it instead of keeping its own copy. A parameter limit (how many
commands a batch may carry) and the field names of a tool's result stay with the caller."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..access.scpi_server import scpi_socket_address
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


def cases_ancestors(files: BenchFiles, def_id: str) -> list[str]:
    """The models a workspace definition is a copy of (extends, nearest first): the manual cases
    shipped with these are its regression cases (the acceptance page lists them as inherited). A
    built-in model has its own cases only."""
    library = files.library(None)
    if (files.workspace / "instruments").resolve() not in library.path(def_id).resolve().parents:
        return []
    return [m for m, _ in library.ancestors(def_id)]


def _is_builtin_cases(name: str, model: str) -> bool:
    from ..library.coverage import builtin_cases_file

    try:
        builtin_cases_file(name, model)
    except ValueError:  # Invalid: another model's file, or no built-in file of that shape
        return False
    return True


def run_cases_file(
    files: BenchFiles,
    path: str,
    *,
    def_id: str | None = None,
    collect: bool = False,
    model: str | None = None,
) -> list[Any]:
    """Run a cases file the way ``simubench cases`` does: each case on a freshly powered bench,
    every step checked against its expectation. A ``builtin:`` name is the shipped manual cases of
    the model that owns it — of ``def_id`` itself, or, when a definition the caller runs cases for
    extends another model, of that ancestor (the copy then runs them in the copy's place,
    ``model=`` the copy); an agent seam without a definition in context runs any model's own file.
    ``model`` the caller gives runs the cases on that definition in place of the cases' own, and
    wins over the ancestor replacement. A missing or broken file, or a built-in name that is
    nobody's, is a domain error for the seam to map; what the result rows are called and whether
    the steps are collected stays with the caller (#1485: one resolution for all three seams —
    the acceptance page used to be the only one that parsed ``builtin:``)."""
    from ..cases import run_cases
    from ..library.coverage import BUILTIN_PREFIX, builtin_cases_file

    cases_file: Path
    if path.startswith(BUILTIN_PREFIX):
        owner = def_id
        if def_id is not None:
            owner = next(
                (m for m in [def_id, *cases_ancestors(files, def_id)] if _is_builtin_cases(path, m)),
                def_id,  # none: builtin_cases_file below says why
            )
            if model is None and owner != def_id:
                model = def_id  # the copy runs the model it extends' cases in the copy's place
        cases_file = builtin_cases_file(path, owner)
    else:
        cases_file = files.resolve(path)
    return run_cases(cases_file, [files.workspace / "instruments"], collect=collect, model=model)  # type: ignore[no-any-return]


def session_summary(manager: SessionManager, served: ServedSession, info: Any = None) -> dict[str, Any]:
    """One summary of a deployed session, for both agent seams (#1485): which bench, the clock,
    and each instrument's identity with the addresses it answers at — virtual resource, HiSLIP,
    VXI-11, the SCPI socket, the installed options. The seam-specific extras stay with the seam
    (the MCP tool adds the switch_script tutorial and, when configured, the UI URL). ``info`` when
    the caller already described the session: describe re-projects every instrument's state,
    deviations and nets — not something to do twice (issue 641)."""
    info = info or manager.views.describe(served)
    return {
        "session_id": info.id,
        "bench": info.bench_name,
        "bench_path": info.bench_path,
        "clock": info.clock_mode,
        "now_ns": info.now_ns,
        "instruments": [
            {
                "name": i.name,
                "model": i.model,
                "idn": i.idn,
                "resource_virtual": i.address_virtual,
                "resource_real": i.address_real,
                "resource_hislip": i.address_hislip,
                "resource_vxi11": i.address_vxi11,
                "scpi_socket": scpi_socket_address("127.0.0.1", i.scpi_port) if i.scpi_port else None,
                # the hardware options this unit has (D68), for a model that declares any
                **({"options": [o.id for o in i.options if o.installed]} if i.options else {}),
            }
            for i in info.instruments
        ],
    }
