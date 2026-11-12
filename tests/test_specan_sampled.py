"""The spectrum analyzer's digital back end and the sampled evaluation (signal-chain.md 5.3–5.5, #955).

``sb-sa-fft-1`` makes its trace from the chain's samples (IF ADC → digital down-converter →
windowed FFT). The same definition with ``evaluation: analytic`` is the reference: both use the
same blocks and the same parameters, so a tone and the noise floor must read the same either way.
The algorithms behind the blocks are pure functions, tested first, without a session.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import yaml
from helpers import session_query

from simubench.api import open_bench
from simubench.engine.stream import MAX_REQUEST_FANOUT
from simubench.library.components.specan_backend import (
    NBW_FACTOR,
    STOPBAND_DB,
    ddc,
    decimation_taps,
    gaussian_window,
    line_response,
    noise_bandwidth,
    taps_per_phase,
    window_length,
    windowed_fft,
)
from simubench.models.bench import BenchDefinition
from simubench.models.definition import InstrumentDefinition
from simubench.server.manager import ServedSession
from simubench.server.services import internal_probe

ROOT = Path(__file__).resolve().parents[1]
DEFS = ROOT / "validation" / "instruments"
TONE_HZ = 1.0e9
TONE_DBM = -20.0
#: Spread, dB, of one trace point that is noise (the dB of an exponentially distributed power).
POINT_SIGMA_DB = 10.0 / math.log(10.0) * math.pi / math.sqrt(6.0)


# ---- the algorithms, as pure functions -----------------------------------------------------------


@pytest.mark.unit
def test_the_oscillator_brings_a_tone_to_zero_hertz():
    """A real tone at the oscillator's frequency comes out as a constant of its amplitude; a tone
    a little above it turns at the difference, counted in output samples."""
    rate, f_if, decimation = 100.0e6, 25.0e6, 50
    taps = decimation_taps(decimation)
    k = np.arange(200 * decimation + taps.size - 1)
    on = ddc(0.3 * np.cos(2 * np.pi * f_if / rate * k + 0.7), f_if / rate, decimation, taps)
    assert on.size == 200
    assert np.abs(on) == pytest.approx(0.3, rel=1e-4)
    assert np.ptp(np.angle(on)) < 1e-4  # no rotation: it sits on 0 Hz
    off = ddc(0.3 * np.cos(2 * np.pi * (f_if + 20.0e3) / rate * k), f_if / rate, decimation, taps)
    turns = np.unwrap(np.angle(off))
    assert (turns[-1] - turns[0]) / (2 * np.pi * 199) == pytest.approx(20.0e3 * decimation / rate, rel=1e-6)
    # no decimation: no filter, the mixing alone (the tone's other half is still in it)
    plain = ddc(0.3 * np.cos(2 * np.pi * f_if / rate * k[:64]), f_if / rate, 1, decimation_taps(1))
    assert plain.size == 64 and float(np.mean(plain.real)) == pytest.approx(0.3, rel=1e-9)


@pytest.mark.unit
@pytest.mark.parametrize("decimation", [2, 5, 64])
def test_the_decimation_filter_meets_its_figures(decimation):
    """What ``decimation_taps`` says of itself: taps_per_phase output intervals long, unity at DC,
    under 0.01 dB of ripple to a quarter of the output rate, the stopband attenuation from three
    quarters of it on — so what folds onto the used band is that far down."""
    taps = decimation_taps(decimation)
    assert taps.size == taps_per_phase(STOPBAND_DB) * decimation
    assert float(taps.sum()) == pytest.approx(1.0, abs=1e-12)
    n = 1 << math.ceil(math.log2(taps.size * 64))
    response = 20 * np.log10(np.maximum(np.abs(np.fft.rfft(taps, n)), 1e-30))
    f = np.fft.rfftfreq(n) * decimation  # in output rates
    assert np.ptp(response[f <= 0.25]) < 0.01
    assert response[f >= 0.75].max() <= -STOPBAND_DB


@pytest.mark.unit
def test_the_window_is_the_gaussian_resolution_filter():
    """One filter, two forms: the window's transform is the closed-form response the analytic
    trace draws lines through — 3.01 dB down half an RBW off — and its noise bandwidth is
    1.0645 × RBW. A tone reads its amplitude whatever the padding."""
    rbw, dt = 1.0e4, 1.0e-6
    count = window_length(rbw, dt)
    window = gaussian_window(count, rbw, dt)
    assert noise_bandwidth(window, dt) == pytest.approx(NBW_FACTOR * rbw, rel=1e-3)
    points = 1 << 14
    df = 1.0 / (points * dt)
    for off_bins in (0, 40, 82, 164):  # on the tone, a quarter, a half and a whole RBW off
        tone = 0.5 * np.exp(2j * np.pi * (off_bins * df) * dt * np.arange(count))
        level = np.abs(windowed_fft(tone, window, points)[points // 2]) ** 2 / 0.25
        expected = line_response(np.array([off_bins * df]), rbw, 0.0, "SAMPle")[0]
        assert 10 * math.log10(level) == pytest.approx(10 * math.log10(expected), abs=0.01)
    assert 10 * math.log10(line_response(np.array([rbw / 2]), rbw, 0.0, "SAMPle")[0]) == pytest.approx(
        -3.0103, abs=1e-3
    )
    for padded in (count, 4096, points):
        assert np.abs(windowed_fft(np.full(count, 0.5 + 0j), window, padded)).max() == pytest.approx(0.5)


# ---- the instrument: sampled against analytic ----------------------------------------------------


def _data() -> dict:
    return yaml.safe_load((DEFS / "sb-sa-fft-1.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def analytic_dir(tmp_path_factory):
    """``sb-sa-fft-1`` with the one word changed: the same blocks, evaluated analytically."""
    data = _data()
    for block in data["components"]:
        if block["type"] == "specan_trace":
            assert block["params"]["evaluation"] == "sampled"
            block["params"]["evaluation"] = "analytic"
    where = tmp_path_factory.mktemp("analytic")
    (where / "sb-sa-fft-1.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return where


def _open(dirs: list[Path], *, seed: int = 3, hidden: dict | None = None):
    sa = {"model": "sb-sa-fft-1", "state": {"freq.center": TONE_HZ, "freq.span": 1.0e7}}
    if hidden:
        sa["hidden"] = hidden
    bench = BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "specan-sampled",
            "instruments": {
                "sg": {
                    "model": "sb-sg-1",
                    "state": {"freq.cw": TONE_HZ, "power.level": TONE_DBM, "output.enabled": True},
                },
                "sa": sa,
            },
            "wires": [{"from": "sg.RF_OUT", "to": "sa.RF_IN"}],
            "run": {"seed": seed, "clock": "command", "idle": "off", "reveal_true_values": True},
        }
    )
    return open_bench(bench, definition_dirs=dirs)


@pytest.fixture(scope="module")
def sampled():
    s = _open([DEFS])
    yield s
    s.close()


@pytest.fixture(scope="module")
def analytic(analytic_dir):
    s = _open([analytic_dir, DEFS])
    yield s
    s.close()


def _trace(s) -> np.ndarray:
    return np.array([float(v) for v in session_query(s, "sa", ":TRAC? TRACE1").split(",")])


def _floor(s, rbw: float, span: float, sweeps: int = 100) -> tuple[float, float]:
    """The mean of the trace away from the tone, averaged over ``sweeps`` sweeps of the sample
    detector, and the spread that mean has: every point is the dB mean of ``sweeps`` noise
    values, and the part looked at holds about ``0.7 × span / RBW`` independent ones."""
    for line in (
        f":FREQ:SPAN {span:.6E}",
        f":BAND {rbw:.6E}",
        ":DET SAMP",
        ":TRAC:TYPE AVER",
        f":AVER:COUN {sweeps}",
        ":INIT:CONT OFF",
        ":INIT",
    ):
        assert session_query(s, "sa", line) is None
    y = _trace(s)
    assert session_query(s, "sa", ":SYST:ERR?").startswith("0,")
    away = np.r_[y[: int(0.35 * y.size)], y[int(0.65 * y.size) :]]  # the tone's skirt is in the middle
    for line in (":INIT:CONT ON", ":TRAC:TYPE WRIT", ":DET POS", ":BAND:AUTO ON"):
        session_query(s, "sa", line)
    return float(away.mean()), POINT_SIGMA_DB / math.sqrt(sweeps * 0.7 * span / rbw)


def test_a_tone_reads_the_same_sampled_and_analytic(sampled, analytic):
    """The single tone (signal-chain.md 5.5): the peak within 0.05 dB and one display point of
    the analytic trace, on the tone; the skirt is the same Gaussian a decibel-scale away."""
    traces = {}
    for name, s in (("sampled", sampled), ("analytic", analytic)):
        assert session_query(s, "sa", ":FREQ:SPAN 1E6") is None
        assert float(session_query(s, "sa", ":BAND?")) == 1.0e4  # coupled: span / 100
        traces[name] = _trace(s)
    y, ref = traces["sampled"], traces["analytic"]
    assert y.size == ref.size == 1001
    assert y.max() == pytest.approx(ref.max(), abs=0.05)
    assert y.max() == pytest.approx(TONE_DBM, abs=0.05)
    assert abs(int(y.argmax()) - int(ref.argmax())) <= 1
    assert int(y.argmax()) == 500  # the tone is on the center point
    # The skirt is the same Gaussian: 1.5 RBW off the tone the filter is 27.1 dB down (60 dB over
    # the noise). The sample detector reads the frequency point nearest the display point, at most
    # half a point's spacing off — on a slope of 2 × 12.04 × 1.5 dB per RBW.
    for s in (sampled, analytic):
        assert session_query(s, "sa", ":DET SAMP") is None
    skirt, ref_skirt = _trace(sampled)[515], _trace(analytic)[515]
    plan_df = sampled.instruments["sa"].components["rbw"]
    spacing = 1.0 / (plan_df.points() * plan_df.interval())
    assert spacing <= 1.0e4 / 16
    slope_db = 2 * 12.04 * 1.5 * (spacing / 2) / 1.0e4
    assert ref_skirt == pytest.approx(TONE_DBM - 12.04 * 1.5**2, abs=0.02)
    assert skirt == pytest.approx(ref_skirt, abs=slope_db + 0.02)
    for s in (sampled, analytic):
        assert session_query(s, "sa", ":DET POS") is None
    # two reads at one instant are one trace
    assert session_query(sampled, "sa", ":TRAC? TRACE1") == session_query(sampled, "sa", ":TRAC? TRACE1")
    assert session_query(sampled, "sa", ":SYST:ERR?").startswith("0,")


def test_the_noise_floor_is_the_analytic_one_and_follows_the_rbw(sampled, analytic):
    """Averaged over 100 sweeps the sampled floor is the analytic one (within 0.3 dB); ten times
    the RBW raises it 10 dB (signal-chain.md 5.5). The tolerance is three times the spread
    the difference of two such means has, or more."""
    narrow, sigma = _floor(sampled, 1.0e5, 1.0e7)
    reference, _ = _floor(analytic, 1.0e5, 1.0e7)
    assert 3 * sigma * math.sqrt(2) < 0.3
    assert narrow == pytest.approx(reference, abs=0.3)
    # kT0 + the receiver's 24 dB + the 10 dB of attenuation in the filter's noise bandwidth, less
    # the 2.51 dB the dB mean of noise sits under its power
    thermal = -174.0 + 24.0 + 10.0 + 10 * math.log10(NBW_FACTOR * 1.0e5) - 10 * np.euler_gamma / math.log(10)
    assert narrow == pytest.approx(thermal, abs=0.3)
    wide, sigma_wide = _floor(sampled, 1.0e6, 1.0e8)
    assert 3 * math.hypot(sigma, sigma_wide) < 0.3
    assert wide - narrow == pytest.approx(10.0, abs=0.3)


def test_fewer_bits_raise_the_floor_by_the_quantization_noise(sampled):
    """16 → 12 bits (a bench's hidden value on the ADC): the floor rises as lsb²/12 over the
    Nyquist band says, referred to the input through the chain's gain (within 0.5 dB). The tone
    keeps the coarser converter busy — its error is noise only while something moves the codes."""
    coarse = _open([DEFS], hidden={"adc": {"bits": 12}})
    try:
        blocks = coarse.instruments["sa"].components
        adc = blocks["adc"]
        assert int(adc.param("bits")) == 12 and adc.lsb() == pytest.approx(2.0 / 4096)
        chain_gain_db = -10.0 + float(blocks["if_filter"].param("gain"))  # the attenuator, the IF amplifier
        rate = float(adc.param("rate"))
        quantization_mw_hz = adc.lsb() ** 2 / 12 / float(adc.param("impedance")) * 1e3 / (rate / 2)
        thermal_mw_hz = 10 ** ((-174.0 + 24.0 + 10.0) / 10)
        rise = 10 * math.log10(1 + quantization_mw_hz * 10 ** (-chain_gain_db / 10) / thermal_mw_hz)
        assert rise == pytest.approx(2.5, abs=0.2)  # visible, by design of the reference analyzer
        fine, _ = _floor(sampled, 1.0e5, 1.0e7)
        raised, _ = _floor(coarse, 1.0e5, 1.0e7)
        assert raised - fine == pytest.approx(rise, abs=0.5)
    finally:
        coarse.close()


def test_a_sweep_beyond_the_model_is_refused_before_it_is_computed(sampled):
    """A narrow RBW over a wide span (signal-chain.md 5.4): the sweep would take more samples
    than the model computes — an error, no trace, and no fallback to the analytic evaluation. The
    refusal is decided from the plan, so nothing is sampled first."""
    s = sampled
    sa = s.instruments["sa"].components["sa"]
    made = []
    adc = s.instruments["sa"].components["adc"]
    original = adc.frames
    adc.frames = lambda port, request: made.append(request) or original(port, request)  # type: ignore[method-assign]
    try:
        assert session_query(s, "sa", ":FREQ:SPAN 1E9") is None
        assert session_query(s, "sa", ":BAND 1E3") is None
        st = sa.settings()
        plan = sa._plan(st.start, st.stop, st.points)
        assert plan.segments * plan.cost > MAX_REQUEST_FANOUT
        assert session_query(s, "sa", ":TRAC? TRACE1") is None
        error = session_query(s, "sa", ":SYST:ERR?")
        assert error.startswith("-221,") and "beyond the model" in error
        assert made == []  # refused from the plan: the converter was never asked
        # the panel says so instead of drawing a trace
        assert "beyond the model" in sa.readings()["values"]["beyond_model"]
        assert "traces" not in sa.readings()
        # a setting within the model reads again
        assert session_query(s, "sa", ":BAND:AUTO ON") is None
        assert _trace(s).size == 1001 and made
        assert session_query(s, "sa", ":SYST:ERR?").startswith("0,")
    finally:
        del adc.frames
        session_query(s, "sa", ":FREQ:SPAN 1E7")


def test_the_sweep_time_is_the_tunings_times_what_each_acquires(sampled):
    """Virtual time (signal-chain.md 5.4): a sampled sweep takes its tunings one after another,
    each as long as the samples its transform and the filters ahead of it need."""
    s = sampled
    sa = s.instruments["sa"].components["sa"]
    assert session_query(s, "sa", ":FREQ:SPAN 2E4") is None
    assert session_query(s, "sa", ":BAND 1E2") is None
    st = sa.settings()
    plan = sa._plan(st.start, st.stop, st.points)
    ddc_block = s.instruments["sa"].components["ddc"]
    decimation = round(plan.dt * 100.0e6)
    assert decimation == ddc_block.decimation_for(plan.dt) == 7812  # 100 MSa/s over 128 RBW, whole
    samples = window_length(1.0e2, plan.dt) * decimation + ddc_block.filter_length(decimation) - 1
    assert plan.acquisition == pytest.approx(samples / 100.0e6, rel=1e-9)
    assert plan.segments == 4  # 20 kHz in transforms of 64 RBW
    assert float(session_query(s, "sa", ":SWE:TIME?")) == pytest.approx(4 * samples / 100.0e6, rel=1e-6)
    assert session_query(s, "sa", ":BAND:AUTO ON") is None
    assert session_query(s, "sa", ":FREQ:SPAN 1E7") is None


def test_a_change_of_the_input_starts_the_trace_again(sampled):
    """The sampled chain reads what RF_IN carries; it has no earlier signal to return to. A
    change of the input restarts the trace, so max hold does not keep a tone that has gone — the
    stated limit of this evaluation."""
    s = sampled
    for line in (":FREQ:SPAN 1E7", ":TRAC:TYPE MAXH"):
        assert session_query(s, "sa", line) is None
    assert _trace(s).max() == pytest.approx(TONE_DBM, abs=0.1)
    assert session_query(s, "sg", ":OUTP OFF") is None
    assert _trace(s).max() < TONE_DBM - 40.0  # the hold began again without the tone
    assert session_query(s, "sg", ":OUTP ON") is None
    assert _trace(s).max() == pytest.approx(TONE_DBM, abs=0.1)
    assert session_query(s, "sa", ":TRAC:TYPE WRIT") is None


def test_the_same_seed_gives_the_same_bytes():
    def run(seed: int) -> str:
        s = _open([DEFS], seed=seed)
        try:
            return session_query(s, "sa", ":TRAC? TRACE1")
        finally:
            s.close()

    first = run(11)
    assert run(11) == first
    assert run(12) != first


# ---- the internal probe on the digital side ------------------------------------------------------


def test_the_probe_reads_the_adc_the_down_converter_and_the_transform(sampled):
    """``adc.out`` is the IF in codes, ``ddc.out`` the complex baseband, ``rbw.out`` the
    transform's frequency points; tuned away from the tone, the tone moves off the middle."""
    s = sampled
    sv = ServedSession("t", s, None)
    assert session_query(s, "sa", ":FREQ:SPAN 1E6") is None
    ports = internal_probe.out_ports(s.instrument("sa"))
    assert {"adc.out", "ddc.out", "rbw.out"} <= set(ports)
    # the tone at the ADC: −20 dBm less 10 dB of attenuation plus 10 dB of IF gain, on the 25 MHz IF
    amplitude = math.sqrt(2 * 50.0 * 10 ** (TONE_DBM / 10) * 1e-3)
    adc = internal_probe.probe(sv, "sa", "adc.out")
    assert (adc.format.kind, adc.format.bits, adc.sample_rate_hz) == ("volts", 16, 100.0e6)
    top = int(np.argmax(adc.spectrum.level))
    assert adc.spectrum.freq_hz[top] == pytest.approx(25.0e6, abs=100.0e6 / 4096)
    assert adc.spectrum.unit == "dBFS" and adc.spectrum.level[top] == pytest.approx(
        20 * math.log10(amplitude), abs=0.1
    )
    assert not adc.clipped
    iq = internal_probe.probe(sv, "sa", "ddc.out")
    assert iq.format.kind == "iq" and iq.sample_rate_hz == pytest.approx(100.0e6 / 78)  # 128 RBW, whole
    assert iq.peak == pytest.approx(amplitude, rel=0.01)  # the tone's amplitude, on 0 Hz
    bins = internal_probe.probe(sv, "sa", "rbw.out")
    assert bins.format.kind == "bins"
    middle = int(np.argmax(bins.spectrum.level))
    assert middle == len(bins.spectrum.level) // 2 and bins.peak == pytest.approx(amplitude, rel=1e-3)
    tuned = internal_probe.probe(sv, "sa", "rbw.out", tune_hz=TONE_HZ - 2.0e4)
    shifted = int(np.argmax(tuned.spectrum.level))
    assert tuned.spectrum.freq_hz[shifted] == pytest.approx(
        TONE_HZ, abs=1.0e3
    )  # the axis is the radio frequency
    assert shifted > middle
    assert internal_probe.probe(sv, "sa", "rbw.out").spectrum == bins.spectrum  # passive, repeatable
    assert session_query(s, "sa", ":FREQ:SPAN 1E7") is None


# ---- an analyzer written before the digital back end ---------------------------------------------


def test_an_analyzer_written_before_the_back_end_gets_one(sampled):
    """``sb-sa-1`` names no ``rbw``: loading it adds the ADC, the down-converter and the
    resolution filter at the end of its tables, moves the RBW parameters off the IF filter and
    points the video filter and the RBW command at the new block. Its trace stays analytic."""
    written = yaml.safe_load((DEFS / "sb-sa-1.yaml").read_text(encoding="utf-8"))
    names = [c["name"] for c in written["components"]]
    assert not {"adc", "ddc", "rbw"} & set(names)
    loaded = InstrumentDefinition.model_validate(written)
    blocks = {c.name: c for c in loaded.components}
    assert [c.name for c in loaded.components] == [*names, "adc", "ddc", "rbw"]
    assert (blocks["adc"].type, blocks["ddc"].type, blocks["rbw"].type) == ("adc", "specan_ddc", "specan_rbw")
    assert blocks["rbw"].params == {"rbw": "$bw.res", "rbw_auto": "$bw.res_auto", "span": "$freq.span"}
    assert blocks["if_filter"].params == {}
    assert blocks["vbw"].params["rbw"] == "rbw" and "if_filter" not in blocks["vbw"].params
    assert blocks["sa"].params["rbw"] == "rbw" and "evaluation" not in blocks["sa"].params
    assert loaded.wiring.instrument[-3:] == [
        ("if_filter.out", "adc.in"),
        ("adc.out", "ddc.in"),
        ("ddc.out", "rbw.in"),
    ]
    assert {c.op for c in loaded.commands if c.op and c.op.endswith("coupled_rbw")} == {"rbw.coupled_rbw"}
    # written out in full, the sampled analyzer is left as it is
    explicit = _data()
    assert InstrumentDefinition.model_validate(explicit).components == [
        type(blocks["sa"]).model_validate(c) for c in explicit["components"]
    ]
