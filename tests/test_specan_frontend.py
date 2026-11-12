"""The spectrum analyzer's analog front end passes signals between its blocks (#952,
signal-chain.md 5.2): the attenuator, mixer and IF filter each have an output, the mixer's and the
IF's depend on where the local oscillator is tuned (``Session.tuned``), and the trace reads the
chain's stages by its wiring. A definition written before that expands into the same one."""

from __future__ import annotations

import copy
import math
from pathlib import Path

import numpy as np
import pytest
import yaml

from helpers import session_query
from simubench.api import open_bench
from simubench.engine.rfcascade import cascade
from simubench.engine.validate_definition import validate_definition_data
from simubench.models.bench import BenchDefinition
from simubench.models.definition import InstrumentDefinition

ROOT = Path(__file__).resolve().parents[1]
DEFS = ROOT / "validation" / "instruments"
MODELS = ROOT / "src" / "simubench" / "library" / "instruments"
ANALYZERS = [DEFS / "sb-sa-1.yaml", MODELS / "keysight-n9320b.yaml", MODELS / "keysight-n9042b.yaml"]

TONE_HZ = 1.0e9
IF_HZ = 21.4e6
CONVERSION_LOSS = 7.0
IF_GAIN = 3.0


def _data(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _dbm(amplitude: complex) -> float:
    return 10 * math.log10(abs(amplitude) ** 2 / (2 * 50.0) * 1e3)


@pytest.fixture(scope="module")
def bench(tmp_path_factory):
    """A 1 GHz, -20 dBm tone into an ``sb-sa-1`` whose mixer loses 7 dB and whose IF gains 3 dB."""
    d = _data(DEFS / "sb-sa-1.yaml")
    blocks = {c["name"]: c for c in d["components"]}
    blocks["mixer"]["params"]["conversion_loss"] = CONVERSION_LOSS
    blocks["if_filter"]["params"].update(if_hz=IF_HZ, gain=IF_GAIN)
    where = tmp_path_factory.mktemp("specan")
    (where / "sb-sa-1.yaml").write_text(yaml.safe_dump(d, allow_unicode=True), "utf-8")
    definition = BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "specan-frontend",
            "instruments": {
                "sg": {
                    "model": "sb-sg-1",
                    "state": {"freq.cw": TONE_HZ, "power.level": -20.0, "output.enabled": True},
                },
                "sa": {"model": "sb-sa-1", "state": {"freq.center": TONE_HZ, "freq.span": 1.0e6}},
            },
            "wires": [{"from": "sg.RF_OUT", "to": "sa.RF_IN"}],
            "run": {"seed": 3, "clock": "command", "idle": "off"},
        }
    )
    session = open_bench(definition, definition_dirs=[where, DEFS])
    yield session
    session.close()


