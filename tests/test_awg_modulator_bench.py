"""The modulation block on a validation bench (#327): a scope reads what the closed form says."""

from __future__ import annotations

import pytest


def test_a_validation_bench_scope_reads_the_am_envelope():
    """sb-awg-2 → sb-dso-4: 2 Vpp at 10 kHz, AM 50 % at 1 kHz: the scope's peak-to-peak over a
    whole modulation period is (1 + m) × 2 V = 3 V, and its frequency count stays the carrier's."""
    from pathlib import Path

    from simubench.api import open_bench
    from simubench.models.bench import BenchDefinition

    bench = BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "am",
            "instruments": {
                "awg": {
                    "model": "sb-awg-2",
                    "state": {
                        "ch1.frequency": 10000.0,
                        "ch1.amplitude": 2.0,
                        "ch1.load": float("inf"),
                        "ch1.output": True,
                    },
                },
                "scope": {"model": "sb-dso-4", "state": {"ch1.probe": 1.0, "ch1.scale": 0.5}},
            },
            "wires": [{"from": "awg.OUT1", "to": "scope.CH1"}],
            "run": {"clock": "command", "idle": "off", "seed": 1},
        }
    )
    root = Path(__file__).resolve().parents[1]
    s = open_bench(bench, definition_dirs=[root / "validation" / "instruments"])

    def q(inst: str, cmd: str) -> str:
        s.write(inst, (cmd + "\n").encode())
        out = s.read(inst)
        return out.decode().strip() if out else ""

    try:
        q("awg", ":SOUR1:MOD:TYPE AM;:SOUR1:AM:DEPT 50;:SOUR1:MOD:FREQ 1000;:SOUR1:MOD:STAT ON")
        q("scope", ":TIMebase:SCALe 2E-4;:DIGitize CHANnel1")  # 2 ms: two modulation periods
        assert float(q("scope", ":MEASure:VPP? CHANnel1")) == pytest.approx(3.0, rel=0.02)
        assert float(q("scope", ":MEASure:FREQuency? CHANnel1")) == pytest.approx(10000.0, rel=0.01)
        q("awg", ":SOUR1:MOD:STAT OFF")
        q("scope", ":DIGitize CHANnel1")
        assert float(q("scope", ":MEASure:VPP? CHANnel1")) == pytest.approx(2.0, rel=0.02)
    finally:
        s.close()


def test_pwm_on_a_1khz_square_reads_on_the_scope_without_an_error():
    """Issue #388: sb-awg-2 (300 MHz bandwidth) with PWM on a 1 kHz square: the expansion keeps
    the harmonics that fit the line budget instead of refusing with -222."""
    from pathlib import Path

    from simubench.api import open_bench
    from simubench.models.bench import BenchDefinition

    bench = BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "pwm",
            "instruments": {
                "awg": {
                    "model": "sb-awg-2",
                    "state": {
                        "ch1.function": "SQUare",
                        "ch1.frequency": 1000.0,
                        "ch1.amplitude": 2.0,
                        "ch1.load": float("inf"),
                        "ch1.output": True,
                    },
                },
                "scope": {"model": "sb-dso-4", "state": {"ch1.probe": 1.0, "ch1.scale": 0.5}},
            },
            "wires": [{"from": "awg.OUT1", "to": "scope.CH1"}],
            "run": {"clock": "command", "idle": "off", "seed": 1},
        }
    )
    root = Path(__file__).resolve().parents[1]
    s = open_bench(bench, definition_dirs=[root / "validation" / "instruments"])

    def q(inst: str, cmd: str) -> str:
        s.write(inst, (cmd + "\n").encode())
        out = s.read(inst)
        return out.decode().strip() if out else ""

    try:
        q("awg", ":SOUR1:MOD:TYPE PWM;:SOUR1:MOD:FREQ 100;:SOUR1:MOD:STAT ON")
        assert q("awg", ":SYST:ERR?").startswith("0,")
        q("scope", ":TIMebase:SCALe 1E-3;:DIGitize CHANnel1")
        assert q("awg", ":SYST:ERR?").startswith("0,")
        # 2 Vpp pulse; the line expansion rings at the edges (Gibbs plus Carson's cut, ~30 %
        # even within the budget), so the peak-to-peak only brackets the level.
        assert 1.9 < float(q("scope", ":MEASure:VPP? CHANnel1")) < 3.0
        assert float(q("scope", ":MEASure:FREQuency? CHANnel1")) == pytest.approx(1000.0, rel=0.02)
    finally:
        s.close()
