"""Sample streams (D67, signal-chain.md 3, #946): frames pulled through samples ports, the frame
cache, the model limits and the definition rules — with three small blocks defined here, since no
built-in block has a samples port yet."""

from __future__ import annotations

import copy
from typing import ClassVar

import numpy as np
import pytest

from helpers import raises_with
from simubench.engine import session as session_module
from simubench.engine.component import Component, PortSpec, component, component_types, registry
from simubench.engine.noise import gaussian, stream_key
from simubench.engine.session import Session
from simubench.engine.signal import Signal, sine
from simubench.engine.stream import (
    MAX_FRAME,
    MAX_REQUEST_FANOUT,
    VOLTS,
    FrameRequest,
    ModelLimit,
    SampleFormat,
    SampleFrame,
    decimate,
)
from simubench.engine.validate_definition import validate_definition_data
from simubench.models.bench import BenchDefinition
from simubench.models.definition import InstrumentDefinition

pytestmark = pytest.mark.unit
component_types()  # load the built-in library here, not inside the first test's time budget

DT = 1e-6
WARMUP = 10


@pytest.fixture
def blocks():
    @component("t_tone")
    class Tone(Component):
        """A 1 kHz sine: the analog side."""

        ports: ClassVar[dict[str, PortSpec]] = {"out": PortSpec("out", "signal")}
        defaults: ClassVar[dict] = {"peak": 1.0}

        def output(self, port: str) -> Signal:
            return sine(1e3, float(self.param("peak")))

    @component("t_adc")
    class Adc(Component):
        """Samples its input and adds the analog chain's noise, keyed by the request."""

        ports: ClassVar[dict[str, PortSpec]] = {
            "in": PortSpec("in", "signal"),
            "out": PortSpec("out", "samples"),
        }
        defaults: ClassVar[dict] = {"noise_rms": 0.01}
        calls = 0

        def frames(self, port: str, request: FrameRequest) -> SampleFrame:
            self.calls += 1
            v = self.input("in").sample_rel(request.t0_ns, request.offsets_s())
            noise = gaussian(stream_key(self.session.seed, *request.key), request.indexes())
            return SampleFrame(v + float(self.param("noise_rms")) * noise, request.t0_ns, request.dt_s)

        def sample_interval(self, port: str) -> float:
            return DT

    @component("t_decimator")
    class Decimator(Component):
        """Keeps one sample in ``factor``, after ``WARMUP`` samples of history."""

        ports: ClassVar[dict[str, PortSpec]] = {
            "in": PortSpec("in", "samples"),
            "out": PortSpec("out", "samples"),
        }
        defaults: ClassVar[dict] = {"factor": 4}
        asked: FrameRequest | None = None

        def frames(self, port: str, request: FrameRequest) -> SampleFrame:
            m = int(self.param("factor"))
            self.asked = request.upstream(m, WARMUP)
            up = self.input_frames("in", self.asked)
            return SampleFrame(decimate(up.data, m, WARMUP), request.t0_ns, request.dt_s, up.fmt)

        def sample_interval(self, port: str) -> float:
            return DT * int(self.param("factor"))

    yield
    registry.unregister("t_tone", "t_adc", "t_decimator")


CHAIN = {
    "schema": "simubench.instrument/1",
    "id": "chain",
    "manufacturer": "ACME",
    "model": "CHAIN",
    "class": "oscilloscope",
    "state": {"tone.level": {"type": "float", "unit": "V", "min": 0.0, "max": 10.0, "default": 1.0}},
    "components": [
        {"name": "tone", "type": "t_tone", "params": {"peak": "$tone.level"}},
        {"name": "adc", "type": "t_adc"},
        {"name": "dec", "type": "t_decimator"},
    ],
    "wiring": {"instrument": [["tone.out", "adc.in"], ["adc.out", "dec.in"]]},
    "commands": [{"header": "LEVel", "state": "tone.level"}],
}