def test_the_front_end_passes_the_tone_from_block_to_block(bench):
    s = bench
    sa = s.instruments["sa"]
    blocks = sa.components
    out = {
        name: lambda name=name: s.cached_output(blocks[name], "out")
        for name in ("input", "mixer", "if_filter")
    }
    assert float(session_query(s, "sa", ":POW:ATT?")) == 10.0
    level_in = _dbm(blocks["input"].signal().merged().amp[0])
    assert level_in == pytest.approx(-20.0, abs=0.01)

    # the attenuator: the tone where it was, 10 dB down
    after_att = out["input"]().merged()
    assert after_att.freq.tolist() == [TONE_HZ]
    assert _dbm(after_att.amp[0]) == pytest.approx(level_in - 10.0, abs=1e-9)

    # the mixer, tuned to the tone: the line is on the IF, less the conversion loss
    with s.tuned(TONE_HZ):
        mixed = out["mixer"]()
        filtered = out["if_filter"]()
    assert mixed.freq.tolist() == pytest.approx([IF_HZ])
    assert _dbm(mixed.amp[0]) == pytest.approx(level_in - 10.0 - CONVERSION_LOSS, abs=1e-9)
    # the IF filter: the line at the passband's center passes, times the IF gain
    assert filtered.freq.tolist() == pytest.approx([IF_HZ])
    assert _dbm(filtered.amp[0]) == pytest.approx(_dbm(mixed.amp[0]) + IF_GAIN, abs=1e-9)

    # tuned half an RBW away the line is off the IF by as much. The IF filter is the anti-alias
    # passband ahead of the ADC (#955), flat there; the 3.01 dB of the Gaussian resolution filter
    # is the RBW block's, behind the ADC
    rbw = float(session_query(s, "sa", ":BAND?"))
    with s.tuned(TONE_HZ + rbw / 2):
        off = out["mixer"]()
        off_filtered = out["if_filter"]()
    assert off.freq.tolist() == pytest.approx([IF_HZ - rbw / 2])
    assert _dbm(off.amp[0]) == pytest.approx(_dbm(mixed.amp[0]), abs=1e-9)
    assert _dbm(off_filtered.amp[0]) == pytest.approx(_dbm(filtered.amp[0]), abs=1e-3)
    half_power = blocks["rbw"].response(np.array([rbw / 2]), rbw, 0.0, "SAMPle")[0]
    assert 10 * math.log10(half_power) == pytest.approx(-3.0103, abs=1e-3)
    # half the IF bandwidth off its center the passband is 3.01 dB down, twice the bandwidth 30 dB
    bandwidth = float(blocks["if_filter"].param("bandwidth"))
    for away, down in ((bandwidth / 2, 3.0103), (bandwidth, 30.1), (2 * bandwidth, 60.2)):
        with s.tuned(TONE_HZ + away):
            edge = out["if_filter"]()
        assert _dbm(edge.amp[0]) == pytest.approx(_dbm(filtered.amp[0]) - down, abs=0.05)

    # one tuning point's output does not answer for another's: each has its own cache entry
    with s.tuned(TONE_HZ):
        assert out["mixer"]() is mixed
        with s.tuned(TONE_HZ + rbw / 2):  # re-entrant
            assert out["mixer"]() is off
        assert s.tune_hz == TONE_HZ
        assert out["if_filter"]() is filtered
    assert s.tune_hz is None
    # outside a tuning context the oscillator is at the center frequency
    assert out["mixer"]().freq.tolist() == pytest.approx([IF_HZ])
    assert session_query(s, "sa", ":FREQ:CENT 1.0002E9") is None
    assert out["mixer"]().freq.tolist() == pytest.approx([IF_HZ - 0.2e6])
    assert blocks["input"].tuned_ports == frozenset()  # the attenuator does not depend on the tuning
    assert out["input"]().freq.tolist() == [TONE_HZ]
    assert session_query(s, "sa", f":FREQ:CENT {TONE_HZ}") is None

    # what lies below the oscillator (the image side) is rejected
    with s.tuned(TONE_HZ + 2 * IF_HZ):
        assert out["mixer"]().freq.size == 0

    # 10 dB more attenuation: the attenuator's output and everything behind it drop 10 dB
    assert session_query(s, "sa", ":POW:ATT 20") is None
    assert _dbm(out["input"]().merged().amp[0]) == pytest.approx(level_in - 20.0, abs=1e-9)
    with s.tuned(TONE_HZ):
        assert _dbm(out["mixer"]().amp[0]) == pytest.approx(level_in - 20.0 - CONVERSION_LOSS, abs=1e-9)
    # the display is calibrated to the input: the marker still reads the tone's level
    assert session_query(s, "sa", ":CALC:MARK:MAX") is None
    assert float(session_query(s, "sa", ":CALC:MARK:Y?")) == pytest.approx(level_in, abs=0.01)
    assert session_query(s, "sa", ":SYST:ERR?") == '0,"No error"'


def test_a_tuned_read_is_passive(bench):
    s = bench
    mixer = s.instruments["sa"].components["mixer"]
    before = session_query(s, "sa", ":TRAC:DATA?")
    now, events = s.now_ns, len(s.history)
    with s.view():
        with s.tuned(TONE_HZ + 1e5):
            first = s.cached_output(mixer, "out")
        with s.tuned(TONE_HZ + 1e5):
            again = s.cached_output(mixer, "out")
    assert again is first  # the same tuning point reads the same
    assert (s.now_ns, len(s.history)) == (now, events)  # no time, no event
    assert session_query(s, "sa", ":TRAC:DATA?") == before  # the script's next trace is the same


def test_the_trace_reads_the_chain_by_its_wiring(bench):
    sa = bench.instruments["sa"].components["sa"]
    chain = sa.chain()
    assert [block.name for block in chain] == ["input", "mixer", "if_filter"]
    att = sa.attenuation()
    # Only the whole receiver's noise figure is declared: behind the attenuator the cascade is
    # that figure itself, at the input it is the figure plus the attenuation — exactly.
    assert sa.noise_figure() == 24.0
    whole = cascade(block.stage(TONE_HZ) for block in chain)
    assert whole.noise_figure_db == 24.0 + att
    assert whole.gain_db == pytest.approx(-att - CONVERSION_LOSS + IF_GAIN)


