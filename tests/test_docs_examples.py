"""Documentation examples point at the components and fields the code actually has (#257)."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_metrology_authoring_uses_the_current_dmm_component_names():
    text = (ROOT / "docs" / "metrology-authoring.md").read_text(encoding="utf-8")
    # #190 split dmm_core into dmm_input_terminal / dmm_conditioning / dmm_adc and renamed the
    # error-carrying block meter -> path; the examples must be copyable.
    assert "meter.gain_error_ppm" not in text
    assert "param: meter.offset" not in text
    assert "hidden: {meter:" not in text
    assert "path.gain_error_ppm" in text and "hidden: {path:" in text
    # A bench instance names its model, not a "definition" field.
    assert "definition: keysight-34465a" not in text


# ---- instrument-authoring.md 2.11: the signal chain in a definition (#961) ----------------------------

AUTHORING = ROOT / "docs" / "instrument-authoring.md"
DIGITIZER = ROOT / "tests" / "definitions" / "doc-digitizer.yaml"
TONE_V = 10 ** (-20 / 20) * (2 * 50 * 1e-3) ** 0.5  # -20 dBm into 50 ohm, V peak


def _fenced(after: str, lang: str) -> str:
    """The first ``lang`` code block of the authoring guide after the text ``after``."""
    text = AUTHORING.read_text(encoding="utf-8")
    start = text.index(after)
    opening = f"```{lang}\n"
    body = text.index(opening, start) + len(opening)
    return text[body : text.index("```", body)]


def _digitizer_bench(model: str = "doc-digitizer"):
    from simubench.models.bench import BenchDefinition

    return BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "doc-digitizer",
            "instruments": {
                "sg": {
                    "model": "sb-sg-1",
                    "state": {"freq.cw": 10.0e6, "power.level": -20.0, "output.enabled": True},
                },
                "dig": {"model": model},
            },
            "wires": [{"from": "sg.RF_OUT", "to": "dig.IN"}],
        }
    )


def _ddc_amplitude(s) -> float:
    """The mean complex amplitude on ``ddc.out``, read the way an internal probe reads it."""
    from simubench.server.services.internal_probe import stream_request

    ddc = s.instruments["dig"].components["ddc"]
    frame = s.cached_frames(ddc, "out", stream_request(s, ddc, "out"))
    return float(abs(frame.data).mean())


def test_the_signal_chain_example_is_the_file_it_names_and_runs(tmp_path):
    """The guide's minimal digitizer: the YAML in the guide is the test definition, it validates,
    the tone comes out of the digital down-converter at the amplified level, and each wiring the
    guide's table rejects is rejected with its code."""
    import copy

    import yaml

    from simubench.api import open_bench
    from simubench.engine.validate_definition import validate_definition_data

    # the guide's block is the file
    doc = _fenced("**最小的完整例子**", "yaml")
    assert doc == DIGITIZER.read_text(encoding="utf-8")
    data = yaml.safe_load(doc)
    _, report = validate_definition_data(data)
    assert [d.code for d in report.diagnostics] == []

    # it runs: 10 MHz, -20 dBm, +20 dB, the NCO on the tone -> a complex amplitude of 0.316 V
    s = open_bench(
        _digitizer_bench(), definition_dirs=[DIGITIZER.parent, ROOT / "validation" / "instruments"]
    )
    try:
        assert _ddc_amplitude(s) == pytest.approx(TONE_V * 10, rel=1e-3)
        s.write("dig", b":DIG:GAIN 0\n")
        assert _ddc_amplitude(s) == pytest.approx(TONE_V, rel=1e-3)
    finally:
        s.close()

    # the rejections of the guide's table, one each
    def codes(change) -> list[tuple[str, str]]:
        d = copy.deepcopy(data)
        change(d)
        _, rep = validate_definition_data(d)
        return [(x.code, x.pointer) for x in rep.diagnostics]

    def samples_into_signal(d):
        d["wiring"]["instrument"].append(["ddc.out", "amp.in"])

    def signal_into_samples(d):
        d["wiring"]["instrument"][2] = ["amp.out", "ddc.in"]

    def samples_port_out(d):
        d["ports"]["instrument"]["IQ"] = "ddc.out"

    def samples_open(d):
        del d["wiring"]["instrument"][2]

    assert ("E-DEF-WIRE-MEDIUM", "/wiring/instrument/3") in codes(samples_into_signal)
    assert ("E-DEF-WIRE-MEDIUM", "/wiring/instrument/2") in codes(signal_into_samples)
    assert ("E-DEF-WIRE-MEDIUM", "/ports/instrument/IQ") in codes(samples_port_out)
    assert "E-DEF-SAMPLES-OPEN" in [code for code, _ in codes(samples_open)]


def test_the_plugin_frames_example_runs_between_the_adc_and_the_ddc(tmp_path):
    """The guide's ``sample_scale`` plugin, executed as written: wired between ``adc`` and ``ddc``
    with ``factor: 2``, it doubles what the down-converter puts out."""
    import yaml

    from simubench.api import open_bench
    from simubench.engine.component import registry

    exec(compile(_fenced("**插件组件的 `frames`**", "python"), str(AUTHORING), "exec"), {})
    try:
        data = yaml.safe_load(DIGITIZER.read_text(encoding="utf-8"))
        data["id"] = "doc-digitizer-scaled"
        data["components"].insert(3, {"name": "scale", "type": "sample_scale", "params": {"factor": 2.0}})
        data["wiring"]["instrument"][2:] = [["adc.out", "scale.in"], ["scale.out", "ddc.in"]]
        (tmp_path / "doc-digitizer-scaled.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
        s = open_bench(
            _digitizer_bench("doc-digitizer-scaled"),
            definition_dirs=[tmp_path, ROOT / "validation" / "instruments"],
        )
        try:
            assert _ddc_amplitude(s) == pytest.approx(2 * TONE_V * 10, rel=1e-3)
        finally:
            s.close()
    finally:
        registry.unregister("sample_scale")
