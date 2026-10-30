"""Low-frequency calibration bench (docs/lowfreq-cal.md): the LeCroy-style reference scope, the scope
calibrator, the text waveform readback and the platform pieces they need.

The host program's contract (FR-1..FR-5) is checked command by command here; the whole program flow
over raw sockets is the acceptance scenario (acceptance/test_lowfreq_cal_scenario.py).
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest
import pyvisa
import yaml

from simubench.api import open_bench
from simubench.engine.commands import classify
from simubench.library.loader import DefinitionLibrary, load_yaml
from simubench.models.definition import InstrumentDefinition

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "lowfreq-cal"
# The bench and its definitions; issue 48 runs these tests again on a workspace built through MCP alone.
WORKSPACE = Path(os.environ.get("SIMUBENCH_LFCAL_WORKSPACE") or EXAMPLE)
BENCH = WORKSPACE / "lowfreq_cal.bench.yaml"
SCRIPT = EXAMPLE / "lowfreq_cal_demo.py"
INSPECT = 'C1:INSPECT? "DATA_ARRAY_1",FLOAT'


def _script():
    spec = importlib.util.spec_from_file_location("lowfreq_cal_demo", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


demo = _script()


def _bench(tmp_path, edit=None) -> Path:
    """A copy of the preset (its instruments/ folder too), optionally edited."""
    dst = tmp_path / "ws"
    shutil.copytree(WORKSPACE, dst, ignore=shutil.ignore_patterns("__pycache__"))
    bench = dst / BENCH.name
    if edit:
        data = yaml.safe_load(bench.read_text("utf-8"))
        edit(data)
        bench.write_text(yaml.safe_dump(data, allow_unicode=True), "utf-8")
    return bench


@pytest.fixture
def rig(tmp_path):
    rm = pyvisa.ResourceManager(f"{_bench(tmp_path)}@simubench")
    scope = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=5000)
    cal = rm.open_resource("calibrator", read_termination="\n", write_termination="\n", timeout=5000)
    yield scope, cal, rm.visalib.bench
    rm.close()


def _square(cal, freq=1000.0, vpp=1.0, ch=1, load=50, duty=0.5):
    for cmd in (
        f":CHANnel{ch}:BASE:WAVe SQUare",
        f":CHANnel{ch}:BASE:OFFSet 0",
        f":CHANnel{ch}:AMPLitude:UNIT VPP",
        f":CHANnel{ch}:BASE:AMPLitude {vpp}",
        f":CHANnel{ch}:BASE:FREQuency {freq}",
        f":CHANnel{ch}:BASE:DUTY {duty}",
        f":CHANnel{ch}:LOAD {load}",
        f":CHANnel{ch}:OUTPut 1",
    ):
        cal.write(cmd)


def _setup_scope(scope, points=30000, tdiv=1e-3, vdiv_mv=200, cpl="D50"):
    for cmd in (
        "*RST ; *CLS ; SYST:PRES",
        "C1:TRACE ON",
        "RCLK INTERNAL",
        f"C1:CPL {cpl}",
        "TRSE Edge,SR,C1",
        "TRMD AUTO",
        "ILVD OFF",
        "PACU 1,AMPL,C1",
        f"C1:VDIV {vdiv_mv} mV",
        f"MSIZ {points}",
        f"TDIV {tdiv}",
        "C1:OFFSET 0",
        "C1:TRLV 0",
    ):
        scope.write(cmd)


def _read(scope, point, np_=0):
    """The host's readback loop (§5.3), with NP as given."""
    data: list[float] = []
    replies = []
    while len(data) < point:
        scope.write(f"WAVEFORM_SETUP SP,0,NP,{np_},FP,{len(data)},SN,0")
        reply = scope.query(INSPECT)
        replies.append(reply)
        values = list(map(float, reply.strip().split()[2:-1]))
        assert values, f"no samples at FP {len(data)}"
        data += values
    return np.array(data), replies