#: The definition validated once at collection (not in the 100 ms call phase, D49).
CHAIN_DEFINITION = InstrumentDefinition.model_validate(CHAIN)
CHAIN_BENCH = BenchDefinition.model_validate(
    {"schema": "simubench.bench/1", "name": "c", "instruments": {"x": {"model": "chain"}}}
)


def open_chain(data: dict = CHAIN, seed: int = 1):
    definition = CHAIN_DEFINITION if data is CHAIN else InstrumentDefinition.model_validate(data)
    bench = (
        CHAIN_BENCH
        if data is CHAIN
        else BenchDefinition.model_validate(
            {"schema": "simubench.bench/1", "name": "c", "instruments": {"x": {"model": "chain"}}}
        )
    )
    session = Session(bench, lambda _id: definition, clock="stepped", seed=seed)
    return session, session.instruments["x"].components


def ask(count: int = 100, key: tuple = ("acq", 0), **more) -> FrameRequest:
    return FrameRequest(t0_ns=0, count=count, dt_s=4 * DT, key=key, **more)


# ---- frames are pure functions of the request ---------------------------------------------------


def test_the_same_request_gives_the_same_frame(blocks):
    session, comps = open_chain()
    first = session.cached_frames(comps["dec"], "out", ask())
    again = session.cached_frames(comps["dec"], "out", ask())
    assert np.array_equal(first.data, again.data)


def test_a_repeated_request_is_served_from_the_cache(blocks):
    session, comps = open_chain()
    session.cached_frames(comps["dec"], "out", ask())
    session.cached_frames(comps["dec"], "out", ask())
    assert comps["adc"].calls == 1


def test_a_fresh_session_computes_the_same_frame(blocks):
    a = open_chain()[0]
    b = open_chain()[0]
    frames = [s.cached_frames(s.instruments["x"].components["dec"], "out", ask()) for s in (a, b)]
    assert np.array_equal(frames[0].data, frames[1].data)


def test_another_key_draws_other_noise(blocks):
    session, comps = open_chain()
    script = session.cached_frames(comps["dec"], "out", ask(key=("acq", 0)))
    probe = session.cached_frames(comps["dec"], "out", ask(key=("probe", 0)))
    assert not np.array_equal(script.data, probe.data)


def test_another_seed_draws_other_noise(blocks):
    frames = [
        s.cached_frames(s.instruments["x"].components["dec"], "out", ask())
        for s in (open_chain(seed=1)[0], open_chain(seed=2)[0])
    ]
    assert not np.array_equal(frames[0].data, frames[1].data)


def test_overlapping_requests_agree_where_they_overlap(blocks):
    """Two requests on one sample grid (``first`` and ``index0`` are the position on it) see the
    same samples at the same instants — there is no history a block could have kept between them."""
    session, comps = open_chain()
    whole = session.cached_frames(comps["dec"], "out", ask(count=100))
    tail = session.cached_frames(
        comps["dec"], "out", FrameRequest(t0_ns=0, count=60, dt_s=4 * DT, key=("acq", 0), index0=40, first=40)
    )
    assert np.allclose(whole.data[40:], tail.data, rtol=0, atol=1e-12)


def test_a_frame_is_read_only(blocks):
    session, comps = open_chain()
    frame = session.cached_frames(comps["dec"], "out", ask())
    with pytest.raises(ValueError):
        frame.data[0] = 0.0


# ---- rate changes -------------------------------------------------------------------------------


def test_a_rate_changing_block_asks_upstream_for_count_times_factor_plus_warmup(blocks):
    session, comps = open_chain()
    session.cached_frames(comps["dec"], "out", ask(count=100))
    asked = comps["dec"].asked
    assert (asked.count, asked.dt_s, asked.index0) == (100 * 4 + WARMUP, DT, -WARMUP)


def test_the_warmup_samples_come_before_the_requested_instant(blocks):
    session, comps = open_chain()
    session.cached_frames(comps["dec"], "out", ask())
    assert (comps["dec"].asked.t0_ns, comps["dec"].asked.first) == (0, -WARMUP)