def test_the_mixers_output_notes_stay_a_tuple(bench):
    """The mixer's output passes its input's notes on as a tuple (an added note extends it), so
    signal arithmetic on it keeps working (#954)."""
    s = bench
    mixer = s.instruments["sa"].components["mixer"]
    with s.tuned(TONE_HZ):
        out = s.cached_output(mixer, "out")
    assert isinstance(out.notes, tuple)
    assert (out + out).notes == out.notes


def _codes(data: dict) -> list[tuple[str, str]]:
    _, report = validate_definition_data(data)
    return [(d.code, d.pointer) for d in report.diagnostics]


@pytest.mark.parametrize("target", ["mixer.out", "if_filter.out"])
def test_a_port_that_depends_on_the_tuning_cannot_leave_the_instrument(target):
    data = _data(DEFS / "sb-sa-1.yaml")
    assert _codes(data) == []
    data["ports"]["instrument"]["IF_OUT"] = target
    assert _codes(data) == [("E-DEF-PORT-TUNED", "/ports/instrument/IF_OUT")]


def _earlier(data: dict) -> dict:
    """The definition the way it was written before #952: no oscillator, the reference on the
    mixer, the sweep rate on the trace, the front end not wired, the earlier parameter names."""
    d = copy.deepcopy(data)
    blocks = {c["name"]: c for c in d["components"]}
    lo = blocks["lo"]["params"]
    d["components"] = [c for c in d["components"] if c["name"] != "lo"]
    mixer, trace, attenuator = (blocks[n]["params"] for n in ("mixer", "sa", "input"))
    del mixer["lo"], trace["lo"]
    mixer.update(clock=lo["clock"], source=lo["source"])
    trace.update(mixer="mixer", **{k: lo[k] for k in ("lo_rate", "sweep_overhead") if k in lo})
    trace["maxhold_limit"] = trace.pop("max_hold_sweeps")
    attenuator["mixer_limit"] = attenuator.pop("max_mixer_level")
    d["wiring"]["instrument"] = [
        w for w in d["wiring"]["instrument"] if w[0] not in ("input.out", "mixer.out")
    ]
    for command in d["commands"]:
        if command.get("op") == "lo.status":
            command["op"] = "mixer.status"
    return d


@pytest.mark.parametrize("path", ANALYZERS, ids=lambda p: p.stem)
def test_a_definition_written_the_earlier_way_is_the_same_definition(path):
    data = _data(path)
    earlier = _earlier(data)
    assert earlier != data
    assert _codes(earlier) == []
    old, new = InstrumentDefinition.model_validate(earlier), InstrumentDefinition.model_validate(data)
    assert {c.name: c for c in old.components} == {c.name: c for c in new.components}
    # what the macros add goes to the end of its table: the author's entries keep their places
    # (the oscillator of #952, then the digital back end of #955)
    assert [c.name for c in old.components][-4:] == ["lo", "adc", "ddc", "rbw"]
    assert old.wiring.instrument[-5:] == [
        ("input.out", "mixer.in"),
        ("mixer.out", "if_filter.in"),
        ("if_filter.out", "adc.in"),
        ("adc.out", "ddc.in"),
        ("ddc.out", "rbw.in"),
    ]
    assert sorted(old.wiring.instrument) == sorted(new.wiring.instrument)
    assert old.commands == new.commands


def test_tuned_restores_the_outer_point_when_the_read_fails(bench):
    with pytest.raises(RuntimeError), bench.tuned(1.0):
        raise RuntimeError
    assert bench.tune_hz is None
    assert np.isfinite(bench.instruments["sa"].components["lo"].tuned())


# ---- the front end's non-ideals (#954): a definition that declares them, one flow per figure ----

IDEALS = ROOT / "tests" / "definitions" / "test-specan-ideals.yaml"
CARRIER = 1.0e9
#: Two 50 Ω sources share the 50 Ω input: each tone delivers its level minus 3.52 dB, so this
#: drive puts both at −10 dBm on the analyzer's input.
TWO_TONE_LEVEL = -10.0 + 10 * math.log10(9 / 4)
#: The same divider at the compression point: 5 mW per tone on the node, 10 mW together — the
#: chain's P1dB (#954).
P1DB_EACH_LEVEL = 10.0 * math.log10(5.0) + 10 * math.log10(9 / 4)
IMAGE = CARRIER - 2 * IF_HZ  # the image of the tuned point: one IF below the oscillator's side