# ---- FR-1: identity ---------------------------------------------------------------------------------


def test_idn_carries_the_model_token_the_bench_sets(rig):
    scope, cal, _ = rig
    assert scope.query("*IDN?").split(",")[1] == "LFCAL-REFSCOPE"
    assert cal.query("*IDN?").split(",")[1] == "LFCAL-CALIBRATOR"


def test_bench_idn_overrides_the_definition(tmp_path):
    def edit(data):
        data["instruments"]["scope"]["idn"] = "LECROY,WAVEPRO 254HD,{serial},{firmware}"
        data["instruments"]["scope"]["serial"] = "LCRY9999"

    rm = pyvisa.ResourceManager(f"{_bench(tmp_path, edit)}@simubench")
    scope = rm.open_resource("scope", read_termination="\n", write_termination="\n")
    assert scope.query("*IDN?") == "LECROY,WAVEPRO 254HD,LCRY9999,9.6.0"
    rm.close()


# ---- FR-2 / FR-3: every contract command, idempotent, queryable --------------------------------------


def test_contract_commands_are_accepted_twice_without_errors(rig):
    scope, cal, _ = rig
    for _ in range(2):  # FR-3: setting the same values again is not an error
        _setup_scope(scope)
        scope.write("WAVEFORM_SETUP SP,0,NP,1000,FP,0,SN,0")
        _square(cal)
        cal.write(":SYSTem:CLKSource INTernal")
    for n in range(1, 5):
        scope.write(f"C{n}:TRACE {'ON' if n == 1 else 'OFF'}")
    for mode in ("AUTO", "SINGLE", "NORM", "STOP"):
        scope.write(f"TRMD {mode}")
    scope.write("RCLK EXTERNAL")
    scope.write("ILVD ON")
    for cpl in ("A1M", "D1M", "GND", "D50"):
        scope.write(f"C1:CPL {cpl}")
    assert scope.query("SYST:ERR?") == '0,"No error"'
    assert scope.query("*CLS?") == "0"
    assert scope.query("*OPC?") == "1"
    assert cal.query(":SYSTem:ERRor?") == '0,"No error"'


def test_scope_queries_answer_the_settings(rig):
    scope, _, _ = rig
    _setup_scope(scope, points=100000, tdiv=2e-4, vdiv_mv=200)
    scope.write("C1:OFFSET -0.1")
    scope.write("C1:TRLV 0.05")
    assert scope.query("MSIZ?") == "MSIZ 100E+3"
    assert scope.query("TDIV?") == "TDIV 200E-6 S"
    assert scope.query("C1:VDIV?") == "C1:VDIV 200E-3 V"
    assert scope.query("C1:OFST?") == "C1:OFST -100E-3 V"
    assert scope.query("C1:TRLV?") == "C1:TRLV 50E-3 V"
    assert scope.query("C1:CPL?") == "C1:CPL D50"
    assert scope.query("C1:TRA?") == "C1:TRA ON"
    assert scope.query("TRSE?") == "TRSE EDGE,SR,C1,HT,OFF"
    assert scope.query("TRMD?") == "TRMD AUTO"
    assert scope.query("ILVD?") == "ILVD OFF"
    assert scope.query("RCLK?") == "RCLK INTERNAL"
    assert scope.query("PACU? 1") == "PACU 1,AMPL,C1"
    assert scope.query("WFSU?") == "WFSU SP,0,NP,0,FP,0,SN,0"
    # Long names are the same commands; COMM_HEADER changes how replies start.
    scope.write("VOLT_DIV 1")  # no channel prefix: not a channel command, ignored
    scope.write("C1:VOLT_DIV 0.5")
    assert scope.query("C1:VDIV?") == "C1:VDIV 500E-3 V"
    scope.write("CHDR OFF")
    assert scope.query("C1:VDIV?") == "500E-3 V"
    scope.write("COMM_HEADER LONG")
    assert scope.query("C1:VDIV?") == "C1:VOLT_DIV 500E-3 V"
    assert scope.query("SYST:ERR?") == '0,"No error"'


