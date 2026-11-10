"""Keysight 53220A (issue 200): the guide's measurement flow, against the engine.

The manual cases (tests/models/keysight-53220a/cases.yaml) check command by command; these tests run
the real-instrument script examples/scripts/keysight_53220a_measure.py on the example bench and
compare what it measures with the signal the bench carries, then check the parts a SCPI transcript
cannot see: the channel argument routing the measurement to the second input, the input impedance
loading the source, and the hidden timebase error scaling the reading.
"""

import math
import statistics
from pathlib import Path

import pytest
import pyvisa
import yaml

from helpers import load_script, session_query
from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_BENCH = ROOT / "examples" / "benches" / "keysight_53220a.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "keysight_53220a_measure.py"


reader = load_script(SCRIPT)


def _data(**overrides) -> dict:
    """The example bench, with changes merged into one of its instruments (awg / cnt)."""
    data = yaml.safe_load(EXAMPLE_BENCH.read_text("utf-8"))
    for name, changes in overrides.items():
        instrument = data["instruments"][name]
        if "state" in changes:
            instrument["state"] = {**instrument.get("state", {}), **changes.pop("state")}
        instrument.update(changes)
    return data


def _open(tmp_path, **overrides):
    bench = tmp_path / "bench.yaml"
    bench.write_text(yaml.safe_dump(_data(**overrides)), "utf-8")
    return pyvisa.ResourceManager(f"{bench}@simubench")


@pytest.fixture
def counter(tmp_path):
    rm = _open(tmp_path)
    cnt = rm.open_resource("cnt", read_termination="\n", write_termination="\n", timeout=5000)
    yield cnt, rm
    rm.close()


def test_example_bench_runs_the_script(tmp_path):
    rm = _open(tmp_path)
    cnt = rm.open_resource("cnt", read_termination="\n", write_termination="\n", timeout=5000)
    try:
        assert cnt.query("*IDN?").strip() == "Keysight Technologies,53220A,MY53001234,3.00"
        cnt.write("*RST")
        freq = reader.frequency(cnt, count=5)
        assert len(freq) == 5
        # 1 kHz from the SDG2042X; the counter's timebase is exact (0 ppm) on this bench.
        assert all(value == pytest.approx(1000.0, rel=1e-6) for value in freq)
        interval = reader.time_interval(cnt)
        # CH2 is 90 degrees behind CH1: the next CH2 event is 750 us after a CH1 event.
        assert interval == pytest.approx(7.5e-4, abs=5e-9)
        events = reader.totalize(cnt)
        # 1 kHz over a 0.1 s gate: 100 or 101 events, depending on where the gate opens.
        assert events == pytest.approx(100.0, abs=1.0)
        mean, sdev, low, high, n = reader.statistics(cnt)
        assert n == 5
        for value in (mean, low, high):
            assert value == pytest.approx(1000.0, rel=1e-6)
        assert sdev < 1e-3  # #255: the gate resolution moves the last digits
        assert cnt.query(":SYST:ERR?").strip().startswith("+0,")
    finally:
        rm.close()


def test_channel_argument_selects_the_input(counter):
    cnt, rm = counter
    awg = rm.open_resource("awg", read_termination="\n", write_termination="\n", timeout=5000)
    try:
        awg.write("C2:BSWV FRQ,2000")
        assert float(cnt.query(":MEAS:FREQ? 1E3, 0.0, (@1)")) == pytest.approx(1000.0, rel=1e-6)
        assert float(cnt.query(":MEAS:FREQ? 2E3, 0.0, (@2)")) == pytest.approx(2000.0, rel=1e-6)
        # CONFigure + READ? routes the trigger model's sampler the same way.
        cnt.write(":CONF:FREQ 2E3, 0.1, (@2)")
        cnt.write(":SAMP:COUN 1")
        cnt.write(":INIT")
        assert float(cnt.query(":FETCh?")) == pytest.approx(2000.0, rel=1e-6)
    finally:
        awg.close()


def test_the_50_ohm_input_loads_the_source_below_the_sensitivity(tmp_path):
    # 0.15 Vpp is above the 0.1 Vpp model sensitivity at the 1 M ohm input; the same source into the
    # 50 ohm input (the SDG's own 50 ohm output impedance) halves the amplitude and stops the count.
    rm = _open(tmp_path, awg={"state": {"ch1.amplitude": 0.15}})
    cnt = rm.open_resource("cnt", read_termination="\n", write_termination="\n", timeout=5000)
    try:
        cnt.write("*RST")
        assert float(cnt.query(":MEAS:FREQ?")) == pytest.approx(1000.0, rel=1e-6)
        cnt.write(":INP:IMP 50")
        assert float(cnt.query(":MEAS:FREQ?")) == pytest.approx(0.0, abs=1e-12)
        assert float(cnt.query(":INP:IMP?")) == pytest.approx(50.0)
    finally:
        rm.close()


def test_a_hidden_timebase_error_scales_the_reading():
    session = open_bench(BenchDefinition.model_validate(_data(cnt={"hidden": {"clock": {"ppm": 2.0}}})))
    try:

        def q(text: str) -> str:
            session.write("cnt", (text + "\n").encode())
            reply = session.read("cnt")
            assert reply is not None, text
            return reply.decode().strip()

        session.write("cnt", b"*RST\n")
        session.write("cnt", b":CONF:FREQ 1E3,0.1,(@1)\n")
        # A fast timebase makes the counter's second short: it reads low by 2 ppm.
        assert float(q(":MEAS:FREQ?")) == pytest.approx(1000.0 / (1 + 2e-6), rel=1e-6)
    finally:
        session.close()


def test_the_trigger_and_single_shot_jitter_spread_the_time_interval():
    """The counter's jitter (#744/#861): each event time carries a normal error of the channel's
    trigger error (1 ns, a model value) combined with the interpolator's single-shot resolution
    (100 ps, Table 5-2's Tss); a time interval is the difference of two events, so readings spread
    by √2 × that, around the 750 µs the 90° phase gives, and the same seed replays bit for bit."""
    sigma = 2**0.5 * math.hypot(1.0e-9, 1.0e-10)

    def run(n: int) -> list[float]:
        s = open_bench(EXAMPLE_BENCH)
        try:
            assert session_query(s, "cnt", ":CONF:TINT (@1),(@2)") is None
            return [float(session_query(s, "cnt", ":READ?")) for _ in range(n)]
        finally:
            s.close()

    readings = run(100)
    assert len(set(readings)) > 50
    assert statistics.mean(readings) == pytest.approx(7.5e-4, abs=4 * sigma / math.sqrt(100))
    assert statistics.stdev(readings) == pytest.approx(sigma, rel=0.3)
    assert run(20) == readings[:20]
