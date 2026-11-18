"""The scope screen as a projection of the acquisition block's screen state (issue 1383): the
block owns the screen's rules — what is live, what a running screen redraws (D47), when it holds
(#986) — and :func:`simubench.server.views.scope_screen` assembles the DTO and downsamples, over
the ``ScreenSource``/``ScreenMeasurer`` protocols. These tests read that seam directly, with a
fake block: no HTTP, no server, no instrument definition (D49's unit tier)."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pytest
from tests.helpers import raises_with

from simubench.engine.component import ScreenState
from simubench.engine.stream import beyond_model
from simubench.library.components.scope import Record
from simubench.server.views import scope_screen

pytestmark = pytest.mark.unit


def a_record(channel: int, volts: list[float]) -> Record:
    return Record(
        channel=channel,
        t_start_ns=0,
        dt_s=1e-6,
        trigger_index=0,
        scale=0.1,
        offset=0.0,
        triggered=True,
        volts=np.asarray(volts, dtype=float),
    )


class BeyondRecord(Record):
    """A record whose screen envelope (the projection's downsampling) is beyond the model."""

    def envelope(self, bins: int, key: tuple[object, ...] = ()) -> list[float]:
        raise beyond_model(f"a screen of {bins} bins is beyond the model")


@dataclass
class FakeInst:
    components: dict[str, Any]
    ports: dict[str, Any] = field(default_factory=dict)

    def get_state(self, path: str) -> Any:
        return {"timebase.scale": 5e-3, "trigger.source": "CHAN2"}[path]


@dataclass
class FakeSession:
    inst: FakeInst

    def instrument(self, name: str) -> FakeInst:
        return self.inst

    def view(self, settle: Any = None) -> Any:
        return contextlib.nullcontext()


@dataclass
class FakeServed:
    session: FakeSession
    frames: dict[str, int] = field(default_factory=dict)


class FakeAcq:
    """An acquisition block that answers the protocol and remembers the frames it was asked to draw."""

    type_name = "scope_acquisition"

    def __init__(self, make_state: Any) -> None:
        self.make_state = make_state
        self.asked_frames: list[int | None] = []

    def screen_state(self, frame: int | None = None) -> ScreenState:
        self.asked_frames.append(frame)
        return self.make_state(frame)


class FakeMeasurer:
    """A measurement block that reports canned rows and the records it was handed."""

    type_name = "scope_measurements"

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.seen_channels: list[int] | None = None

    def screen_values(self, records: Any) -> list[dict[str, Any]]:
        self.seen_channels = sorted(records)
        return [dict(row) for row in self.rows]


def a_state(
    live: bool = False,
    shown: bool = False,
    records: dict[int, Record] | None = None,
    stored: dict[int, Record] | None = None,
) -> ScreenState:
    return ScreenState(
        live=live,
        shown=shown,
        records=records if records is not None else {},
        stored=stored if stored is not None else {},
        waiting=False,
        trigger_level=0.4,
        trigger_source=2,
        acquisitions=3,
        screen_left_s=-1e-5,
    )


def test_a_stopped_screens_records_reach_the_dto_whole():
    """The projection passes the block's state through and turns its records into traces: no
    downsampling without ``points``, the measurement rows as the block reported them."""
    held = {1: a_record(1, [0.1, -0.1, 0.2]), 2: a_record(2, [0.0, 0.05])}
    meas = FakeMeasurer([{"channel": 1, "vpp": 0.3, "frequency": None}])
    served = FakeServed(
        FakeSession(
            FakeInst({"acq": FakeAcq(lambda frame: a_state(records=held, stored=held)), "meas": meas})
        )
    )
    screen = scope_screen(served, "scope")  # type: ignore[arg-type]
    assert screen.source == "record" and not screen.running
    assert screen.waiting is False and screen.acquisitions == 3
    assert screen.trigger_level == 0.4 and screen.trigger_source == 2
    assert screen.timebase_scale == 5e-3 and screen.screen_left_s == -1e-5
    assert [(t.channel, t.volts) for t in screen.traces] == [
        (1, [0.1, -0.1, 0.2]),
        (2, [0.0, 0.05]),
    ]
    assert [m.channel for m in screen.measurements] == [1]
    assert screen.measurements[0].vpp == 0.3 and screen.measurements[0].frequency is None
    assert screen.functions == []


def test_a_running_screen_draws_the_frame_the_counter_numbers():
    """A running scope redraws (D47): the projection numbers each read and hands the number to the
    block, whose frame — with its own noise — is what the DTO reports, measurements included."""
    stored = {1: a_record(1, [0.0])}
    frame = {1: a_record(1, [0.9, -0.9])}
    acq = FakeAcq(lambda f: a_state(live=True, shown=f is not None, records=frame, stored=stored))
    meas = FakeMeasurer([{"channel": 1}])
    served = FakeServed(FakeSession(FakeInst({"acq": acq, "meas": meas})))
    first = scope_screen(served, "scope")  # type: ignore[arg-type]
    second = scope_screen(served, "scope")  # type: ignore[arg-type]
    assert acq.asked_frames == [1, 2] and served.frames == {"scope": 2}
    assert second.source == "live" and second.traces[0].volts == first.traces[0].volts
    assert first.source == "live" and first.running
    assert first.traces[0].volts == [0.9, -0.9]  # the frame, not the stored record
    assert meas.seen_channels == [1]  # the panel measures the record shown


def test_a_frame_beyond_the_model_holds_the_stored_records():
    """#986: the frame's projection (its per-bin envelope) beyond the model leaves the screen
    holding what it had — a record screen, not an error."""
    stored = {1: a_record(1, [0.2, -0.2])}
    acq = FakeAcq(
        lambda f: a_state(
            live=True,
            shown=True,
            records={
                1: BeyondRecord(
                    channel=1,
                    t_start_ns=0,
                    dt_s=1e-6,
                    trigger_index=0,
                    scale=0.1,
                    offset=0.0,
                    triggered=True,
                    volts=np.asarray([0.5, -0.5]),
                )
            },
            stored=stored,
        )
    )
    served = FakeServed(FakeSession(FakeInst({"acq": acq})))
    screen = scope_screen(served, "scope", points=4)  # type: ignore[arg-type]
    assert screen.source == "record" and not screen.running
    # the held record, downsampled as asked (not the refused frame's samples)
    assert screen.traces[0].volts == [] and screen.traces[0].minmax == [0.2, 0.2, -0.2, -0.2]


def test_an_acquisition_without_the_screen_protocol_is_no_oscilloscope():
    """The seam is the protocol: a component that does not answer it (or no acquisition at all) is
    refused with the same refusal as before."""
    no_acq = FakeServed(FakeSession(FakeInst({"meas": FakeMeasurer([])})))
    with raises_with(KeyError, "not an oscilloscope"):
        scope_screen(no_acq, "dmm")  # type: ignore[arg-type]

    class OldStyle:  # an acquisition of an older seam: computes, but knows no screen protocol
        type_name = "scope_acquisition"

        def compute(self, channels: Any, frame: Any = None) -> Any: ...

    old = FakeServed(FakeSession(FakeInst({"acq": OldStyle()})))
    with raises_with(KeyError, "not an oscilloscope"):
        scope_screen(old, "scope")  # type: ignore[arg-type]