def _ideals_bench(seed: int = 3) -> BenchDefinition:
    """The bench of the non-ideal tests: two generators sharing the analyzer's input, one tone per
    generator at the node (the 3.52 dB the divider costs each is in the drive levels)."""
    return BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "specan-ideals",
            "instruments": {
                "sg1": {
                    "model": "sb-sg-1",
                    "state": {"freq.cw": CARRIER, "power.level": TWO_TONE_LEVEL, "output.enabled": True},
                },
                "sg2": {
                    "model": "sb-sg-1",
                    "state": {
                        "freq.cw": CARRIER + 1.0e6,
                        "power.level": TWO_TONE_LEVEL,
                        "output.enabled": True,
                    },
                },
                "sa": {
                    "model": "test-specan-ideals",
                    "state": {"freq.center": CARRIER, "freq.span": 4.0e6, "sweep.points": 301},
                },
            },
            "wires": [
                {"from": "sg1.RF_OUT", "to": "sa.RF_IN"},
                {"from": "sg2.RF_OUT", "to": "sa.RF_IN"},
            ],
            "run": {"seed": seed, "clock": "command", "idle": "off"},
        }
    )


@pytest.fixture(scope="module")
def ideals(tmp_path_factory):
    """Two generators on an analyzer that declares the non-ideals: the mixer's intercepts (+20 dBm
    IIP2, +10 dBm IIP3, +10 dBm P1dB), a 60 dB image rejection, a −40 dBm oscillator feedthrough,
    a −40 dBc |f_rf − 2·f_lo| spur, a −80 dBm residual at 1.5 GHz, phase noise at −80/−100/−130
    dBc/Hz at 100 Hz/10 kHz/1 MHz, and a flatness table over 1.1–1.3 GHz that dips to −1 dB at
    1.2 GHz and holds 0 dB outside it. No attenuation: the mixer's figures are the chain's."""
    where = tmp_path_factory.mktemp("ideals")
    (where / IDEALS.name).write_text(yaml.safe_dump(_data(IDEALS), allow_unicode=True), "utf-8")
    s = open_bench(_ideals_bench(), definition_dirs=[where, DEFS])
    yield s
    s.close()


@pytest.fixture(scope="module")
def preamp(tmp_path_factory):
    """The same analyzer with the preamplifier in the chain — a 20 dB gain, a 6 dB figure of its
    own — and the intercepts moved: the mixer's are gone, the IF amplifier carries the IIP3. The
    chain's figures then come from every stage, referred through the preamplifier (#954)."""
    d = _data(IDEALS)
    blocks = {c["name"]: c for c in d["components"]}
    blocks["input"]["params"].update(preamp_gain=20.0, preamp_noise_figure=6.0)
    for key in ("iip2_dbm", "iip3_dbm", "p1db_dbm"):
        del blocks["mixer"]["params"][key]
    blocks["if_filter"]["params"]["iip3_dbm"] = 10.0
    where = tmp_path_factory.mktemp("preamp")
    (where / IDEALS.name).write_text(yaml.safe_dump(d, allow_unicode=True), "utf-8")
    s = open_bench(_ideals_bench(), definition_dirs=[where, DEFS])
    yield s
    s.close()


def _marker_at(s, f_hz: float) -> float:
    session_query(s, "sa", f":CALC:MARK:X {f_hz:.6E}")
    return float(session_query(s, "sa", ":CALC:MARK:Y?"))


def test_two_tones_show_the_third_order_products_at_the_intercept(ideals):
    """Two −10 dBm tones into a +10 dBm IIP3: the 2f₁−f₂ product reads −50 dBm, and 10 dB less
    drive drops it 30 dB (#954)."""
    s = ideals
    assert float(session_query(s, "sa", ":POW:ATT?")) == 0.0
    # a narrow RBW keeps the receiver's own noise 50 dB under the products: the |v|² cross term
    # with it would sway the reading
    session_query(s, "sa", ":BAND:AUTO OFF")
    session_query(s, "sa", ":BAND 1E2")
    assert _marker_at(s, 2 * CARRIER - (CARRIER + 1.0e6)) == pytest.approx(-50.0, abs=0.1)
    session_query(s, "sg1", f":POW:LEV {TWO_TONE_LEVEL - 10:.4f}")
    session_query(s, "sg2", f":POW:LEV {TWO_TONE_LEVEL - 10:.4f}")
    assert _marker_at(s, 2 * CARRIER - (CARRIER + 1.0e6)) == pytest.approx(-80.0, abs=0.1)
    session_query(s, "sg1", f":POW:LEV {TWO_TONE_LEVEL:.4f}")
    session_query(s, "sg2", f":POW:LEV {TWO_TONE_LEVEL:.4f}")
    session_query(s, "sa", ":BAND:AUTO ON")