def test_the_decimated_frame_is_the_fine_grid_at_the_requested_instants(blocks):
    session, comps = open_chain()
    out = session.cached_frames(comps["dec"], "out", ask(count=50))
    fine = session.cached_frames(comps["adc"], "out", FrameRequest(0, 200, DT, ("acq", 0)))
    assert np.allclose(out.data, fine.data[::4], rtol=0, atol=1e-12)


def test_input_frames_refuses_an_unwired_input(blocks):
    data = copy.deepcopy(CHAIN)
    data["wiring"]["instrument"].pop()
    _, comps = open_chain(data)
    with raises_with(ValueError, "dec", "not wired"):
        comps["dec"].input_frames("in", ask())


def test_a_block_without_a_samples_output_says_so(blocks):
    _, comps = open_chain()
    with raises_with(NotImplementedError, "t_tone", "no samples output"):
        comps["tone"].frames("out", ask())


# ---- model limits -------------------------------------------------------------------------------


def test_a_frame_beyond_the_limit_is_refused(blocks):
    session, comps = open_chain()
    with raises_with(ModelLimit, "beyond the model", str(MAX_FRAME)):
        session.cached_frames(comps["adc"], "out", FrameRequest(0, MAX_FRAME + 1, DT))
    assert comps["adc"].calls == 0  # refused before any sample was computed


def test_a_read_that_fans_out_beyond_the_limit_is_refused(blocks, monkeypatch):
    """Each frame is within ``MAX_FRAME``; what the chain asks for in total is not."""
    monkeypatch.setattr(session_module, "MAX_REQUEST_FANOUT", 1000)
    data = copy.deepcopy(CHAIN)
    data["components"][2]["params"] = {"factor": 100}
    session, comps = open_chain(data)
    with raises_with(ModelLimit, "x/adc.out", "10110 samples along the chain", "at most 1000"):
        session.cached_frames(comps["dec"], "out", FrameRequest(0, 100, 100 * DT))
    assert comps["adc"].calls == 0


def test_the_fan_out_limit_is_above_one_full_frame():
    assert MAX_REQUEST_FANOUT >= 2 * MAX_FRAME


def test_a_refused_read_leaves_the_next_one_working(blocks):
    session, comps = open_chain()
    with pytest.raises(ModelLimit):
        session.cached_frames(comps["adc"], "out", FrameRequest(0, MAX_FRAME + 1, DT))
    assert session.cached_frames(comps["dec"], "out", ask()).count == 100


def test_a_cached_upstream_frame_still_counts_toward_the_fan_out(blocks, monkeypatch):
    """Whether a read is refused must not depend on what was read before."""
    session, comps = open_chain()
    session.cached_frames(comps["dec"], "out", ask(count=100))  # the upstream frame (410) is now cached
    monkeypatch.setattr(session_module, "MAX_REQUEST_FANOUT", 450)
    session._frame_cached -= session._frame_cache.pop(
        next(k for k in session._frame_cache if k[1] == "x/dec")
    )[0].count
    with pytest.raises(ModelLimit):
        session.cached_frames(comps["dec"], "out", ask(count=100))  # 100 + 410 > 450


def test_a_frame_of_the_wrong_length_is_a_programming_error(blocks):
    session, comps = open_chain()
    comps["dec"].frames = lambda port, request: SampleFrame(np.zeros(3), 0, DT)
    with raises_with(ValueError, "x/dec.out", "3 samples"):
        session.cached_frames(comps["dec"], "out", ask())


# ---- the cache follows the signal version ------------------------------------------------------


def test_a_changed_setting_drops_the_cached_frames(blocks):
    session, comps = open_chain()
    before = session.cached_frames(comps["dec"], "out", ask())
    session.write("x", b"LEV 2\n")
    after = session.cached_frames(comps["dec"], "out", ask())
    assert comps["adc"].calls == 2
    assert np.ptp(after.data) > 1.5 * np.ptp(before.data)


