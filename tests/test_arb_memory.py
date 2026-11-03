"""Arbitrary waveforms in volatile memory (#426), as the Trueform loads them: DATA:ARBitrary
<name>,<values> (or :DAC codes), FUNCtion:ARBitrary <name> selects, FUNCtion ARB plays it through
``waveform_synth``'s ``arb`` reference, at the arb_table's sample rate. ``sb-awg-2`` is patched here."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
DEFS = ROOT / "validation" / "instruments"


@pytest.fixture
def awg(tmp_path):
    d = yaml.safe_load((DEFS / "sb-awg-2.yaml").read_text("utf-8"))
    d["channel_state"]["function"]["options"] += ["ARBitrary", "PRBS"]
    d["channel_state"]["srate"] = {"type": "float", "unit": "HZ", "min": 1.0, "max": 1.0e9, "default": 4000.0}
    d["channel_components"] += [
        {"name": "arb", "type": "arb_table", "params": {"rate": "$ch.srate", "scale": 0.5, "min_points": 8}},
        {"name": "bits", "type": "prbs_source", "params": {"order": 7, "rate": 1.0e3, "level": 0.5}},
    ]
    for c in d["channel_components"]:
        if c["name"] == "synth":
            c["params"].update({"arb": "arb", "prbs": "bits"})
    d["commands"] += [
        {
            "header": "[:SOURce<ch>]:DATA:ARBitrary",
            "op": "ch.arb.load",
            "query": False,
            "params": [{"name": "name", "type": "string"}, {"name": "values", "type": "float_list"}],
        },
        {
            "header": "[:SOURce<ch>]:DATA:ARBitrary:DAC",
            "op": "ch.arb.load_dac",
            "query": False,
            "params": [{"name": "name", "type": "string"}, {"name": "values", "type": "float_list"}],
        },
        {
            "header": "[:SOURce<ch>]:FUNCtion:ARBitrary",
            "op": "ch.arb.select",
            "params": [{"name": "name", "type": "string", "optional": True}],
        },
        {"header": "[:SOURce<ch>]:FUNCtion:ARBitrary:POINts", "op": "ch.arb.points", "set": False},
        {"header": "[:SOURce<ch>]:FUNCtion:ARBitrary:SRATe", "state": "ch.srate"},
        {"header": "[:SOURce<ch>]:DATA:VOLatile:CATalog", "op": "ch.arb.catalog", "set": False},
        {"header": "[:SOURce<ch>]:DATA:VOLatile:CLEar", "op": "ch.arb.clear", "query": False},
    ]
    (tmp_path / "sb-awg-2.yaml").write_text(yaml.safe_dump(d, allow_unicode=True), "utf-8")
    bench = BenchDefinition.model_validate(
        {"schema": "simubench.bench/2", "name": "b", "instruments": {"awg": {"model": "sb-awg-2"}}}
    )
    session = open_bench(bench, definition_dirs=[tmp_path, DEFS])

    def q(text: str) -> str:
        session.write("awg", (text + "\n").encode())
        reply = session.read("awg")
        return reply.decode().strip() if reply else ""

    yield q, session
    session.close()


def _signal(session):
    return session.instruments["awg"].components["ch1.synth"].output("out")


def test_load_select_and_play(awg):
    q, session = awg
    square8 = ",".join(["1"] * 4 + ["-1"] * 4)
    q(f"DATA:ARB sq8,{square8}")
    q("FUNC:ARB sq8")
    q("FUNC ARB")
    assert q("FUNC:ARB?") == '"sq8"'
    assert q("FUNC:ARB:POIN?") == "8"
    assert q("SYST:ERR?").endswith('"No error"')
    sig = _signal(session)
    # 4000 Sa/s over 8 points: a 500 Hz fundamental, ±0.5 (1 Vpp at the synth)
    fundamental = float(sig.freq[int(np.argmax(np.abs(sig.amp)))])
    assert fundamental == pytest.approx(500.0)
    t = np.linspace(0, 2e-3, 400, endpoint=False)
    assert np.max(sig.sample(t * 1e9)) == pytest.approx(0.5, abs=0.06)


def test_dac_codes_and_the_catalog(awg):
    q, _ = awg
    q("DATA:ARB:DAC ramp," + ",".join(str(int(v)) for v in np.linspace(-32767, 32767, 16)))
    q("DATA:ARB sq8," + ",".join(["1"] * 4 + ["-1"] * 4))
    assert q("DATA:VOL:CAT?") == '"ramp","sq8"'
    q("DATA:VOL:CLE")
    assert q("DATA:VOL:CAT?") == '""'


def test_refusals(awg):
    q, _ = awg
    q("DATA:ARB short,0,1,0")  # fewer than 8 points
    assert q("SYST:ERR?").startswith("-222")
    q("DATA:ARB loud," + ",".join(["2"] * 8))  # outside -1..+1
    assert q("SYST:ERR?").startswith("-222")
    q("FUNC:ARB nothere")
    assert q("SYST:ERR?").startswith("-224")


def test_prbs_plays_the_prbs_source(awg):
    q, session = awg
    q("FUNC PRBS")
    sig = _signal(session)
    assert len(sig.freq) > 10  # a sinc-shaped line spectrum, not a single tone