def test_the_phase_noise_is_a_skirt_around_the_carrier(ideals):
    """−100 dBc/Hz at 10 kHz: the averaged trace beside a −10 dBm carrier reads the density over
    the RBW's noise bandwidth — 2.51 dB lower in the dB average, the mean of a noise point's dB
    values — and a 10× narrower RBW lowers the skirt 10 dB (#954). The 106 points 3–10 kHz out
    are corrected by their own density before averaging: the table is a −10 dB/decade slope
    there."""
    s = ideals
    session_query(s, "sg2", ":OUTP OFF")
    session_query(s, "sa", f":FREQ:CENT {CARRIER:.6E}")
    session_query(s, "sa", ":FREQ:SPAN 2E4")
    session_query(s, "sa", ":SWE:POINts 151")  # a cell per point at both RBWs, the carrier on point 75
    session_query(s, "sa", ":BAND:AUTO OFF")
    session_query(s, "sa", ":BAND 1E3")
    session_query(s, "sa", ":DET AVER")
    session_query(s, "sa", ":TRAC:TYPE AVER")
    session_query(s, "sa", ":AVER:COUNt 100")
    session_query(s, "sa", ":INIT:CONT OFF")
    session_query(s, "sa", ":INIT")

    def trace() -> np.ndarray:
        return np.array([float(v) for v in session_query(s, "sa", ":TRAC:DATA?").split(",")])

    y = trace()
    carrier = y[75]  # the point exactly on the carrier: undiluted, and within RBW/2 of its line, no skirt
    idx = np.concatenate([np.arange(0, 53), np.arange(98, 151)])  # 3 kHz out or more: clear of the line
    o = np.abs((idx - 75) * (2.0e4 / 150))  # the points' offsets from the carrier
    ell = -80.0 - 10 * np.log10(o / 100.0)
    d = y[idx] - (10 * math.log10(1.0645 * 1.0e3) + ell)
    # the skirt is noise: its dB mean sits 10·γ/ln10 = 2.51 dB under the density's level; the
    # corrected average of 106 points, each the mean of 100 sweeps, holds that to 5σ = ±0.27 dB
    assert d.mean() - carrier == pytest.approx(-10.0 * float(np.euler_gamma) / math.log(10.0), abs=0.5)
    session_query(s, "sa", ":BAND 1E2")
    session_query(s, "sa", ":INIT")
    y2 = trace()
    assert y2[idx].mean() - y[idx].mean() == pytest.approx(-10.0, abs=0.5)
    session_query(s, "sa", ":INIT:CONT ON")
    session_query(s, "sa", ":AVER:COUNt 10")
    session_query(s, "sa", ":TRAC:TYPE WRIT")
    session_query(s, "sa", ":DET POS")
    session_query(s, "sa", ":BAND:AUTO ON")
    session_query(s, "sa", ":SWE:POINts 301")
    session_query(s, "sa", ":FREQ:SPAN 4E6")
    session_query(s, "sg2", ":OUTP ON")


def test_the_flatness_table_scales_the_display(ideals):
    """The table holds −1 dB at 1.2 GHz: a tone there reads 1 dB low, and one below the table or
    above it reads its true level — the table is a patch on the calibration, not an extrapolation
    (#954)."""
    s = ideals
    session_query(s, "sg2", ":OUTP OFF")
    session_query(s, "sg1", ":FREQ:CW 1.2E9")
    session_query(s, "sa", ":FREQ:CENT 1.2E9")
    assert _marker_at(s, 1.2e9) == pytest.approx(-11.0, abs=0.05)
    session_query(s, "sg1", f":FREQ:CW {CARRIER:.6E}")  # below the table
    session_query(s, "sa", f":FREQ:CENT {CARRIER:.6E}")
    assert _marker_at(s, CARRIER) == pytest.approx(-10.0, abs=0.05)
    session_query(s, "sg1", ":FREQ:CW 1.4E9")  # above it
    session_query(s, "sa", ":FREQ:CENT 1.4E9")
    assert _marker_at(s, 1.4e9) == pytest.approx(-10.0, abs=0.05)
    session_query(s, "sg1", f":FREQ:CW {CARRIER:.6E}")
    session_query(s, "sa", f":FREQ:CENT {CARRIER:.6E}")
    session_query(s, "sg2", ":OUTP ON")


