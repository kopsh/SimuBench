"""Instrument templates (issue 125, instrument-authoring.md 7.4, D3): an agent fills in a data sheet,
the oscilloscope template generates section 8's modules and a generic SCPI command set."""

from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
import pytest
import yaml

from simubench.api import open_bench
from simubench.engine.session import Session
from simubench.library.templates import INPUTS, get_template, templates
from simubench.models.bench import BenchDefinition
from simubench.models.definition import InstrumentDefinition
from simubench.server.catalog import components

ROOT = Path(__file__).resolve().parents[1]
SCOPE = get_template("oscilloscope")

#: sb-dso-4 as a data sheet: the template reproduces it reply for reply.
DSO4_INPUTS = {
    "identity": {
        "manufacturer": "SimuBench", "model": "SB-DSO-4", "serial": "SBDSO000001", "firmware": "1.0.0",
        "description": "四通道数字示波器，200 MHz，16 位波形传输，输入 1 MΩ / 50 Ω 可选。",
    },
    "channels": {
        "count": 4, "bandwidth_hz": 2.0e8, "bandwidth_limit_hz": 2.0e7,
        "impedances": [
            {"name": "ONEMeg", "ohms": 1.0e6, "label": "1 MΩ"}, {"name": "FIFTy", "ohms": 50.0, "label": "50 Ω"},
        ],
    },
    "vertical": {"scale_range": [1.0e-3, 100.0], "default_scale": 1.0, "probe_range": [0.001, 10000.0]},
    "adc": {"bits": 0},
    "timebase": {"scale_range": [1.0e-9, 50.0]},
    "transfer": {"byte_codes_per_div": 32.0, "word_codes_per_div": 8192.0},
}  # fmt: skip

#: The acceptance scope scenario's commands and more (formats, measurements, trigger, coupling,
#: impedance, limits, unknown headers), each followed by what the scope answers.
DSO4_SEQUENCE = """\
*IDN?
:CHANnel1:SCALe?
:CHANnel1:PROBe 10
:CHANnel1:SCALe 0.5
:TIMebase:SCALe 2E-4
:WAVeform:PREamble?
:DIGitize CHANnel1
:WAVeform:PREamble?
:WAVeform:DATA?
:WAVeform:FORMat BYTE
:WAVeform:DATA?
:WAVeform:FORMat ASCii
:WAVeform:DATA?
:WAVeform:FORMat WORD;:WAVeform:UNSigned 1;:WAVeform:BYTeorder MSBFirst
:WAVeform:DATA?
:MEASure:VPP?
:MEASure:VMAX?
:MEASure:VMIN?
:MEASure:VAMPlitude?
:MEASure:VRMS?
:MEASure:VAVerage?
:MEASure:FREQuency?
:MEASure:PERiod?
:MEASure:VPP? CHANnel2
:TRIGger:EDGE:SLOPe NEGative;:TRIGger:LEVel 0.3
:DIGitize CHANnel1
:WAVeform:DATA?
:TRIGger:LEVel 50
:SINGle
:WAVeform:PREamble?
:TRIGger:LEVel 0;:TRIGger:SWEep NORMal
:TRIGger:SWEep?
:CHANnel1:COUPling AC
:DIGitize CHANnel1
:MEASure:VAVerage?
:CHANnel1:IMPedance FIFTy
:CHANnel1:IMPedance?
:DIGitize CHANnel1
:MEASure:VPP?
:CHANnel1:IMPedance ONEMeg;:CHANnel1:COUPling DC;:CHANnel1:BWLimit 1
:DIGitize CHANnel1
:MEASure:VPP?
:CHANnel1:SCALe 1000
:SYSTem:ERRor?
:CHANnel3:DISPlay?
:CHANnel1:DISPlay?
:WAVeform:POINts 10000
:WAVeform:POINts 777
:WAVeform:POINts?
:SYSTem:ERRor?
:DIGitize CHANnel1
:WAVeform:DATA?
:FOO?
:SYSTem:ERRor?
:WAVeform:SOURce CHANnel2;:WAVeform:DATA?
:SYSTem:ERRor?
""".splitlines()


