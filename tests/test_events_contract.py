"""The event contract (issue 595): one registry the stream, the record and the tests derive from.

The WS projection (:func:`simubench.engine.events.project`), the run-record reader and the TUI's
test fixtures all build on ``engine.events``. These tests pin the derivation itself: the JSON-safe
spellings, the per-kind whitelist, and — over a real session — that every key the engine emits is
either a contract field or the declared engine-side inventory.
"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import numpy as np
import pytest

from simubench.api import open_bench
from simubench.engine.events import (
    ENGINE_SIDE_KEYS,
    EVENT_KINDS,
    KIND_DESCRIPTION,
    Event,
    RunEvent,
    project,
)
from simubench.engine.record import encode_bytes, jsonable
from simubench.jsonsafe import SPECIAL_NUMBERS, as_number, spelling
from simubench.models import api as M
from simubench.models.bench import Fault

ROOT = Path(__file__).resolve().parents[1]


# ---- the one JSON-safe helper ---------------------------------------------------------------------


def test_jsonable_unifies_the_infinity_spelling():
    """One helper for the run record and the event stream: "INF", as the bench files spell it."""
    assert jsonable(float("inf")) == "INF"
    assert jsonable(float("-inf")) == "-INF"
    assert jsonable(float("nan")) == "NAN"
    assert jsonable({"v": (float("inf"), 1.0)}) == {"v": ["INF", 1.0]}


def test_jsonable_covers_numpy_bytes_and_datetimes():
    assert jsonable(np.float64(1.5)) == 1.5 and isinstance(jsonable(np.int32(3)), int)
    assert jsonable(np.float64(float("inf"))) == "INF"
    assert jsonable(dt.date(2026, 11, 5)) == "2026-11-05"
    assert jsonable(b":MEAS?") == {"text": ":MEAS?"}
    raw = bytes(range(256))
    assert jsonable(raw) == {"b64": encode_bytes(raw)["b64"], "size": "256"}


def test_jsonsafe_is_the_one_spelling_forward_and_back():
    """The spelling lives once (simubench.jsonsafe, #628); jsonable writes it, clients read it
    back through as_number."""
    assert list(SPECIAL_NUMBERS) == ["INF", "-INF", "NAN"]
    assert SPECIAL_NUMBERS["INF"] == float("inf") and SPECIAL_NUMBERS["-INF"] == float("-inf")
    assert math.isnan(SPECIAL_NUMBERS["NAN"])
    assert spelling(float("inf")) == "INF" and spelling(float("-inf")) == "-INF"
    assert spelling(float("nan")) == "NAN"
    assert as_number("INF") == float("inf") and as_number("-INF") == float("-inf")
    assert math.isnan(as_number("nan"))  # any case, as the TUI read it before
    assert as_number(2.5) == 2.5 and as_number("3") == "3"  # anything else unchanged


# ---- the contract and its projection --------------------------------------------------------------


def test_the_kind_description_is_generated_from_the_registry():
    assert " | ".join(EVENT_KINDS) == KIND_DESCRIPTION
    assert set(EVENT_KINDS) == {
        "scpi",
        "state",
        "advance",
        "error",
        "ignored",
        "fault",
        "acquisition",
        "panel",
        "hidden",
        "remote",
        "hislip",
        "bus_control",
        "solver",
        "file",
    }


def test_api_reexports_the_engine_contract():
    assert M.Event is Event and M.RunEvent is RunEvent
    assert issubclass(RunEvent, Event)  # the record's event is the stream's, plus the block ref


def test_project_keeps_every_contract_field_and_nothing_else():
    out = project(
        {
            "kind": "state",
            "instrument": "smu",
            "path": "v",
            "old": float("inf"),
            "new": 1.0,
            "client": "ui",
            "eid": 41,
            "secret": "not in the contract",
        },
        7,
    )
    assert out == {
        "seq": 41,  # the engine's eid, not the WS fallback
        "t_ns": 0,
        "kind": "state",
        "instrument": "smu",
        "path": "v",
        "old": "INF",  # JSON has no infinity
        "new": 1.0,
        "client": "ui",
    }


def test_project_carries_the_acquisition_recipe():
    """The acquisition's count/forced/channels reach the stream (issue 595: they were stripped)."""
    out = project(
        {
            "kind": "acquisition",
            "instrument": "scope",
            "count": 3,
            "forced": True,
            "channels": {"1": {"points": 1000, "sha256": "ab" * 32}},
            "seed": 7,
        },
        0,
    )
    assert out["count"] == 3 and out["forced"] is True
    assert out["channels"] == {"1": {"points": 1000, "sha256": "ab" * 32}}
    assert "seed" not in out  # the record keeps it (also in the record header)
    assert M.Event.model_validate(out)


def test_project_maps_the_raw_data_to_text_and_size():
    out = project({"kind": "scpi", "dir": "write", "data": b":MEAS?\n"}, 0)
    assert out["text"] == ":MEAS?\n" and out["size"] == 7
    out = project({"kind": "scpi", "dir": "read", "data": b"#80001234" + b"x" * 1230}, 0)
    assert out["text"].startswith("#80001234") and out["size"] == 1239  # a waveform block's header
    out = project({"kind": "scpi", "dir": "read", "data": bytes(range(256))}, 0)
    assert out["text"] == "<256 B binary>" and out["size"] == 256


def test_project_defaults_the_seq_and_the_kind():
    assert project({"kind": "state", "t_ns": 5}, 7)["seq"] == 7
    assert project({"t_ns": 5}, 7)["kind"] == "unknown"


@pytest.mark.parametrize(
    "kind",
    [
        "ignored",
        "remote",
        "hislip",
        "bus_control",
    ],  # the kinds an engine session alone cannot cheaply produce
)
def test_project_carries_each_kind_s_declared_fields(kind):
    samples = {
        "ignored": {"header": "C1:FOO", "query": False, "reason": "ignored", "origin": "d.yaml"},
        "remote": {"instrument": "awg", "action": "remote", "source": "script"},
        "hislip": {"instrument": "awg", "client": "hislip:host:1010#0", "action": "GOLOCAL"},
        "bus_control": {"instrument": "awg", "client": "vxi11:host:1010#0", "action": "go to local"},
    }
    out = project({"kind": kind, **samples[kind]}, 0)
    assert {k: out[k] for k in samples[kind]} == samples[kind]
    assert M.Event.model_validate(out)


# ---- the registration-point regression over a real session ----------------------------------------


def test_every_key_a_session_emits_is_contract_or_declared_engine_side():
    """A new event type is registered exactly once, in the contract (issue 595): every kind a
    session emits is in the registry, and every key it carries is a contract field or the
    declared engine-side inventory — the projection consumes it, or only the record keeps it."""
    s = open_bench(ROOT / "acceptance/benches/awg_rc_scope.bench.yaml", clock="stepped")
    try:
        seen: list[dict] = []
        s.subscribe(seen.append)
        s.write("awg", b":OUTPut1:STATe ON\n")  # scpi write + the set's state event
        s.exchange("awg", b"*IDN?")  # scpi write + read
        s.write("scope", b":NOSUCHcommand\n")  # an error event
        s.write("scope", b":DIGitize\n") and s.read("scope")  # an acquisition event
        s.advance(1e-4)  # advance
        s.panel("scope", "run")  # panel
        s.set_parameter("rc", "", "r", 1234.0)  # hidden
        s.faults.inject(Fault(kind="latency", instrument="awg", latency_s=1e-3))  # fault
    finally:
        s.close()
    kinds = {e.get("kind") for e in seen}
    assert {"scpi", "state", "error", "acquisition", "advance", "panel", "hidden", "fault"} <= kinds
    for e in seen:
        assert e.get("kind") in EVENT_KINDS, e
        assert set(e) <= set(Event.model_fields) | ENGINE_SIDE_KEYS, e
        out = project(e, 0)  # every event projects cleanly onto the stream
        assert out["kind"] == e["kind"] and isinstance(out["seq"], int)