def test_the_image_comes_through_at_the_rejection_ratio(ideals):
    """A tone one IF below the oscillator's side folds onto the tuned point, 60 dB down (#954)."""
    s = ideals
    session_query(
        s, "sg2", ":OUTP OFF"
    )  # the off source is still a 50 Ω load: the divider stays, and the node's lone tone is −10 dBm
    session_query(s, "sg1", f":FREQ:CW {IMAGE:.6E}")
    assert _marker_at(s, CARRIER) == pytest.approx(-70.0, abs=0.1)
    session_query(s, "sg1", f":FREQ:CW {CARRIER:.6E}")
    session_query(s, "sg1", f":POW:LEV {TWO_TONE_LEVEL:.4f}")
    session_query(s, "sg2", ":OUTP ON")


def test_a_tone_at_the_compression_point_reads_one_db_low(ideals):
    """The chain's P1dB is +10 dBm (the mixer's, at no attenuation): a tone there reads 1 dB down
    (#954)."""
    s = ideals
    session_query(s, "sg2", ":OUTP OFF")
    session_query(s, "sg1", f":POW:LEV {10.0 + 10 * math.log10(9 / 4):.4f}")  # the divider: node at +10
    assert _marker_at(s, CARRIER) == pytest.approx(9.0, abs=0.05)
    session_query(s, "sg1", f":POW:LEV {TWO_TONE_LEVEL:.4f}")
    assert _marker_at(s, CARRIER) == pytest.approx(-10.0, abs=0.05)
    session_query(s, "sg2", ":OUTP ON")


def test_two_tones_at_the_compression_point_compress_together(ideals):
    """Both tones at 5 mW — 10 mW together, the chain's P1dB: each rides the multi-tone drive
    2·total − p = 1.5×P1dB, and the product rides its parents' drive of exactly P1dB (#954)."""
    s = ideals
    session_query(s, "sg1", f":POW:LEV {P1DB_EACH_LEVEL:.4f}")
    session_query(s, "sg2", f":POW:LEV {P1DB_EACH_LEVEL:.4f}")
    c = 1.0 - 10.0 ** (-1.0 / 20.0)  # the gain the third-order model leaves at P1dB
    tone = 10.0 * math.log10(5.0) + 20.0 * math.log10(1.0 - c * 1.5)
    product = 3 * 10.0 * math.log10(5.0) - 20.0 + 20.0 * math.log10(1.0 - c * 1.0)
    assert _marker_at(s, CARRIER) == pytest.approx(tone, abs=0.05)
    assert _marker_at(s, 2 * CARRIER - (CARRIER + 1.0e6)) == pytest.approx(product, abs=0.05)
    session_query(s, "sg1", f":POW:LEV {TWO_TONE_LEVEL:.4f}")
    session_query(s, "sg2", f":POW:LEV {TWO_TONE_LEVEL:.4f}")


def test_the_mixer_probe_compresses_like_the_display(ideals):
    """The mixer's ``out`` probe and the display agree at the compression point: this mixer
    declares no conversion loss, so the probe's IF line is the equivalent input line and reads
    what the display reads; its spur shows at both solutions of the mixer equation, and none of
    its frequencies is negative (#954)."""
    s = ideals
    session_query(s, "sg2", ":OUTP OFF")
    session_query(
        s, "sg1", f":POW:LEV {10.0 + 10 * math.log10(9 / 4):.4f}"
    )  # the divider: the node at +10 dBm
    display = _marker_at(s, CARRIER)
    assert display == pytest.approx(9.0, abs=0.05)
    mixer = s.instruments["sa"].components["mixer"]
    with s.tuned(CARRIER):
        out = s.cached_output(mixer, "out").merged()
    i = int(np.argmin(np.abs(out.freq - IF_HZ)))
    assert _dbm(out.amp[i]) == pytest.approx(display, abs=0.05)
    assert np.isclose(out.freq, abs(CARRIER - 2 * (CARRIER - IF_HZ))).any()
    assert np.isclose(out.freq, CARRIER + 2 * (CARRIER - IF_HZ)).any()
    assert (out.freq >= 0.0).all()
    session_query(s, "sg1", f":POW:LEV {TWO_TONE_LEVEL:.4f}")
    session_query(s, "sg2", ":OUTP ON")


