"""Oscilloscope scenario (docs/validation-scope.md), with expectations recomputed by circuit theory (D13)."""

from __future__ import annotations

import hashlib
import math
import subprocess
import sys

import numpy as np
import pytest
from refcalc.circuits import emf_for_load_setting, fit_sine, rc_chain, rc_ladder

from .conftest import BENCHES, capture, open_rig

R, C = 1000.0, 100e-9
FC = 1 / (2 * math.pi * R * C)
PROBE_LOAD = 10e6  # 9 Mohm series + 1 Mohm scope input


def expected_tip_peak(f: float, vpp_set: float = 2.0, load_setting: float = 50.0) -> float:
    emf = emf_for_load_setting(vpp_set / 2, 50.0, load_setting)
    _, v_out = rc_chain(f, emf, rs=50.0, r=R, c=C, load_out=PROBE_LOAD)
    return abs(v_out)


def test_identity_and_addresses():
    import pyvisa

    rm = pyvisa.ResourceManager(f"{BENCHES / 'awg_rc_scope.bench.yaml'}@simubench")
    assert set(rm.list_resources("?*")) >= {
        "TCPIP0::127.0.0.1::5025::SOCKET",
        "TCPIP0::127.0.0.1::5026::SOCKET",
        "TCPIP0::192.168.1.20::5025::SOCKET",
    }
    by_real = rm.open_resource(
        "TCPIP0::192.168.1.21::5025::SOCKET", read_termination="\n", write_termination="\n"
    )
    assert by_real.query("*IDN?").startswith("SIMUBENCH,SB-DSO-4,")
    rm.close()


def test_preamble_fields(rig):
    awg, scope = rig
    awg.write(":OUTPut1:STATe ON")
    scope.write(":CHANnel1:SCALe 0.05")
    cap = capture(scope)
    fmt, typ, points, count, x_inc, x_org, x_ref, y_inc, y_org, y_ref = cap.preamble
    assert (fmt, typ, points, count) == (1, 0, 1000, 1)
    assert x_inc == pytest.approx(1e-5) and x_org == pytest.approx(-5e-3) and x_ref == 0
    assert y_inc == pytest.approx(4 * 0.05 / 32768, rel=1e-6) and y_org == 0 and y_ref == 0


def test_amplitude_follows_circuit_theory(rig):
    awg, scope = rig
    awg.write(":OUTPut1:STATe ON")  # 2 Vpp into a 50 ohm load setting, 1 kHz
    scope.write(":CHANnel1:SCALe 0.1")
    cap = capture(scope, probe=10.0)
    peak, _, _ = fit_sine(cap.t, cap.v, 1000.0)
    assert peak == pytest.approx(expected_tip_peak(1000.0), rel=1e-3)
    # The classic bench bug, reproduced: a 50 ohm setting into a high-impedance load reads about twice.
    assert peak * 2 > 1.9 * 2 * 0.8467


def test_high_z_load_setting_halves_the_emf(rig):
    awg, scope = rig
    awg.write(":OUTPut1:LOAD INF;:OUTPut1:STATe ON")
    scope.write(":CHANnel1:SCALe 0.05")
    cap = capture(scope, probe=10.0)
    peak, _, _ = fit_sine(cap.t, cap.v, 1000.0)
    assert peak == pytest.approx(expected_tip_peak(1000.0, load_setting=math.inf), rel=1e-3)


def test_binary_block_cut_by_termination_character(rig):
    awg, scope = rig
    awg.write(":OUTPut1:STATe ON")
    for freq in (100, 137, 211, 333, 512, 777, 1000, 1499):
        awg.write(f":SOURce1:FREQuency {freq}")
        scope.write(":CHANnel1:SCALe 0.05;:TIMebase:SCALe 0.001;:DIGitize")
        scope.read_termination = None
        scope.write(":WAVeform:DATA?")
        full = scope.read_raw()
        scope.read_termination = "\n"
        payload = full[6:-1]
        if 0x0A in payload:
            break
    else:
        pytest.skip("no capture with a 0x0A byte found")
    assert len(full) == 2007 and full[:6] == b"#42000"
    scope.write(":WAVeform:DATA?")
    cut = scope.read_raw()  # read_termination="\n" stops at the first 0x0A, with no instrument error
    assert len(cut) < 2007
    scope.clear()
    assert scope.query(":SYSTem:ERRor?").startswith("0,")


