"""#1304: the maximum input voltage of the DSO-X 2024A, the SDS2104X Plus and the WavePro 804HD
(each data sheet's, in ``specs.max_input``): beyond it an acquisition warns of possible damage, as
on the UXRs (#1264). None of the three data sheets gives a maximum input range, so none sets an
overload bit."""

from __future__ import annotations

from pathlib import Path

import pytest
import pyvisa
import yaml

ROOT = Path(__file__).resolve().parents[1]


def _bench(tmp_path: Path, model: str, amplitude: float = 2.0, hidden: dict | None = None) -> Path:
    """The model's cases bench (a 1 kHz sine on CH1, open-circuit amplitude ``amplitude``)."""
    data = yaml.safe_load((ROOT / "tests" / "models" / model / "bench.yaml").read_text("utf-8"))
    data["instruments"]["awg"]["state"]["ch1.amplitude"] = amplitude
    if hidden:
        data["instruments"]["scope"]["hidden"] = hidden
    bench = tmp_path / f"{model}.bench.yaml"
    bench.write_text(yaml.safe_dump(data), "utf-8")
    return bench


def _acquire(bench: Path, setup: list[str]) -> tuple[float, dict[str, str], list[str]]:
    """The front's absolute maximum after ``setup``, then one acquisition of CH1: the instrument's
    warnings and the overload bits it latched."""
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    try:
        scope = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=20000)
        for command in setup:
            scope.write(command)
        inst = rm.visalib.bench.instruments["scope"]
        acq = inst.components["acq"]
        acq.compute([1])  # one acquisition (not a screen frame), whether or not it would be pending
        limit = float(inst.components["ch1.front"].param("max_input_v"))
        return limit, dict(inst.warnings), sorted(str(n) for n in acq.overloaded)
    finally:
        rm.close()


@pytest.mark.parametrize(
    ("model", "setup", "limit"),
    [
        ("keysight-dsox2024a", [], 400.0),  # CAT I 300 Vrms, 400 Vpk
        ("siglent-sds2104xplus", [], 400.0),  # 1 MΩ: ≤ 400 Vpk
        ("siglent-sds2104xplus", [":CHANnel1:IMPedance FIFTy"], 7.07),  # 50 Ω: ≤ 5 Vrms, a sine's peak
        ("lecroy-wavepro804hd", [], 400.0),  # 1 MΩ: 400 V max
        ("lecroy-wavepro804hd", ["C1:CPL D50", "C1:VDIV 0.01"], 2.0),  # 50 Ω: ±2 V at ≤ 34.5 mV/div
        ("lecroy-wavepro804hd", ["C1:CPL D50", "C1:VDIV 0.05"], 5.0),  # ±5 V at 35-87 mV/div
        ("lecroy-wavepro804hd", ["C1:CPL D50", "C1:VDIV 0.1"], 7.78),  # 5.5 Vrms above, a sine's peak
    ],
)
def test_each_scope_takes_its_data_sheet_s_maximum_input(tmp_path, model, setup, limit):
    """The 2 Vpp sine is far within every limit: no warning, no overload bit."""
    got, warnings, overloaded = _acquire(_bench(tmp_path, model), setup)
    assert got == pytest.approx(limit)
    assert warnings == {} and overloaded == []


def test_beyond_the_wavepro_s_50_ohm_maximum_it_warns(tmp_path):
    """A 10 Vpp (open-circuit) sine from the 50 Ω generator is ±2.5 V into the WavePro's 50 Ω input:
    beyond the ±2 V at 10 mV/div, a possible-damage warning; within the ±5 V at 50 mV/div, none."""
    bench = _bench(tmp_path, "lecroy-wavepro804hd", amplitude=10.0)
    _, warnings, overloaded = _acquire(bench, ["C1:CPL D50", "C1:VDIV 0.01"])
    assert list(warnings) == ["CH1-OVERVOLTAGE"] and "±2 V" in warnings["CH1-OVERVOLTAGE"]
    assert overloaded == []
    assert _acquire(bench, ["C1:CPL D50", "C1:VDIV 0.05"])[1] == {}


@pytest.mark.parametrize("model", ["keysight-dsox2024a", "siglent-sds2104xplus"])
def test_beyond_the_maximum_input_it_warns(tmp_path, model):
    """No source of the benches reaches 400 V: with the limit lowered to 0.5 V (a hidden parameter)
    the 2 Vpp sine at the 1 MΩ input warns of possible damage, and sets no overload bit. The check
    looks at the record's span: 1 ms/div takes in the 1 kHz sine's peaks."""
    bench = _bench(tmp_path, model, hidden={"ch1.front": {"max_input_v": 0.5}})
    _, warnings, overloaded = _acquire(bench, [":TIMebase:SCALe 0.001"])
    assert list(warnings) == ["CH1-OVERVOLTAGE"] and "±0.5 V" in warnings["CH1-OVERVOLTAGE"]
    assert overloaded == []
