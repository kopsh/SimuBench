"""Keysight 53230A (issue 331): the guide's gap-free flow, against the engine.

The manual cases (tests/models/keysight-53230a/cases.yaml) check command by command; these tests run
the real-instrument script examples/scripts/keysight_53230a_gap_free.py on the example bench and
check what a SCPI transcript cannot see: SENSe:FREQuency:MODE CONTinuous turns the timebase's
gap-free gates on, so adjacent readings share their boundary edge and the readings times their
spans add up to exactly the edges counted, while AUTO keeps a gate per reading.
"""

import importlib.util
import sys
from pathlib import Path

import pytest
import pyvisa
import yaml

from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_BENCH = ROOT / "examples" / "benches" / "keysight-53230a.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "keysight_53230a_gap_free.py"


def _script():
    spec = importlib.util.spec_from_file_location("keysight_53230a_gap_free", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


reader = _script()


def _data(frequency: float | None = None) -> dict:
    data = yaml.safe_load(EXAMPLE_BENCH.read_text("utf-8"))
    if frequency is not None:
        data["instruments"]["awg"]["state"]["ch1.frequency"] = frequency
    return data


def test_example_bench_runs_the_script(tmp_path):
    bench = tmp_path / "bench.yaml"
    bench.write_text(yaml.safe_dump(_data()), "utf-8")
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    cnt = rm.open_resource("cnt", read_termination="\n", write_termination="\n", timeout=5000)
    try:
        assert cnt.query("*IDN?").strip() == "Keysight Technologies,53230A,MY53005678,3.00"
        cnt.write("*RST")
        freq = reader.gap_free_frequency(cnt, count=20)
        assert len(freq) == 20
        # 1 kHz from the SDG2042X; 10 ms gates and the 20 ps single-shot resolution.
        assert all(value == pytest.approx(1000.0, rel=1e-6) for value in freq)
        assert 0.0 < reader.allan_deviation(freq) < 1e-6
        assert cnt.query(":SENS:FREQ:MODE?").strip() == "CONT"
        cnt.write(":SENS:FREQ:MODE AUTO")
        # CH2 is 90 degrees behind CH1: the next CH2 event is 750 us after a CH1 event.
        assert reader.time_interval(cnt) == pytest.approx(7.5e-4, abs=5e-9)
        assert cnt.query(":SYST:ERR?").strip().startswith("+0,")
    finally:
        rm.close()


def _spans(mode: str) -> tuple[list[float], list[float], int]:
    """Eight 10 ms readings of 1234.567 Hz (12 or 13 edges a gate, never whole periods) in ``mode``,
    with each reading's span and the edges the eight gates hold."""
    session = open_bench(BenchDefinition.model_validate(_data(frequency=1234.567)))
    try:
        session.write("cnt", f"*RST;:SENS:FREQ:GATE:TIME 0.01;:SENS:FREQ:MODE {mode}\n".encode())
        core = session.instruments["cnt"].components["ch1.core"]
        core.run_begin()
        start = session.now_ns
        readings, spans = [], []
        for i in range(8):  # a reading's span is the one of the sample just taken
            readings.append(core.sample(i))
            spans.append(core.sample_fields(i)["SPAN"])
        counted, _, _ = core._input("a").events(start, start + 8 * core._gate().duration_ns())
        return readings, spans, counted
    finally:
        session.close()


def test_continuous_mode_readings_add_up_to_the_edges_counted():
    readings, spans, counted = _spans("CONT")
    # each reading x its span is the edges of its gate; adjacent gates share their boundary edge
    assert sum(f * s for f, s in zip(readings, spans, strict=True)) == pytest.approx(counted, rel=1e-9)
    assert all(r == pytest.approx(1234.567, rel=1e-6) for r in readings)
    assert all(s > 0 for s in spans)


def test_auto_mode_measures_each_gate_on_its_own():
    readings, spans, _ = _spans("AUTO")
    assert all(r == pytest.approx(1234.567, rel=1e-6) for r in readings)
    # no gap-free span is recorded outside continuous mode
    assert all(s == 0.0 for s in spans)


@pytest.mark.parametrize(("frequency", "rate", "scale"), [(1.0e3, 1.0e6, 1), (2.5e4, 1.0e4, 3)])
def test_time_stamps_read_the_scale_factor_and_the_intervals(frequency, rate, scale):
    """CONFigure:ARRay:TSTamp (guide p.100, #385): the scale factor floor(f/rate)+1, then count
    intervals of scale input periods; TSTamp:RATE sets the rate; (@2) is an enum value, not skipped."""
    session = open_bench(BenchDefinition.model_validate(_data(frequency)))
    try:

        def q(text: str) -> str:
            session.write("cnt", (text + "\n").encode())
            reply = session.read("cnt")
            assert reply is not None, text
            return reply.decode().strip()

        session.write("cnt", f":CONF:ARR:TST (6),(@1);:SENS:TST:RATE {rate}\n".encode())
        values = [float(v) for v in q(":READ?").split(",")]
        assert values[0] == scale and len(values) == 7
        assert values[1:] == pytest.approx([scale / frequency] * 6, abs=1e-10)
        assert float(q(":TRIG:COUN?")) == 1
        assert len(q(":MEAS:ARR:TST? (3),(@2)").split(",")) == 4
        assert q(":SYST:ERR?") == '+0,"No error"'
        session.write("cnt", b":CONF:FREQ\n")
        assert float(q(":READ?")) == pytest.approx(frequency, rel=1e-6)  # one reading again
    finally:
        session.close()