@pytest.mark.parametrize(("slope", "level"), [("POSitive", 0.0), ("NEGative", 0.0), ("POSitive", 0.05)])
def test_trigger_point(rig, slope, level):
    awg, scope = rig
    awg.write(":OUTPut1:STATe ON")
    scope.write(f":CHANnel1:SCALe 0.05;:TRIGger:EDGE:SLOPe {slope};:TRIGger:EDGE:LEVel {level}")
    cap = capture(scope)
    assert cap.t[500] == pytest.approx(0.0, abs=1e-12)
    if slope == "POSitive":
        assert cap.v[499] < level <= cap.v[500] + 1e-4
    else:
        assert cap.v[499] > level >= cap.v[500] - 1e-4


def test_unreachable_trigger_level_auto_triggers(rig):
    awg, scope = rig
    awg.write(":OUTPut1:STATe ON")
    scope.write(":CHANnel1:SCALe 0.05;:TRIGger:EDGE:LEVel 5")
    cap = capture(scope)
    assert len(cap.v) == 1000


def test_corner_sweep(rig):
    awg, scope = rig
    awg.write(":OUTPut1:STATe ON")
    scope.write(":CHANnel1:SCALe 0.1")
    for f in (100, 500, 1000, FC, 2500, 10000, 20000):
        awg.write(f":SOURce1:FREQuency {f}")
        scope.write(f":TIMebase:SCALe {2 / f}")
        cap = capture(scope, probe=10.0)
        peak, _, _ = fit_sine(cap.t, cap.v, f)
        assert peak == pytest.approx(expected_tip_peak(f), rel=2e-3), f


def test_ac_coupling_is_an_analog_high_pass(rig):
    awg, scope = rig
    awg.write(":SOURce1:VOLTage:OFFSet 1.0;:OUTPut1:STATe ON")
    scope.write(":CHANnel1:SCALe 0.2")
    for f in (1000.0, 10.0):
        awg.write(f":SOURce1:FREQuency {f}")
        scope.write(f":TIMebase:SCALe {2 / f}")
        dc_peak, _, dc_level = fit_sine(*_cap(scope, f, "DC"), f)
        ac_peak, _, ac_level = fit_sine(*_cap(scope, f, "AC"), f)
        assert dc_level > 0.09  # 1 V offset at the tip is about 0.1 V at the scope input
        assert abs(ac_level) < 1e-3
        h = abs(1j * f / 10.0 / (1 + 1j * f / 10.0))
        assert ac_peak / dc_peak == pytest.approx(h, rel=2e-3)


def _cap(scope, f, coupling):
    scope.write(f":CHANnel1:COUPling {coupling}")
    cap = capture(scope)
    return cap.t, cap.v


def test_probe_setting_scales_the_preamble(rig):
    awg, scope = rig
    awg.write(":OUTPut1:STATe ON")
    scope.write(":CHANnel1:SCALe 0.1")
    at_input = capture(scope)
    scope.write(":CHANnel1:PROBe 10;:CHANnel1:SCALe 1")
    at_tip = capture(scope)
    p_in, _, _ = fit_sine(at_input.t, at_input.v, 1000.0)
    p_tip, _, _ = fit_sine(at_tip.t, at_tip.v, 1000.0)
    assert p_tip == pytest.approx(10 * p_in, rel=2e-3)


def test_buffered_two_stage_corner():
    rm, awg, scope = open_rig("rc_rc_buffer.bench.yaml")
    awg.write(":OUTPut1:LOAD INF;:OUTPut1:STATe ON;:SOURce1:FREQuency 10")
    scope.write(":CHANnel1:SCALe 0.05;:TIMebase:SCALe 0.2")
    low = fit_sine(*_tv(capture(scope, probe=10.0)), 10.0)[0]
    awg.write(f":SOURce1:FREQuency {FC}")
    scope.write(f":TIMebase:SCALe {2 / FC}")
    at_fc = fit_sine(*_tv(capture(scope, probe=10.0)), FC)[0]
    # Each buffered stage is a one-pole RC; at fc the pair gives 0.5 (stage loading is small here).
    v1_low = abs(rc_chain(10.0, 1.0, rs=50, r=R, c=C, load_out=1e6)[1])
    v1_fc = abs(rc_chain(FC, 1.0, rs=50, r=R, c=C, load_out=1e6)[1])
    v2_low = abs(rc_chain(10.0, 1.0, rs=50, r=R, c=C, load_out=PROBE_LOAD)[1])
    v2_fc = abs(rc_chain(FC, 1.0, rs=50, r=R, c=C, load_out=PROBE_LOAD)[1])
    assert at_fc / low == pytest.approx((v1_fc * v2_fc) / (v1_low * v2_low), rel=2e-3)
    rm.close()


