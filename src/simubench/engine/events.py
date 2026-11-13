"""The event contract (D57, issue 595): the shape of the session's event stream, declared where
the events are emitted.

The engine emits plain dicts (:meth:`Session.emit`); the WebSocket stream, the run record and the
TUI's log all see the same shape. The models below are that shape, so a new field or kind is
registered exactly here: ``models/api.py`` re-exports them (the D57 precedent of ``SourceLine``),
the server's WS projection (:func:`project`) and the run-record reader derive their whitelists
from the model, and the test fixtures build their events through it.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .record import encode_bytes, jsonable

#: Every kind the engine and its access layers emit, with what the event says. ``Event.kind``'s
#: description is generated from this mapping — a new kind is declared here and nowhere else.
EVENT_KINDS: dict[str, str] = {
    "scpi": "a program message written to, or a reply read from, the bus (dir, instrument, text/size)",
    "state": "a setting changed (instrument, path, old, new)",
    "advance": "virtual time moved (dt_ns, reason)",
    "error": "an error queued on the instrument (code, message, reason)",
    "ignored": "a command ignored by unknown_commands: no error and no reply (header, query, reason)",
    "fault": "a fault injected, tripped or cleared (fault, reason, message)",
    "acquisition": "an acquisition stored (instrument, count, forced, channels)",
    "panel": "a front-panel operation (instrument, action)",
    "hidden": "a hidden parameter of the running bench written (source)",
    "remote": "remote/local control of an instrument changed (instrument, action)",
    "hislip": "a HiSLIP remote/local control message acknowledged (instrument, client, action)",
    "bus_control": "something a script did on a bus: a remote/local control (REN, GTL, LLO), a GPIB command "
    "(SDC, DCL, GET, IFC ...) or a USB488 request (INDICATOR_PULSE ...) (instrument, client, action)",
    "solver": "the circuit solver reported (reason, message)",
    "file": "an instrument wrote, deleted or made a file or folder in its mass storage "
    "(instrument, action, path; a written file's size and sha256, #1069)",
}

#: ``Event.kind``'s schema description, generated from :data:`EVENT_KINDS` instead of hand-copied.
KIND_DESCRIPTION = " | ".join(EVENT_KINDS)

# The shared model config mirrors models/api._Model so the generated schema stays identical (D57).


class Event(BaseModel):
    """One event of the session's event stream (WebSocket)."""

    model_config = ConfigDict(
        frozen=True, populate_by_name=True, json_schema_serialization_defaults_required=True
    )

    seq: int
    t_ns: int
    kind: str = Field(..., description=KIND_DESCRIPTION)
    instrument: str | None = None
    dir: Literal["write", "read"] | None = None
    text: str | None = Field(None, description="SCPI bytes as text when printable")
    size: int | None = Field(None, description="Byte count of SCPI data")
    duration_ns: int | None = None
    client: str | None = None
    path: str | None = None
    old: Any = None
    new: Any = None
    source: str | None = None
    fault: str | None = Field(None, description="Fault id, for fault events and messages a fault affected")
    code: int | None = None
    message: str | None = None
    dt_ns: int | None = None
    reason: str | None = Field(
        None,
        description="For error and ignored events: why the command was not executed — not_modeled | "
        "not_in_model | not_in_definition | ignored | builtin_excluded | bad_parameter (D18). "
        "Fault, advance and solver events carry their own kind-specific reason (solver: unsettled, D52)",
    )
    wall: float | None = None
    header: str | None = Field(None, description="The header that was ignored, for ignored events")
    query: bool | None = Field(None, description="Whether the ignored header was a query")
    origin: str | None = Field(
        None,
        description="The file the classification's declaration comes from (the definition or its "
        "coverage table), as the loader received the path",
    )
    closest: str | None = Field(
        None, description="The author's command header closest to the one sent, when one is close"
    )
    param: str | None = Field(None, description="The parameter or state path, for bad_parameter")
    range: str | None = Field(
        None, description="The range or options the definition declares, for bad_parameter"
    )
    gap: Literal["out_of_scope", "blocked", "model_work"] | None = Field(
        None,
        description="For not_modeled: why the coverage table does not model the command — "
        "out_of_scope | blocked (see blocked_by) | model_work; absent when the table does not say",
    )
    blocked_by: str | None = Field(
        None, description="For gap blocked: the platform capability the command waits for (e.g. C5)"
    )
    note: str | None = Field(None, description="For not_modeled: the coverage table's note on the command")
    action: str | None = Field(
        None,
        description="A front-panel button, for panel events: run | stop | single; "
        "for remote events: local (taken back from the UI) | remote (a remote client's command); "
        "for hislip and bus_control events: the acknowledged control",
    )
    # An acquisition's recipe (the scope's scope_acquisition emits them): the count, whether it
    # was forced, and per channel what was stored (D36) — what a waveform block regenerates from.
    count: int | None = Field(None, description="The acquisition count, for acquisition events")
    forced: bool | None = Field(
        None, description="Read from a channel that had no record, for acquisition events"
    )
    channels: dict[str, Any] | None = Field(
        None, description="Acquisition recipe, per channel (t_start_ns, dt_s, points, sha256, …)"
    )


class RunEvent(Event):
    """An event as recorded; binary replies carry their hash and the acquisition that regenerates them."""

    sha256: str | None = Field(None, description="Hash of a binary reply kept as a reference (D36)")
    recipe: int | None = Field(None, description="seq of the acquisition event a waveform block came from")


#: Keys the engine puts on its event dicts that the contract does not declare. The projection
#: consumes ``eid``, ``data`` and ``t_start_ns`` (the stream's ``seq``, the reply's ``text``/
#: ``size``, the write's start tick); only the run record keeps ``spec`` (what was injected, D36),
#: ``seed`` (the acquisition's, also in the record header) and the hidden write's payload. The
#: regression in tests/test_events_contract.py holds every emit site to this inventory.
ENGINE_SIDE_KEYS = frozenset(
    {"eid", "data", "t_start_ns", "spec", "seed", "owner", "component", "parameter", "value"}
)


def _summary(data: bytes) -> str:
    """Printable summary of binary SCPI data: the block header and the byte count."""
    if data.startswith(b"#") and len(data) > 2 and data[1:2].isdigit():
        n = int(data[1:2])
        return data[: 2 + n].decode(errors="replace") + f"… ({len(data)} B)"
    return f"<{len(data)} B binary>"


def project(ev: dict[str, Any], seq: int) -> dict[str, Any]:
    """An engine event dict as the WS stream carries it: contract-shaped and JSON-safe.

    The whitelist is the contract itself: every field the contract declares flows through, so a
    field added to :class:`Event` reaches the stream with no second edit. What stays engine-side
    is :data:`ENGINE_SIDE_KEYS`. ``old`` and ``new`` go through :func:`jsonable` (JSON has no
    infinity).
    """
    out: dict[str, Any] = {
        "seq": ev.get("eid", seq),
        "t_ns": int(ev.get("t_ns", 0)),
        "kind": ev.get("kind", "unknown"),
    }
    for key in Event.model_fields:
        if key in ("seq", "t_ns", "kind"):
            continue
        if key in ev:
            out[key] = ev[key]
    if "old" in out:
        out["old"] = jsonable(out["old"])
    if "new" in out:
        out["new"] = jsonable(out["new"])
    data = ev.get("data")
    if isinstance(data, bytes):
        enc = encode_bytes(data)
        out["size"] = len(data)
        out["text"] = enc.get("text") if "text" in enc else _summary(data)
    return Event.model_validate(out).model_dump(exclude_none=True)