def test_the_cache_keeps_a_bounded_number_of_frames(blocks):
    session, comps = open_chain()
    for k in range(70):  # past the bound of 64: the oldest must be evicted (D49 budget)
        session.cached_frames(comps["adc"], "out", FrameRequest(0, 4, DT, ("acq", k)))
    assert len(session._frame_cache) == 64


# ---- the data types -----------------------------------------------------------------------------


def test_a_request_needs_samples_and_an_interval():
    with pytest.raises(ValueError):
        FrameRequest(0, 0, DT)
    with pytest.raises(ValueError):
        FrameRequest(0, 10, 0.0)


def test_complex_kinds_hold_complex_samples():
    frame = SampleFrame(np.ones(4), 0, DT, SampleFormat("bins"), center_hz=1e9, df_hz=1e3)
    assert frame.data.dtype == np.complex128


def test_real_kinds_hold_floats():
    frame = SampleFrame(np.arange(4), 0, DT, SampleFormat("codes", bits=8, lsb=0.01))
    assert (frame.data.dtype, frame.count, frame.rate_hz, frame.fmt.bits) == (np.float64, 4, 1 / DT, 8)


def test_a_frame_copies_the_array_it_is_given():
    mine = np.zeros(4)
    frame = SampleFrame(mine, 0, DT, VOLTS)
    mine[0] = 1.0
    assert frame.data[0] == 0.0


# ---- definition rules ---------------------------------------------------------------------------


def codes(data: dict) -> list[tuple[str, str]]:
    _, report = validate_definition_data(data)
    return [(d.code, d.pointer) for d in report.diagnostics]


def test_the_chain_is_a_valid_definition(blocks):
    assert codes(CHAIN) == []


def test_a_samples_output_feeds_only_a_samples_input(blocks):
    data = copy.deepcopy(CHAIN)
    data["components"].append({"name": "adc2", "type": "t_adc"})
    data["wiring"]["instrument"].append(["adc.out", "adc2.in"])
    assert codes(data) == [("E-DEF-WIRE-MEDIUM", "/wiring/instrument/2")]


def test_a_signal_output_needs_an_adc_before_a_samples_input(blocks):
    data = copy.deepcopy(CHAIN)
    data["wiring"]["instrument"][1] = ["tone.out", "dec.in"]
    assert codes(data) == [("E-DEF-WIRE-MEDIUM", "/wiring/instrument/1")]


def test_a_samples_input_may_not_float(blocks):
    data = copy.deepcopy(CHAIN)
    data["wiring"]["instrument"].pop()
    assert codes(data) == [("E-DEF-SAMPLES-OPEN", "/components/2")]


def test_a_samples_port_is_not_an_instrument_port(blocks):
    data = copy.deepcopy(CHAIN)
    data["ports"] = {"instrument": {"OUT": "dec.out"}}
    assert codes(data) == [("E-DEF-WIRE-MEDIUM", "/ports/instrument/OUT")]


def test_channel_blocks_follow_the_same_rules(blocks):
    data = copy.deepcopy(CHAIN)
    data["channels"] = {"count": 2}
    data["channel_components"] = [{"name": "adc", "type": "t_adc"}, {"name": "dec", "type": "t_decimator"}]
    data["wiring"]["channel"] = [["adc.out", "dec.in"]]
    assert codes(data) == []
    data["wiring"]["channel"] = []
    assert codes(data) == [("E-DEF-SAMPLES-OPEN", "/channel_components/1")]


def test_the_cache_holds_a_bounded_number_of_samples(blocks, monkeypatch):
    """Deep records must not pile up: the cache counts samples, and skips a frame over its budget."""
    monkeypatch.setattr(session_module, "FRAME_CACHE_SAMPLES", 1000)
    session, comps = open_chain()
    for k in range(5):
        session.cached_frames(comps["adc"], "out", FrameRequest(0, 400, DT, ("acq", k)))
    session.cached_frames(comps["adc"], "out", FrameRequest(0, 2000, DT, ("acq", 9)))
    assert (len(session._frame_cache), session._frame_cached) == (2, 800)