@pytest.mark.parametrize(
    ("text", "points"), [("30000", 30000), ("10K", 10000), ("2.5MA", 2500000), ("1M", 10**6)]
)
def test_memory_size_suffixes(rig, text, points):
    scope, _, session = rig
    scope.write(f"MSIZ {text}")
    assert session.instruments["scope"].get_state("memory.size") == points


def test_calibrator_settings_and_queries(rig):
    _, cal, session = rig
    _square(cal, freq=12345.0, vpp=0.8, duty=0.25)
    assert cal.query(":CHANnel1:BASE:WAVe?") == "SQU"
    assert float(cal.query(":CHANnel1:BASE:FREQuency?")) == 12345.0
    assert float(cal.query(":CHANnel1:BASE:AMPLitude?")) == 0.8
    assert float(cal.query(":CHANnel1:BASE:DUTY?")) == 0.25
    assert float(cal.query(":CHANnel1:LOAD?")) == 50.0
    assert cal.query(":CHANnel1:OUTPut?") == "1"
    assert cal.query(":CHANnel1:AMPLitude:UNIT?") == "VPP"
    cal.write(":SYSTem:CLKSource EXTernal")
    assert cal.query(":SYSTem:CLKSource?") == "EXT"
    cal.write(":CHANnel2:BASE:DUTY 40")  # a percentage is read as one
    assert session.instruments["calibrator"].get_state("ch2.duty") == pytest.approx(0.4)
    assert cal.query(":SYSTem:ERRor?") == '0,"No error"'


def test_unknown_commands_are_ignored_and_logged(rig):
    scope, cal, session = rig
    seen = []
    session.subscribe(lambda e: seen.append(e) if e.get("kind") == "ignored" else None)
    scope.write("VBS 'app.Acquisition.C1.VerScale = 0.1'")
    scope.write("C1:FOO 3")
    cal.write(":CHANnel1:BASE:SKEW 1e-9")
    assert scope.query("SYST:ERR?") == '0,"No error"'
    assert cal.query("SYST:ERR?") == '0,"No error"'
    assert [e["header"] for e in seen] == ["VBS", "C1:FOO", ":CHANnel1:BASE:SKEW"]
    # An unknown query is swallowed too: nothing to read.
    session.write("scope", b"CMR?\n")
    assert session.read("scope") is None


def test_wrong_values_are_still_errors(rig):
    """Ignoring is for unknown headers; a known command with a bad value reports it (FR-2 is about headers)."""
    scope, _, _ = rig
    scope.write("C1:CPL D75")
    assert scope.query("SYST:ERR?").startswith("-224,")


def test_ignore_is_refused_on_real_models():
    data = load_yaml((ROOT / "src/simubench/library/instruments/siglent-sdg2042x.yaml").read_text("utf-8"))
    data["unknown_commands"] = "ignore"
    with pytest.raises(ValueError, match="unknown_commands"):
        InstrumentDefinition.model_validate(data)


def test_static_classification_knows_the_definition_common_commands():
    lib = DefinitionLibrary([WORKSPACE / "instruments"])
    kinds = [m.kind for m in classify(lib.get("lfcal-reference-scope"), "*CLS?;*OPC?;*IDN?;C1:INSP?;VBS")]
    assert kinds == ["command", "command", "common", "command", "unsupported"]


# ---- FR-4: the text waveform readback ------------------------------------------------------------------


def test_reply_follows_the_contract(rig):
    scope, cal, _ = rig
    _square(cal)
    _setup_scope(scope, points=30000)
    scope.write("TRMD SINGLE")
    scope.write("WAVEFORM_SETUP SP,0,NP,0,FP,0,SN,0")
    scope.write(INSPECT)
    raw = scope.read_raw()
    assert raw.endswith(b"\n") and raw.count(b"\n") == 1 and b";" not in raw
    tokens = raw.decode().strip().split()
    assert tokens[:2] == ["C1:INSP", '"'] and tokens[-1] == '"'
    assert "TCPIP" not in raw.decode()
    values = [float(t) for t in tokens[2:-1]]
    assert len(values) == 30000