def _bench(
    tmp_path: Path, definition: dict, *, deviation: str = "nominal", seed: int = 7, clock: str = "stepped"
) -> Path:
    """sb-awg-2 (output on) through the acceptance RC low pass and a 10:1 probe into the scope's CH1. A scope whose
    single acquisitions complete in virtual time needs a clock that moves with the commands."""
    (tmp_path / "instruments").mkdir(exist_ok=True)
    (tmp_path / "instruments" / f"{definition['id']}.yaml").write_text(
        yaml.safe_dump(definition, allow_unicode=True), "utf-8"
    )
    bench = yaml.safe_load((ROOT / "acceptance" / "benches" / "awg_rc_scope.bench.yaml").read_text("utf-8"))
    bench["instruments"]["scope"] = {"model": definition["id"], "deviation": deviation}
    bench["run"] |= {"seed": seed, "clock": clock}
    bench["instruments"]["awg"]["state"]["ch1.output"] = True
    path = tmp_path / f"{definition['id']}-{deviation}-{seed}.bench.yaml"
    path.write_text(yaml.safe_dump(bench), "utf-8")
    return path


def _replies(path: Path, sequence: list[str]) -> list[bytes | None]:
    s = open_bench(path)
    try:
        out = []
        for command in sequence:
            s.write("scope", command.encode() + b"\n")
            out.append(s.read("scope") if "?" in command else None)
        return out
    finally:
        s.close()


def _expand(inputs: dict, def_id: str = "acme-dso-204") -> dict:
    out = SCOPE.expand(inputs, def_id)
    assert out.ok, [d.as_dict() for d in out.diagnostics]
    assert out.definition is not None
    return out.definition


# ---- the mechanism -------------------------------------------------------------------------------


@pytest.mark.parametrize("template", templates(), ids=lambda t: t.id)
def test_the_schema_labels_every_input_in_both_languages(template):
    schema = template.schema()
    groups = [schema, *schema.get("$defs", {}).values()]
    for group in groups:
        for name, field in group.get("properties", {}).items():
            assert field.get("title") and field.get("title_en"), (group.get("title"), name)
            assert field.get("description"), (group.get("title"), name)


def test_the_templates_and_their_units():
    assert [t.id for t in templates()] == ["oscilloscope", "signal_source"]
    assert SCOPE.schema()["$defs"]["ScaleRowIn"]["properties"]["noise_v_rms"]["unit"] == "V rms"


def test_the_example_expands_clean_and_records_its_template():
    definition = _expand(SCOPE.example())
    assert definition["template"] == {"id": "oscilloscope", "version": 1, "inputs": SCOPE.example()}
    assert InstrumentDefinition.model_validate(definition).template is not None
    # Only generic components (#93): a template never needs a model's own component.
    generic = {c.type: c.generic for c in components()}
    used = [c["type"] for c in definition["components"] + definition["channel_components"]]
    assert all(generic[t] for t in used), used
    # The record is ignored when running: without it the instrument answers the same.
    bare = {k: v for k, v in definition.items() if k != "template"}
    a, b = InstrumentDefinition.model_validate(definition), InstrumentDefinition.model_validate(bare)
    assert a.model_copy(update={"template": None}) == b


def test_a_template_built_sb_dso_4_answers_as_sb_dso_4(tmp_path):
    built = _expand(DSO4_INPUTS, "tpl-dso-4")
    reference = yaml.safe_load((ROOT / "validation" / "instruments" / "sb-dso-4.yaml").read_text("utf-8"))
    ours = _replies(_bench(tmp_path, built), DSO4_SEQUENCE)
    theirs = _replies(_bench(tmp_path, {**reference, "id": "copy-dso-4"}), DSO4_SEQUENCE)
    differ = [(c, a, b) for c, a, b in zip(DSO4_SEQUENCE, ours, theirs, strict=True) if a != b]
    assert not differ, differ[:3]
    assert sum(r is not None for r in ours) == 32


