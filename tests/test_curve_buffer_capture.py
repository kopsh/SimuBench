"""The curve buffer's SR86x capture view (#333): all traces at one bin, bytes, state, rate and the
interleaved float block — on a bare curve_buffer whose traces read a stub with known values."""

from __future__ import annotations

import struct
from types import SimpleNamespace

import pytest

from simubench.library.components.curve import CurveBuffer
from simubench.scpi.errors import ScpiError


class _Src:
    def trace_value(self, quantity: str, t_ns: int) -> float:
        return {"X": 1.0, "Y": -2.0}[quantity] + t_ns * 1e-9  # changes with time: one per sample


def _buffer(**params) -> CurveBuffer:
    session = SimpleNamespace(now_ns=0)
    owner = SimpleNamespace(
        components={"src": _Src()},
        definition=SimpleNamespace(format=SimpleNamespace(readings="component")),
        name="lia",
    )
    comp = CurveBuffer(
        "cap", {"trace1": "src.X", "trace2": "src.Y", "rate": 10.0, "capacity": 4, **params}, owner, session
    )
    return comp


def _floats(reply: bytes) -> list[float]:
    return [float(v) for v in reply.split(b",")]


def _advance(comp: CurveBuffer, seconds: float) -> None:
    comp.session.now_ns += round(seconds * 1e9)


def test_a_fresh_start_clears_and_samples_every_trace_at_one_bin():
    cap = _buffer()
    cap.op_start(fresh=True)
    _advance(cap, 0.25)  # samples at 0, 0.1, 0.2 s
    assert cap.op_bytes().response == b"24"  # 3 points x 2 traces x 4 bytes
    assert _floats(cap.op_sample(index=1).response) == pytest.approx([1.1, -1.9])
    assert _floats(cap.op_sample(index=3).response) == [0.0, 0.0]  # inside the buffer, no data yet
    with pytest.raises(ScpiError):
        cap.op_sample(index=4)
    assert cap.op_capture_state().response == b"3"  # capturing, started
    cap.op_pause()
    cap.op_start(fresh=True)
    assert cap.op_bytes().response == b"8"  # the restart discarded the old points


def test_a_one_shot_capture_stops_full_and_reads_as_an_interleaved_block():
    cap = _buffer()
    cap.op_start(fresh=True)
    _advance(cap, 1.0)
    assert cap.op_capture_state().response == b"6"  # done (not capturing), started, wrapped
    assert cap.op_progress().response == b"2"  # 32 bytes round up to one 2 kB block
    block = cap.op_get(offset_kb=0, length_kb=1).response
    assert len(block) == 1024
    values = struct.unpack("<8f", block[:32])
    assert values == pytest.approx([1.0, -2.0, 1.1, -1.9, 1.2, -1.8, 1.3, -1.7], abs=1e-6)
    assert block[32:] == bytes(1024 - 32)


def test_the_block_needs_a_stopped_capture_and_the_rate_reads_in_hertz():
    cap = _buffer(end="LOOP")
    cap.op_start(fresh=True)
    with pytest.raises(ScpiError):
        cap.op_get(offset_kb=0, length_kb=1)
    assert float(cap.op_rate().response) == pytest.approx(10.0)