@pytest.mark.parametrize("point", [30000, 100000, 12345])
def test_pieces_join_into_the_whole_record(rig, point):
    scope, cal, session = rig
    _square(cal)
    _setup_scope(scope, points=point)
    scope.write("TRMD SINGLE")
    data, replies = _read(scope, point)
    assert data.size == point
    assert len(replies) == -(-point // 30000)  # NP 0: at most 30000 per reply
    rec = session.instruments["scope"].components["acq"].records[1]
    np.testing.assert_array_equal(data, np.array([float(f"{v:.6e}") for v in rec.volts]))


def test_running_scope_keeps_one_record_across_pieces(rig):
    """AUTO acquires anew when a readback starts (FP 0); later pieces read the same record."""
    scope, cal, session = rig
    _square(cal)
    _setup_scope(scope, points=50000)
    acq = session.instruments["scope"].components["acq"]
    data, _ = _read(scope, 50000, np_=7000)
    assert acq.acq_count == 1
    np.testing.assert_allclose(data, acq.records[1].volts, rtol=1e-6, atol=1e-12)
    _read(scope, 50000)
    assert acq.acq_count == 2


def test_np_and_sparsing_select_points(rig):
    scope, cal, session = rig
    _square(cal)
    _setup_scope(scope, points=10000)
    scope.write("TRMD SINGLE")
    volts = session.instruments["scope"].components["acq"].records[1].volts
    scope.write("WAVEFORM_SETUP SP,4,NP,100,FP,8,SN,0")
    values = np.array(list(map(float, scope.query(INSPECT).split()[2:-1])))
    np.testing.assert_allclose(values, volts[8 : 8 + 400 : 4], rtol=1e-6, atol=1e-12)
    scope.write("WAVEFORM_SETUP FP,10000")  # past the end: no samples, still a well-formed reply
    assert scope.query(INSPECT).split() == ["C1:INSP", '"', '"']


# ---- FR-5: physical consistency ----------------------------------------------------------------------


def _single(scope, point):
    scope.write("TRMD SINGLE")
    return _read(scope, point)[0]


def test_samples_match_the_source_settings(rig):
    scope, cal, session = rig
    _square(cal, freq=2000.0, vpp=1.0, duty=0.25)
    _setup_scope(scope, points=40000, tdiv=1e-3)
    v = _single(scope, 40000)
    dt = 10 * 1e-3 / 40000
    hi, lo = np.median(v[v > 0]), np.median(v[v < 0])
    assert hi - lo == pytest.approx(1.0, rel=2e-3)  # 1 Vpp into the 50 ohm the load setting says
    assert np.mean(v > (hi + lo) / 2) == pytest.approx(0.25, abs=0.002)
    rising = np.nonzero((v[:-1] < 0) & (v[1:] >= 0))[0]
    assert np.mean(np.diff(rising)) * dt == pytest.approx(1 / 2000.0, rel=1e-3)
    # The trigger (rising edge through 0 V on C1) sits at the centre of the record.
    assert np.min(np.abs(rising + 1 - 20000)) <= 1
    # What the scope recorded is the net's signal (within noise and the ADC step).
    rec = session.instruments["scope"].components["acq"].records[1]
    truth = session.net_signal(session.port_net["scope.CH1"]).sample_rel(
        rec.t_start_ns, np.arange(rec.volts.size) * rec.dt_s
    )
    assert np.std(v - truth) < 2e-3


def test_load_setting_and_coupling_decide_the_level(rig):
    """LOAD and the scope's input impedance together: the amplitude is defined for the load setting."""
    scope, cal, _ = rig

    def vpp(load, cpl):
        _square(cal, vpp=1.0, load=load)
        _setup_scope(scope, points=20000, cpl=cpl, vdiv_mv=500)
        v = _single(scope, 20000)
        return np.median(v[v > 0]) - np.median(v[v < 0])

    assert vpp(50, "D50") == pytest.approx(1.0, rel=2e-3)
    assert vpp(10000, "D1M") == pytest.approx(1.0, rel=2e-3)  # 10000 is the high-impedance setting
    assert vpp(10000, "D50") == pytest.approx(0.5, rel=2e-3)  # open-circuit amplitude into 50 ohm
    assert vpp(50, "D1M") == pytest.approx(2.0, rel=2e-3)


def test_vertical_scale_clips_like_an_adc(rig):
    scope, cal, _ = rig
    _square(cal, vpp=1.0)
    _setup_scope(scope, points=10000, vdiv_mv=50)  # +-0.5 V on +-4 div of 50 mV: off screen
    v = _single(scope, 10000)
    assert v.max() < 0.26 and v.min() > -0.26  # the ADC reaches 5 div


def test_frequency_response_is_physical_and_monotonic(rig):
    scope, cal, _ = rig
    response = demo.run(scope, cal, frequencies=[1e3, 1e4, 1e5, 1e6, 5e6])
    rel = [a for _, a in response]
    assert all(0.99 < r < 1.0 for r in rel)
    assert all(b <= a for a, b in pairwise(rel))
    assert rel[-1] < rel[0]  # the cable's skin effect: higher frequencies settle lower


# ---- FR-7: reproducible -------------------------------------------------------------------------------


def _waveform_for(bench: Path) -> np.ndarray:
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    try:
        scope = rm.open_resource("scope", read_termination="\n", write_termination="\n")
        cal = rm.open_resource("calibrator", read_termination="\n", write_termination="\n")
        _square(cal)
        _setup_scope(scope, points=20000)
        return _single(scope, 20000)
    finally:
        rm.close()


def test_same_seed_same_numbers_other_seed_other_noise(tmp_path):
    a = _waveform_for(_bench(tmp_path / "a"))
    b = _waveform_for(_bench(tmp_path / "b"))
    np.testing.assert_array_equal(a, b)

    def reseed(data):
        data["run"]["seed"] = 1

    c = _waveform_for(_bench(tmp_path / "c", reseed))
    assert not np.array_equal(a, c)
    assert np.abs(a - c).max() < 5e-3  # only the noise differs


def test_noise_does_not_depend_on_idle_time(tmp_path):
    """With the command-driven clock a pause moves virtual time; the record stays the same."""

    def command_clock(data):
        data["run"]["clock"] = "command"

    bench = _bench(tmp_path, command_clock)
    session = open_bench(bench)
    try:
        for cmd in ("C1:TRACE ON", "C1:CPL D50", "C1:VDIV 200 mV", "MSIZ 10000", "TDIV 1E-3"):
            session.write("scope", cmd.encode() + b"\n")
        acq = session.instruments["scope"].components["acq"]
        first = acq._noise(1, session.now_ns, 1e-6, 1000)
        session.advance(1.234567)
        assert np.array_equal(first, acq._noise(1, session.now_ns, 1e-6, 1000))  # keyed by acquisition
    finally:
        session.close()


# ---- run control ---------------------------------------------------------------------------------------


def test_single_waits_for_a_trigger_and_then_stops(rig):
    scope, cal, session = rig
    _setup_scope(scope, points=10000)
    acq = session.instruments["scope"].components["acq"]
    scope.write("TRMD SINGLE")  # the calibrator is still off: nothing to trigger on
    assert scope.query("TRMD?") == "TRMD SINGLE" and acq.acq_count == 0
    _square(cal)
    assert scope.query("*OPC?") == "1"  # the pending acquisition completes once the signal is there
    assert scope.query("TRMD?") == "TRMD STOP" and acq.acq_count == 1


def test_a_stopped_scope_without_a_record_reads_the_channel_and_says_so(rig):
    scope, _, session = rig
    events = []
    session.subscribe(lambda e: events.append(e) if e.get("kind") == "acquisition" else None)
    _setup_scope(scope, points=1000)
    scope.write("TRMD STOP")
    data, _ = _read(scope, 1000)
    assert data.size == 1000 and np.abs(data).max() < 5e-3
    assert events and events[-1]["forced"] is True


def test_pava_measures_the_record(rig):
    scope, cal, _ = rig
    _square(cal, freq=5000.0, vpp=1.0)
    _setup_scope(scope, points=20000)
    head, unit, state = scope.query("C1:PAVA? AMPL").split(",")
    assert head == "C1:PAVA AMPL" and state == "OK"
    assert float(unit.split()[0]) == pytest.approx(1.0, rel=3e-3)
    assert float(scope.query("C1:PAVA? FREQ").split(",")[1].split()[0]) == pytest.approx(5000.0, rel=1e-3)


def test_wavedesc_gives_the_scaling(rig):
    scope, cal, _ = rig
    _square(cal)
    _setup_scope(scope, points=30000, tdiv=1e-3)
    scope.write("TRMD SINGLE")
    words = scope.query('C1:INSPECT? "WAVEDESC"').split()
    desc = {words[i]: words[i + 2] for i in range(2, len(words) - 2) if words[i + 1] == ":"}
    assert int(desc["WAVE_ARRAY_COUNT"]) == 30000
    assert float(desc["HORIZ_INTERVAL"]) == pytest.approx(1e-2 / 30000)
    # Sample 0 is 5 ms before the trigger, plus where the trigger fell between two samples (G5).
    dt = 1e-2 / 30000
    assert -5e-3 <= float(desc["HORIZ_OFFSET"]) < -5e-3 + dt


# ---- platform pieces ----------------------------------------------------------------------------------


def test_output_stage_hiz_from():
    from simubench.library.components.electrical import OutputStage

    stage = OutputStage("out", {"z_out": 50.0, "load_setting": 10000.0, "hiz_from": 10000.0}, None, None)
    assert stage._scale() == 1.0
    stage = OutputStage("out", {"z_out": 50.0, "load_setting": 9999.0, "hiz_from": 10000.0}, None, None)
    assert stage._scale() == pytest.approx(10049 / 9999)


def test_coax_skin_effect_loses_more_at_higher_frequencies():
    from simubench.engine.circuit import Circuit
    from simubench.library.components.electrical import CoaxCable

    cable = CoaxCable("cable", {"r": 0.05, "r_skin_1mhz": 0.15}, None, None)
    c = Circuit()
    cable.stamp(c, lambda port: port)
    c.source("IN", "src", 50.0)
    c.resistor("OUT", "0", 50.0)
    f = np.array([0.0, 1e3, 1e6, 1e8])
    gain = np.abs(c.solve(f).transfer("OUT", "src", f))
    assert gain[0] == pytest.approx(50 / 100.05)
    assert gain[0] > gain[1] > gain[2] > gain[3]
    assert gain[2] / gain[0] == pytest.approx(1 - 0.15 / 100, abs=3e-4)


def test_preset_validates_and_a_cable_does_not_hide_the_termination(tmp_path):
    """The validator sees the scope's 50 Ω input behind the cable; with 1 MΩ it warns of the mismatch."""
    from simubench.cli import main

    assert main(["validate", str(BENCH)]) == 0

    def one_meg(data):
        data["instruments"]["scope"]["state"]["ch2.coupling"] = "D1M"

    lib = DefinitionLibrary([WORKSPACE / "instruments"])
    from simubench.engine.validate import validate_data

    _, report = validate_data(yaml.safe_load(_bench(tmp_path, one_meg).read_text("utf-8")), lib)
    assert [(d.code, d.pointer) for d in report.diagnostics] == [
        ("W-IMPEDANCE-MISMATCH", "/instruments/calibrator/state/ch2.load")
    ]