# ---- the example in a bench ----------------------------------------------------------------------


@pytest.fixture
def example(tmp_path):
    return _expand(SCOPE.example())


def _q(s: Session, cmd: str) -> bytes:
    s.write("scope", cmd.encode() + b"\n")
    return s.read("scope") or b""


def test_waveform_reading_follows_the_preamble(tmp_path, example):
    s = open_bench(_bench(tmp_path, example, clock="command"))
    try:
        _q(s, ":CHANnel1:PROBe 10;:CHANnel1:SCALe 0.5;:TIMebase:SCALe 2E-4;:WAVeform:FORMat BYTE")
        _q(s, ":DIGitize CHANnel1")
        assert _q(s, "*OPC?") == b"1\n"
        pre = _q(s, ":WAVeform:PREamble?").decode().split(",")
        raw = _q(s, ":WAVeform:DATA?")
        digits = int(raw[1:2])
        codes = np.frombuffer(raw[2 + digits : 2 + digits + int(raw[2 : 2 + digits])], "u1").astype(float)
        rec = s.instruments["scope"].components["acq"].records[1]
    finally:
        s.close()
    assert codes.size == int(pre[2]) == 1000
    assert np.ptp(rec.volts) > 1.0  # the source's sine through the RC low pass
    yinc, yorg, yref = float(pre[7]), float(pre[8]), float(pre[9])
    volts = (codes - yref) * yinc + yorg
    step = rec.volts.size // 1000
    np.testing.assert_allclose(volts, rec.volts[::step][:1000], atol=0.5 / 25 * 0.51)
    # 8 bits, 25 codes per division at 0.5 V/div: the record is on the ADC's grid.
    lsb = 0.5 / 25
    assert np.max(np.abs(rec.volts / lsb - np.round(rec.volts / lsb))) < 1e-6


def test_measurement_trigger_and_single_synchronisation(tmp_path, example):
    s = open_bench(_bench(tmp_path, example, clock="command"))
    try:
        _q(s, ":CHANnel1:PROBe 10;:CHANnel1:SCALe 0.5;:TIMebase:SCALe 2E-4")
        _q(s, ":DIGitize CHANnel1")
        vpp = float(_q(s, ":MEASure:VPP? CHANnel1"))
        freq = float(_q(s, ":MEASure:FREQuency? CHANnel1"))
        rec = s.instruments["scope"].components["acq"].records[1]
        # completion: pending — :SINGle arms; the acquisition completes in virtual time after its edge.
        _q(s, ":TRIGger:SWEep NORMal;:TRIGger:LEVel 0;:SINGle")
        acq = s.instruments["scope"].components["acq"]
        assert acq.pending_mode()
        assert _q(s, "*OPC?") == b"1\n"
        single = acq.records[1]
    finally:
        s.close()
    assert vpp == pytest.approx(float(np.ptp(rec.volts)), rel=1e-6)
    assert freq == pytest.approx(1000.0, rel=1e-3)  # sb-awg-2's power-on frequency
    # The trigger (a rising crossing of 0 V) sits at the centre reference: t = 0 between samples.
    i = round(-single.origin / single.dt_s)
    assert single.volts[i - 2000] < 0 < single.volts[i + 2000]  # 40 µs either side of the edge


def test_individual_deviations_follow_the_seed_within_the_table(tmp_path, example):
    def drawn(seed: int) -> dict:
        s = open_bench(_bench(tmp_path, example, deviation="from_specs", seed=seed))
        try:
            return {k: v for k, v in s.deviations().items() if k.startswith("scope/")}
        finally:
            s.close()

    first, again, other = drawn(3), drawn(3), drawn(4)
    assert first == again and first != other
    table = {r["v_div"]: r for r in SCOPE.example()["vertical"]["table"]}
    for ch in range(1, 5):
        for v_div, ppm in first[f"scope/ch{ch}.front.gain_error_ppm"].items():
            assert abs(ppm) <= table[float(v_div)]["gain_error_pct"] * 1e4
        for v_div, volts in first[f"scope/ch{ch}.front.offset_error"].items():
            assert abs(volts) <= table[float(v_div)]["offset_error_v"]
        assert abs(first[f"scope/ch{ch}.front.dac_error_ppm"]) <= 1e4
    assert abs(first["scope/timebase.clock_ppm"]) <= 25.0