def test_the_mixer_probes_products_match_the_display(ideals):
    """The probe's distortion products are the display's, referred through the attenuation: at
    10 dB of it the display's product — the mixer's +10 dBm IIP3 referred through the attenuator
    is the chain's +20 dBm — reads −70 dBm, and the probe at the mixer's input −80 (#954)."""
    s = ideals
    session_query(s, "sa", ":POW:ATT 10")
    session_query(s, "sa", ":BAND:AUTO OFF")
    session_query(s, "sa", ":BAND 1E2")
    session_query(s, "sa", ":DET SAMP")  # a sample per bucket: no peak-detector bias over the cells
    display = _marker_at(s, 2 * CARRIER - (CARRIER + 1.0e6))
    assert display == pytest.approx(-70.0, abs=0.05)
    mixer = s.instruments["sa"].components["mixer"]
    with s.tuned(CARRIER):
        out = s.cached_output(mixer, "out").merged()
    f_if = 2 * CARRIER - (CARRIER + 1.0e6) - (CARRIER - IF_HZ)  # the product's IF frequency
    i = int(np.argmin(np.abs(out.freq - f_if)))
    assert _dbm(out.amp[i]) == pytest.approx(display - 10.0, abs=0.05)
    assert (out.freq >= 0.0).all()
    session_query(s, "sa", ":DET POS")
    session_query(s, "sa", ":BAND:AUTO ON")
    session_query(s, "sa", ":POW:ATT 0")


def test_residuals_and_the_feedthrough_show_without_input(ideals):
    """Generators off: the declared residual at 1.5 GHz and the oscillator's −40 dBm feedthrough
    at 0 Hz are on the trace anyway (#954)."""
    s = ideals
    session_query(s, "sg1", ":OUTP OFF")
    session_query(s, "sg2", ":OUTP OFF")
    session_query(s, "sa", ":FREQ:CENT 1.5E9")
    session_query(s, "sa", ":FREQ:SPAN 1E6")
    session_query(s, "sa", ":BAND:AUTO OFF")
    session_query(s, "sa", ":BAND 1E2")  # the floor well below the residual
    assert _marker_at(s, 1.5e9) == pytest.approx(-80.0, abs=0.05)
    session_query(s, "sa", ":FREQ:SPAN 1E5")  # the span first: the center then slides to hold 0 Hz
    session_query(s, "sa", ":FREQ:CENT 5E4")
    assert _marker_at(s, 0.0) == pytest.approx(-40.0, abs=0.05)
    session_query(s, "sa", ":BAND:AUTO ON")
    session_query(s, "sa", ":FREQ:CENT 1.5E9")
    session_query(s, "sa", ":FREQ:SPAN 4E6")
    session_query(s, "sg1", ":OUTP ON")
    session_query(s, "sg2", ":OUTP ON")


def test_the_spur_shows_at_both_solutions_of_the_mixer_equation(ideals):
    """The declared |f_rf − 2·f_lo| response of a 1 GHz tone lands on the IF from either side of
    the oscillator's second harmonic: the receiver shows the line at (f + if)/2 and (f + 3·if)/2,
    both −40 dBc (#954)."""
    s = ideals
    session_query(s, "sg2", ":OUTP OFF")
    session_query(s, "sa", ":FREQ:CENT 521.4E6")
    session_query(s, "sa", ":FREQ:SPAN 3E7")
    session_query(s, "sa", ":BAND:AUTO OFF")
    session_query(s, "sa", ":BAND 1E4")
    assert _marker_at(s, (CARRIER + IF_HZ) / 2) == pytest.approx(-50.0, abs=0.05)
    assert _marker_at(s, (CARRIER + 3 * IF_HZ) / 2) == pytest.approx(-50.0, abs=0.05)
    session_query(s, "sa", ":BAND:AUTO ON")
    session_query(s, "sa", f":FREQ:CENT {CARRIER:.6E}")
    session_query(s, "sa", ":FREQ:SPAN 4E6")
    session_query(s, "sg2", ":OUTP ON")


def test_the_second_order_products_show_at_the_intercept(ideals):
    """The mixer's +20 dBm IIP2 of two −10 dBm tones: the sum product reads p₁ + p₂ − IIP2 =
    −40 dBm and the second harmonic 2p − IIP2 − 6.02 dB (a harmonic sits 6.02 dB under the
    two-tone product of its order); the products' own compression at these drives is 0.02 dB,
    inside the tolerance (#954)."""
    s = ideals
    session_query(s, "sa", ":FREQ:CENT 2.001E9")
    assert _marker_at(s, 2.001e9) == pytest.approx(-40.0, abs=0.05)
    assert _marker_at(s, 2.0e9) == pytest.approx(2 * (-10.0) - 20.0 - 10 * math.log10(4.0), abs=0.05)
    session_query(s, "sa", f":FREQ:CENT {CARRIER:.6E}")


