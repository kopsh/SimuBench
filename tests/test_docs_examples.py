"""Documentation examples point at the components and fields the code actually has (#257)."""

from pathlib import Path

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