# ---- wrong inputs point at the input field ----------------------------------------------------------


def _errors(inputs: dict) -> list[tuple[str, str]]:
    out = SCOPE.expand(inputs, "bad")
    assert out.definition is None
    return [(d.code, d.pointer) for d in out.diagnostics]


def _with(change) -> dict:
    inputs = copy.deepcopy(SCOPE.example())
    change(inputs)
    return inputs


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        pytest.param(
            lambda x: x["vertical"]["table"][1].update(v_div=0.001),
            ("E-TPL-ORDER", "/vertical/table/1/v_div"),
            id="scale-table-not-increasing",
        ),
        pytest.param(
            lambda x: x["vertical"]["table"][2].pop("offset_v"),
            ("E-TPL-MISSING-ROW", "/vertical/table/2/offset_v"),
            id="offset-range-missing-a-row",
        ),
        pytest.param(
            lambda x: x["vertical"]["table"].pop(),
            ("E-TPL-COVERAGE", "/vertical/table"),
            id="offset-ranges-stop-short-of-the-scale-range",
        ),
        pytest.param(
            lambda x: x["adc"].update(rate_pairs=[[1, 2], [3, 5]]),
            ("E-TPL-CHANNEL", "/adc/rate_pairs/1/1"),
            id="interleave-names-a-missing-channel",
        ),
        pytest.param(
            lambda x: x["adc"].update(rate_pairs=[[1, 2], [2, 3]]),
            ("E-TPL-DUPLICATE", "/adc/rate_pairs/1/0"),
            id="channel-in-two-interleave-pairs",
        ),
        pytest.param(
            lambda x: x["timebase"].update(default_points=1234),
            ("E-TPL-DEFAULT", "/timebase/default_points"),
            id="power-on-points-not-listed",
        ),
        pytest.param(
            lambda x: x.update(measurements=["VPP", "RISetime"]),
            ("E-TPL-UNKNOWN", "/measurements/1"),
            id="unknown-measurement",
        ),
        pytest.param(
            lambda x: x["adc"].update(bits=20),
            ("E-TPL-INPUT", "/adc/bits"),
            id="schema-range",
        ),
        pytest.param(
            lambda x: x["identity"].pop("manufacturer"),
            ("E-TPL-INPUT-MISSING", "/identity/manufacturer"),
            id="schema-missing",
        ),
        pytest.param(
            lambda x: x["channels"].update(bandwidth="fast"),
            ("E-TPL-INPUT", "/channels/bandwidth"),
            id="schema-unknown-field",
        ),
    ],
)
def test_a_wrong_input_is_named_by_its_pointer(change, expected):
    code, path = expected
    assert (code, INPUTS + path) in _errors(_with(change))


def test_a_definition_expanded_by_hand_runs_like_any_other():
    definition = InstrumentDefinition.model_validate(_expand(SCOPE.example()))
    bench = BenchDefinition.model_validate({"name": "t", "instruments": {"scope": {"model": definition.id}}})
    s = Session(bench, lambda _id: definition, clock="stepped", seed=1)
    try:
        s.write("scope", b"*IDN?\n")
        assert s.read("scope") == b"ACME,DSO-204,AC2040001,2.1.0\n"
        s.write("scope", b":CHANnel1:OFFSet 10\n")
        s.write("scope", b":CHANnel1:OFFSet?\n")
        # out_of_range: clamp — 1 V/div (displayed, probe 1X) allows ±50 V.
        assert float(s.read("scope")) == pytest.approx(10.0)
        s.write("scope", b":CHANnel1:SCALe 0.1\n")
        s.write("scope", b":CHANnel1:OFFSet?\n")
        assert float(s.read("scope")) == pytest.approx(2.0)
    finally:
        s.close()
