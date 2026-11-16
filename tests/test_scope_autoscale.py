"""The oscilloscope's autoscale (scope_autoscale): the 1-2-5 rounding, and a scope with nothing on
its inputs keeping every setting (the guide: "returned to its former state")."""

from __future__ import annotations

import pytest
import pyvisa

from simubench.library.components.scope_autoscale import nice_ceil


@pytest.mark.parametrize(
    ("x", "expected"),
    [(0.073, 0.1), (0.1, 0.1), (0.101, 0.2), (0.15, 0.2), (0.3, 0.5), (4.0e-8, 5.0e-8), (6.0, 10.0)],
)
def test_nice_ceil_is_the_next_value_of_the_1_2_5_sequence(x: float, expected: float) -> None:
    assert nice_ceil(x) == pytest.approx(expected)


def test_autoscale_with_no_waveform_keeps_every_setting(tmp_path) -> None:
    bench = tmp_path / "open.bench.yaml"
    bench.write_text(
        "schema: simubench.bench/2\nname: open_inputs\n"
        "instruments:\n  scope:\n    model: keysight-uxr1104b\n"
        'run: {seed: 1, clock: command, idle: "off"}\n',
        encoding="utf-8",
    )
    scope = pyvisa.ResourceManager(f"{bench}@simubench").open_resource("scope")
    scope.write(":CHANnel1:SCALe 0.2;:CHANnel1:OFFSet 0.1;:TIMebase:SCALe 1E-6;:TRIGger:MODE GLITch")
    scope.write(":AUToscale:CHANnels DISPlayed;:AUToscale")
    assert scope.query(":SYSTem:ERRor?").strip() == "0"
    assert float(scope.query(":CHANnel1:SCALe?")) == pytest.approx(0.2)
    assert float(scope.query(":CHANnel1:OFFSet?")) == pytest.approx(0.1)
    assert float(scope.query(":TIMebase:SCALe?")) == pytest.approx(1e-6)
    assert scope.query(":TRIGger:MODE?").strip() == "GLIT"
    assert scope.query(":CHANnel1:DISPlay?").strip() == "1"