def test_direct_cascade_is_a_loaded_ladder():
    """validation-scope.md 7: the reference rejects a direct RC-RC cascade; physically it is a valid
    circuit whose second stage loads the first (D13), so SimuBench accepts it and solves it."""
    rm, awg, scope = open_rig("rc_rc_direct.bench.yaml")
    awg.write(":OUTPut1:LOAD INF;:OUTPut1:STATe ON;:SOURce1:FREQuency 10")
    scope.write(":CHANnel1:SCALe 0.05;:TIMebase:SCALe 0.2")
    low = fit_sine(*_tv(capture(scope, probe=10.0)), 10.0)[0]
    awg.write(f":SOURce1:FREQuency {FC}")
    scope.write(f":TIMebase:SCALe {2 / FC}")
    at_fc = fit_sine(*_tv(capture(scope, probe=10.0)), FC)[0]
    ref_low = abs(rc_ladder(10.0, 1.0, r=R, c=C, load_out=PROBE_LOAD))
    ref_fc = abs(rc_ladder(FC, 1.0, r=R, c=C, load_out=PROBE_LOAD))
    assert at_fc / low == pytest.approx(ref_fc / ref_low, rel=2e-3)
    # Loading makes it clearly lower than two ideal one-pole stages (0.5).
    assert at_fc / low < 0.45
    rm.close()


def test_acceptance_benches_validate_clean():
    """validation-scope.md 1: validation passes with exit code 0."""
    from simubench.cli import main

    for bench in sorted(BENCHES.glob("*.bench.yaml")):
        assert main(["validate", str(bench)]) == 0, bench.name


def _tv(cap):
    return cap.t, cap.v


DETERMINISM_SCRIPT = """
import hashlib, sys, pyvisa
rm = pyvisa.ResourceManager(sys.argv[1] + "@simubench")
awg = rm.open_resource("awg", write_termination="\\n")
scope = rm.open_resource("scope", write_termination="\\n")
awg.write(":OUTPut1:STATe ON")
scope.write(":CHANnel1:SCALe 0.05;:DIGitize")
scope.write(":WAVeform:DATA?")
print(hashlib.sha256(scope.read_raw()).hexdigest())
"""


def test_capture_is_deterministic_across_processes():
    bench = str(BENCHES / "awg_rc_scope.bench.yaml")
    digests = {
        subprocess.run(
            [sys.executable, "-c", DETERMINISM_SCRIPT, bench], capture_output=True, text=True, check=True
        ).stdout.strip()
        for _ in range(2)
    }
    assert len(digests) == 1


def test_same_instant_same_bytes_later_instant_different():
    from simubench.api import open_bench

    s = open_bench(BENCHES / "awg_rc_scope.bench.yaml")
    s.scpi("awg", ":OUTPut1:STATe ON")
    s.scpi("scope", ":CHANnel1:SCALe 0.05;:DIGitize")
    a = s.query("scope", ":WAVeform:DATA?")
    b = s.query("scope", ":WAVeform:DATA?")
    assert hashlib.sha256(a).digest() == hashlib.sha256(b).digest()
    assert np.frombuffer(a[6:-1], "<i2").size == 1000
    # A later acquisition of a periodic signal re-triggers on the same edge: the record is stable.
    s.advance(0.00025)
    s.scpi("scope", ":DIGitize")
    assert s.query("scope", ":WAVeform:DATA?") == a
    # Without a trigger (level out of reach) the record follows the instant of acquisition.
    s.scpi("scope", ":TRIGger:EDGE:LEVel 5;:DIGitize")
    d = s.query("scope", ":WAVeform:DATA?")
    s.advance(0.00025)
    s.scpi("scope", ":DIGitize")
    assert s.query("scope", ":WAVeform:DATA?") != d