def test_the_preamplifiers_figure_cascades_into_the_floor(preamp):
    """The preamplifier's 6 dB figure ahead of the mixer's 24, behind 20 dB of gain: Friis gives
    8.12 dB at the input — the averaged floor reads kT0 + NF + 10·log10(NBW·RBW) − 2.51 dB (the dB
    average of noise) — and 10 dB of attenuation ahead of the preamplifier costs 10 dB of figure
    (#954)."""
    s = preamp
    session_query(s, "sg1", ":OUTP OFF")
    session_query(s, "sg2", ":OUTP OFF")
    session_query(s, "sa", ":FREQ:SPAN 1E5")
    session_query(s, "sa", ":BAND:AUTO OFF")
    session_query(s, "sa", ":BAND 1E3")
    session_query(s, "sa", ":DET AVER")
    session_query(s, "sa", ":TRAC:TYPE AVER")
    session_query(s, "sa", ":AVER:COUNt 100")
    session_query(s, "sa", ":INIT:CONT OFF")
    session_query(s, "sa", ":INIT")
    gamma = -10.0 * float(np.euler_gamma) / math.log(10.0)
    y = np.array([float(v) for v in session_query(s, "sa", ":TRAC:DATA?").split(",")])
    nf = 10 * math.log10(10**0.6 + (10**2.4 - 1) / 10**2)
    assert y.mean() == pytest.approx(-174.0 + nf + 10 * math.log10(1.0645 * 1.0e3) + gamma, abs=0.2)
    session_query(s, "sa", ":POW:ATT 10")
    session_query(s, "sa", ":INIT")
    y = np.array([float(v) for v in session_query(s, "sa", ":TRAC:DATA?").split(",")])
    nf = 10 * math.log10(10**1.6 + (10**2.4 - 1) / 10**1)
    assert y.mean() == pytest.approx(-174.0 + nf + 10 * math.log10(1.0645 * 1.0e3) + gamma, abs=0.2)
    session_query(s, "sa", ":POW:ATT 0")
    session_query(s, "sa", ":INIT:CONT ON")
    session_query(s, "sa", ":AVER:COUNt 10")
    session_query(s, "sa", ":TRAC:TYPE WRIT")
    session_query(s, "sa", ":DET POS")
    session_query(s, "sa", ":BAND:AUTO ON")
    session_query(s, "sa", ":FREQ:SPAN 4E6")
    session_query(s, "sg1", ":OUTP ON")
    session_query(s, "sg2", ":OUTP ON")


def test_the_if_filters_intercept_refers_through_the_preamplifier(preamp):
    """The IF amplifier's +10 dBm IIP3 behind the preamplifier's 20 dB of gain: the chain's
    intercept is −10 dBm at the input, so two −10 dBm tones put their 2f₁−f₂ product at their own
    level — and with no compression point declared anywhere, nothing compresses (#954)."""
    s = preamp
    assert _marker_at(s, CARRIER) == pytest.approx(-10.0, abs=0.05)
    assert _marker_at(s, 2 * CARRIER - (CARRIER + 1.0e6)) == pytest.approx(-10.0, abs=0.05)


def test_the_phase_noise_stream_is_deterministic_noise(tmp_path):
    """The skirt is noise drawn per resolution cell from a counter-based generator: a trace is a
    pure function of the settings and the seed — the same seed reads the same trace again, another
    seed another one (#954)."""
    (tmp_path / IDEALS.name).write_text(IDEALS.read_text(encoding="utf-8"), encoding="utf-8")

    def trace(seed: int) -> np.ndarray:
        s = open_bench(_ideals_bench(seed), definition_dirs=[tmp_path, DEFS])
        try:
            session_query(s, "sa", ":INIT:CONT OFF")
            session_query(s, "sa", ":INIT")
            first = session_query(s, "sa", ":TRAC:DATA?")
            assert session_query(s, "sa", ":TRAC:DATA?") == first  # the frozen trace reads the same
            return np.array([float(v) for v in first.split(",")])
        finally:
            s.close()

    assert np.array_equal(trace(3), trace(3))
    assert not np.array_equal(trace(3), trace(4))
