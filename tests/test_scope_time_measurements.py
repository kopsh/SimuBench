"""The DSO-X's time measurements and trigger-level commands (#350), against the RC bench's closed form.

The example bench drives a 1 kHz sine through 1 kΩ / 100 nF into CH1 (200 µs/div: the record runs
from −1 ms to +1 ms around the trigger, a rising zero crossing). Here CH2 also takes the generator
directly, so the RC's phase lag is what DELay?/PHASe? measure. Times are from the trigger; an
occurrence counts from the left of the record.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
F = 1000.0
T = 1 / F


@pytest.fixture
def scope():
    data = yaml.safe_load(
        (ROOT / "examples" / "benches" / "keysight-dsox2024a.bench.yaml").read_text(encoding="utf-8")
    )
    data["wires"].append({"from": "awg.CH1", "to": "scope.CH2"})
    data["instruments"]["scope"]["state"].update({"ch2.display": True, "ch2.scale": 0.5, "ch2.probe": 1.0})
    s = open_bench(BenchDefinition.model_validate(data), idle="off")
    yield s
    s.close()


def q(s, command: str) -> float | str | None:
    s.write("scope", (command + "\n").encode())
    out = s.read("scope")
    if out is None:
        return None
    text = out.decode().strip()
    try:
        return float(text)
    except ValueError:
        return text


def test_extremes_bit_rate_and_voltage_at_a_time(scope):
    q(scope, ":DIGitize CHANnel1,CHANnel2")
    amp = q(scope, ":MEASure:VMAX? CHANnel1")
    # The sine peaks a quarter period after the rising zero crossing (−0.75 ms is the first on the
    # record) and is lowest a quarter before it; the 8-bit flat top moves the first sample a little.
    assert q(scope, ":MEASure:XMAX? CHANnel1") == pytest.approx(-0.75e-3, abs=0.05 * T)
    assert q(scope, ":MEASure:XMIN? CHANnel1") == pytest.approx(-0.25e-3, abs=0.05 * T)
    # Both half periods are 0.5 ms: 2 kbit/s.
    assert q(scope, ":MEASure:BRATe? CHANnel1") == pytest.approx(2.0 * F, rel=1e-3)
    for t in (1e-4, -3e-4):
        expected = amp * math.sin(2 * math.pi * F * t)
        assert q(scope, f":MEASure:VTIMe? {t},CHANnel1") == pytest.approx(expected, abs=0.02)
    assert q(scope, ":MEASure:VTIMe? 2E-3,CHANnel1") > 9e37  # outside the record


def test_edges_and_crossings_counted_from_the_left(scope):
    q(scope, ":DIGitize CHANnel1")
    amp = q(scope, ":MEASure:VMAX? CHANnel1")
    assert q(scope, ":MEASure:TEDGe? +1,CHANnel1") == pytest.approx(0.0, abs=0.01 * T)  # the trigger
    assert q(scope, ":MEASure:TEDGe? -1,CHANnel1") == pytest.approx(-0.5e-3, abs=0.01 * T)
    assert q(scope, ":MEASure:TEDGe? +2,CHANnel1") > 9e37  # no second rising edge on the record
    expected = -T + math.asin(0.5 / amp) / (2 * math.pi * F)
    assert q(scope, ":MEASure:TVALue? 0.5,+1,CHANnel1") == pytest.approx(expected, abs=0.01 * T)
    falling = -T / 2 - math.asin(0.5 / amp) / (2 * math.pi * F)
    assert q(scope, ":MEASure:TVALue? 0.5,-1,CHANnel1") == pytest.approx(falling, abs=0.01 * T)


def test_delay_and_phase_between_two_sources_are_the_rc_lag(scope):
    # CH2 sees the generator's terminal (after its 50 Ω), CH1 the RC's output: between the two the
    # lag is the 1 kΩ / 100 nF divider's alone, atan(ω R C) (the 1 MΩ inputs are negligible).
    lag = math.atan(2 * math.pi * F * 1000.0 * 1e-7)
    q(scope, ":DIGitize CHANnel1,CHANnel2")
    delay = q(scope, ":MEASure:DELay? CHANnel2,CHANnel1")
    assert delay == pytest.approx(lag / (2 * math.pi * F), rel=0.01)
    assert q(scope, ":MEASure:PHASe? CHANnel2,CHANnel1") == pytest.approx(math.degrees(lag), rel=0.01)
    # Without sources: :MEASure:SOURce's two.
    q(scope, ":MEASure:SOURce CHANnel2,CHANnel1")
    assert q(scope, ":MEASure:DELay?") == pytest.approx(delay)


def test_trigger_high_low_are_kept_per_source_and_asetup_takes_the_middle(scope):
    q(scope, ":TRIGger:LEVel:HIGH 1.5,CHANnel2")
    q(scope, ":TRIGger:LEVel:LOW -0.25,CHANnel2")
    assert q(scope, ":TRIGger:LEVel:HIGH? CHANnel2") == 1.5
    assert q(scope, ":TRIGger:LEVel:LOW? CHANnel2") == -0.25
    assert q(scope, ":TRIGger:LEVel:HIGH? CHANnel1") == 0.0
    # ASETup: each displayed channel's edge level to the middle of its waveform — the generator's
    # sine with a 0.4 V offset reads 0.4 V on CH2; CH1 behind the RC's AC path stays near 0.4 V
    # too (DC passes the RC).
    scope.write("awg", b"C1:BSWV OFST,0.4\n")
    q(scope, ":TRIGger:LEVel 0.9,CHANnel1")
    q(scope, ":TRIGger:LEVel:ASETup")
    assert q(scope, ":TRIGger:LEVel? CHANnel2") == pytest.approx(0.4, abs=0.03)
    assert q(scope, ":TRIGger:LEVel? CHANnel1") == pytest.approx(0.4, abs=0.03)
    q(scope, ":CHANnel2:COUPling AC")
    q(scope, ":TRIGger:LEVel:ASETup")
    assert q(scope, ":TRIGger:LEVel? CHANnel2") == 0.0  # AC coupling: 0 V
    assert q(scope, ":SYSTem:ERRor?") == '+0,"No error"'


# ---- the screen's measurement list (#373) ---------------------------------------------------------


def _results(scope) -> list[list[str]]:
    scope.write("scope", b":MEASure:RESults?\n")
    text = scope.read("scope").decode().strip()
    fields = text.split(",") if text else []
    return [fields[i : i + 7] for i in range(0, len(fields), 7)]


def test_a_measurement_without_a_question_mark_goes_on_the_list_of_four(scope):
    q(scope, ":DIGitize CHANnel1,CHANnel2")
    assert _results(scope) == []
    for item in ("FREQuency CHANnel1", "VPP CHANnel1", "VMAX CHANnel2", "VMIN CHANnel1"):
        assert q(scope, f":MEASure:{item}") is None
    assert [r[0] for r in _results(scope)] == ["FREQuency(1)", "VPP(1)", "VMAX(2)", "VMIN(1)"]
    q(scope, ":MEASure:VRMS CHANnel1")  # a fifth: the oldest leaves
    assert [r[0] for r in _results(scope)] == ["VPP(1)", "VMAX(2)", "VMIN(1)", "VRMS(1)"]
    q(scope, ":MEASure:VMAX CHANnel2")  # installed again: it moves to the end
    assert [r[0] for r in _results(scope)] == ["VPP(1)", "VMIN(1)", "VRMS(1)", "VMAX(2)"]


def test_results_carry_the_statistics_and_clear_empties_the_list(scope):
    q(scope, ":DIGitize CHANnel1")
    q(scope, ":MEASure:FREQuency CHANnel1")
    _results(scope)
    [[label, current, low, high, mean, sdev, count]] = _results(scope)
    assert label == "FREQuency(1)" and int(count) == 2  # each read takes one more value
    assert float(current) == float(low) == float(high) == float(mean) == pytest.approx(F, rel=1e-3)
    assert float(sdev) == 0.0  # the same record twice
    assert float(q(scope, ":MEASure:FREQuency? CHANnel1")) == pytest.approx(F, rel=1e-3)
    q(scope, ":MEASure:CLEar")
    assert _results(scope) == []


def test_all_is_a_snapshot_of_every_single_source_measurement(scope):
    q(scope, ":DIGitize CHANnel1")
    scope.write("scope", b":MEASure:ALL? CHANnel1\n")
    values = [float(v) for v in scope.read("scope").decode().strip().split(",")]
    assert len(values) == 19
    assert values[0] == q(scope, ":MEASure:VPP? CHANnel1")
    assert values[11] == pytest.approx(F, rel=1e-3)  # FREQuency
    assert values[18] == pytest.approx(2 * F, rel=1e-3)  # BRATe
    assert _results(scope) == []  # a snapshot installs nothing


def test_show_and_window_are_kept(scope):
    assert q(scope, ":MEASure:SHOW?") == 1.0
    q(scope, ":MEASure:SHOW OFF")
    assert q(scope, ":MEASure:SHOW?") == 0.0
    q(scope, ":MEASure:WINDow ZOOM")
    assert q(scope, ":MEASure:WINDow?") == "ZOOM"
