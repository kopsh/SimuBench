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
