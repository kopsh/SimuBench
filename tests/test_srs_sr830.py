"""SRS SR830 (issue 195): the real-instrument-style RC sweep script against the engine.

The manual cases (tests/models/srs-sr830/) check command by command; these tests run
examples/scripts/srs_sr830_rc_sweep.py on the example bench, check the readings against the
circuit's closed form (50 Ohm source, 1 kOhm / 100 nF, the 10 MOhm / 25 pF SIG_IN terminal and the
0.16 Hz AC coupling), and check that a real SR830 resource string — the default GPIB address 8 —
opens the simulated instrument. The SR830 has no LAN: a bench declares the GPIB (or RS-232) address
and the in-process PyVISA backend maps it.
"""

from __future__ import annotations

import cmath
import math
from pathlib import Path

import pytest
import pyvisa

from helpers import load_script, run_manual_script
from simubench.api import open_bench

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "benches" / "srs_sr830.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "srs_sr830_rc_sweep.py"

R, C = 1000.0, 100e-9
RS, LOAD, C_IN = 50.0, 10e6, 25e-12  # SINE_OUT, SIG_IN input, SIG_IN capacitance
AC_CORNER = 0.16  # Hz, ICPl = AC
AMPLITUDE = 0.5  # V rms

script = load_script(SCRIPT)


def expected(frequency: float) -> complex:
    """The settled phasor at SIG_IN for the bench: SINE_OUT 0.5 Vrms, RC, 10 MOhm || 25 pF, AC."""
    omega = 2 * math.pi * frequency
    z_c = 1 / (1j * omega * C)
    z_node = 1 / (1 / z_c + 1 / LOAD + 1j * omega * C_IN)
    h = z_node / (RS + R + z_node)
    ac = 1j * frequency / AC_CORNER / (1 + 1j * frequency / AC_CORNER)
    return AMPLITUDE * h * ac


def test_manual_script_runs_on_the_example_bench(tmp_path, monkeypatch, capsys):
    out = run_manual_script(script, ["GPIB0::8::INSTR"], BENCH, tmp_path, monkeypatch, capsys)
    assert out[0] == "Stanford_Research_Systems,SR830,s/n00111,ver1.000"
    points = [[float(x) for x in line.split(",")] for line in out[1:-1]]
    assert len(points) == len(script.FREQUENCIES)
    for (frequency, magnitude, theta), want in zip(points, map(expected, script.FREQUENCIES), strict=True):
        # The reading is a settled measurement of the circuit; 0.2 % covers the virtual-time
        # settling and the residual 2f ripple of the shortest time constant.
        assert magnitude == pytest.approx(abs(want), rel=2e-3), frequency
        assert theta == pytest.approx(math.degrees(cmath.phase(want)), abs=0.1), frequency
    # The RC corner: 1/sqrt(2) of the low-frequency value, -45 degrees.
    corner = points[script.FREQUENCIES.index(1515.76)]
    assert corner[1] == pytest.approx(AMPLITUDE / math.sqrt(2), rel=2e-3)
    assert corner[2] == pytest.approx(-45.0, abs=0.1)
    # The AUX DAC output behind 1 kOhm into the 1 MOhm ADC.
    assert out[-1] == "aux,1.5,1.4985015"


def test_the_real_resource_string_and_its_phasor():
    """A real SR830 resource string — the default GPIB address 8 — opens the simulated instrument,
    and the readings are one coherent phasor: X, Y, R and theta agree, fed by the same solver as
    the rest of the bench."""
    rm = pyvisa.ResourceManager(f"{BENCH}@simubench")
    try:
        lia = rm.open_resource("GPIB0::8::INSTR", read_termination="\n", write_termination="\n")
        try:
            # -- the GPIB address 8 of a real SR830 opens the simulated instrument (was
            # test_the_gpib_address_8_of_a_real_sr830_opens_the_simulated_instrument)
            assert lia.query("*IDN?") == "Stanford_Research_Systems,SR830,s/n00111,ver1.000"
            # A real program starts with OUTX: answers go to the GPIB interface it is talking on.
            lia.write("OUTX 1")
            assert lia.query("OUTX?") == "1"
            assert float(lia.query("OUTP? 3")) == pytest.approx(2 * abs(expected(1000.0)), rel=2e-3)
            # -- the readings are one coherent phasor (was test_the_readings_are_one_coherent_phasor)
            x = float(lia.query("OUTP? 1"))
            y = float(lia.query("OUTP? 2"))
            r = float(lia.query("OUTP? 3"))
            theta = float(lia.query("OUTP? 4"))
        finally:
            lia.close()
    finally:
        rm.close()
    assert r == pytest.approx(math.hypot(x, y), abs=1e-9)
    assert theta == pytest.approx(math.degrees(math.atan2(y, x)), abs=1e-6)
    assert r == pytest.approx(2 * abs(expected(1000.0)), rel=2e-3)


def _errors(session, command: bytes) -> list[dict]:
    before = len(session.history)
    session.write("lia", command)
    return [
        e for i in range(before, len(session.history)) if (e := session.history[i]).get("kind") == "error"
    ]


def test_a_not_modeled_command_is_recorded_with_its_coverage_origin():
    """Every ISRC and RSLP value is modeled since #273; a command outside the definition (FAST,
    the GPIB streaming) is recorded with its coverage origin and no reply (the real SR830 also does
    not answer an unknown command), and a value outside a command's range is refused (-224)."""
    s = open_bench(BENCH)
    try:
        assert _errors(s, b"ISRC 1\n") == []
        assert _errors(s, b"RSLP 2\n") == []
        [fast] = _errors(s, b"FAST 2\n")
        assert fast["code"] == -113 and fast["reason"] == "not_modeled"
        assert fast["origin"].endswith("srs-sr830.coverage.yaml")
        [outp] = _errors(s, b"OUTP? 5\n")
        assert outp["code"] == -224 and outp["reason"] == "not_modeled"
    finally:
        s.close()


# ---- #273: the data storage, the input configuration, the reference edge and the reserve --------


def _bench(parts: dict, wires: list, *, awg: bool = False):
    from simubench.models.bench import BenchDefinition

    instruments = {"lia": {"model": "srs-sr830"}, **({"awg": {"model": "sb-awg-2"}} if awg else {})}
    return BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "sr830-273",
            "instruments": instruments,
            "parts": parts,
            "wires": wires,
            "run": {"clock": "command", "idle": "off"},
        }
    )


def _open(bench):
    return open_bench(bench, definition_dirs=[ROOT / "validation" / "instruments"])


def _q(s, command: str, instrument: str = "lia") -> bytes | None:
    s.write(instrument, (command + "\n").encode())
    return s.read(instrument)


def _ascii(reply: bytes) -> list[float]:
    text = reply.decode().strip()
    assert text.endswith(",")  # every point is followed by a comma (manual 5-16)
    return [float(v) for v in text.rstrip(",").split(",")]


def test_the_storage_samples_at_the_rate_in_virtual_time_and_resumes_after_a_pause():
    s = open_bench(BENCH)
    try:
        _q(s, "SRAT 13;SEND 0;STRT")  # 512 Hz, single shot; the first point is at the start
        s.advance(0.01)
        assert _q(s, "SPTS?") == b"6\n"  # t = 0, 1/512, ..., 5/512 s
        _q(s, "PAUS")
        s.advance(1.0)
        assert _q(s, "SPTS?") == b"6\n"  # paused: nothing is taken
        _q(s, "STRT")
        s.advance(0.01)
        assert _q(s, "SPTS?") == b"12\n"  # resumed where it stopped
        x = _ascii(_q(s, "TRCA? 1,0,12"))
        y = _ascii(_q(s, "TRCA? 2,0,12"))
        z = complex(x[-1], y[-1])
        assert abs(z) == pytest.approx(abs(expected(1000.0)) * 2, rel=1e-3)  # SLVL 1 V: twice 0.5 V
        assert _q(s, "TRCA? 1,10,3") is None  # bins 10..12 of 12: not stored
    finally:
        s.close()


def test_single_shot_stops_when_full_and_loop_keeps_the_newest_points(monkeypatch):
    s = open_bench(BENCH)
    try:
        store = s.instruments["lia"].components["store"]
        monkeypatch.setitem(store.overrides, "capacity", 4)
        _q(s, "SRAT 14;SEND 0;STRT;TRIG;TRIG;TRIG;TRIG;TRIG")
        assert _q(s, "SPTS?") == b"4\n"  # full: the fifth trigger is not stored
        assert _q(s, "*STB? 0") == b"1\n"  # SCN: the single-shot scan is over
        # Loop, CH1 = AUX2 (the bench wires AUX_OUT2 to AUX_IN2): two points at 0 V, then three
        # with the DAC at 1.5 V (1 kΩ out into the 1 MΩ input: 1.5 × 1000/1001 V).
        _q(s, "REST;SEND 1;DDEF 1,4,0;STRT;TRIG;TRIG")
        _q(s, "AUXV 2,1.5")
        _q(s, "TRIG;TRIG;TRIG")
        assert _q(s, "SPTS?") == b"4\n"
        assert _q(s, "*STB? 0") == b"0\n"  # a loop scan keeps running
        # Five points taken, four kept: bin 0 is the oldest kept (the second 0 V point).
        assert _ascii(_q(s, "TRCA? 1,0,4")) == pytest.approx([0.0, *[1.5 * 1000 / 1001] * 3], abs=1e-6)
    finally:
        s.close()


def test_the_binary_encodings_carry_the_ascii_values():
    import struct

    from simubench.library.components.curve import lia16

    s = open_bench(BENCH)
    try:
        _q(s, "SRAT 14;STRT;TRIG;TRIG")
        values = _ascii(_q(s, "TRCA? 1,0,2"))
        raw = _q(s, "TRCB? 1,0,2")
        assert list(struct.unpack("<2f", raw[:8])) == pytest.approx(values, rel=1e-7)
        lia = _q(s, "TRCL? 1,0,2")[:8]
        for k, value in enumerate(values):
            m, e = struct.unpack("<hh", lia[4 * k : 4 * k + 4])
            assert m * 2.0 ** (e - 124) == pytest.approx(value, rel=1e-4)  # a 15-bit mantissa
    finally:
        s.close()
    for value in (1.0, -3.25e-7, 0.5, 2.0**-20):
        m, e = struct.unpack("<hh", lia16(value))
        assert m * 2.0 ** (e - 124) == pytest.approx(value, rel=1e-4)
        assert 16384 <= abs(m) <= 32767


def test_the_current_input_reads_amperes_through_one_kilohm_to_virtual_ground():
    """SINE_OUT (1 V rms, 50 Ω) through 10 kΩ into the current input: 1 V / 11.05 kΩ."""
    s = open_bench(
        _bench(
            {"r": {"type": "resistor", "params": {"r": 1.0e4}}},
            [{"from": "lia.SINE_OUT", "to": "r.A"}, {"from": "r.B", "to": "lia.SIG_IN"}],
        )
    )
    try:
        _q(s, "ISRC 2")
        s.advance(3.0)
        assert float(_q(s, "OUTP? 3")) == pytest.approx(1.0 / 11050.0, rel=2e-3)
        _q(s, "ISRC 0")  # the voltage input again: 10 MΩ, the full 1 V on SIG_IN
        s.advance(3.0)
        assert float(_q(s, "OUTP? 3")) == pytest.approx(1.0, rel=2e-3)
    finally:
        s.close()


def test_the_differential_input_measures_a_minus_b():
    """A gets SINE_OUT, B half of it through a 10 kΩ / 10 kΩ divider: A − B is half of A."""
    s = open_bench(
        _bench(
            {
                "top": {"type": "resistor", "params": {"r": 1.0e4}},
                "bottom": {"type": "load_resistor", "params": {"r": 1.0e4}},
            },
            [
                {"from": "lia.SINE_OUT", "to": "lia.SIG_IN"},
                {"from": "lia.SINE_OUT", "to": "top.A"},
                {"from": "top.B", "to": "bottom.A"},
                {"from": "top.B", "to": "lia.SIG_B"},
            ],
        )
    )
    try:
        s.advance(3.0)
        a = float(_q(s, "OUTP? 3"))
        _q(s, "ISRC 1")
        s.advance(3.0)
        assert float(_q(s, "OUTP? 3")) == pytest.approx(a / 2, rel=2e-3)
    finally:
        s.close()


def test_a_falling_ttl_edge_moves_the_external_reference_by_half_a_period():
    s = _open(
        _bench(
            {}, [{"from": "awg.OUT1", "to": "lia.REF_IN"}, {"from": "awg.OUT2", "to": "lia.SIG_IN"}], awg=True
        )
    )
    try:
        _q(s, "SOUR1:VOLT 2;:OUTP1 ON;:OUTP2 ON", "awg")
        _q(s, "FMOD 0")
        s.advance(3.0)
        sine = float(_q(s, "OUTP? 4"))
        _q(s, "RSLP 1")  # a rising TTL edge is the sine's positive zero crossing
        s.advance(3.0)
        assert float(_q(s, "OUTP? 4")) == pytest.approx(sine, abs=0.01)
        _q(s, "RSLP 2")
        s.advance(3.0)
        falling = float(_q(s, "OUTP? 4"))
        assert ((falling - sine + 180.0) % 360.0) - 180.0 == pytest.approx(-180.0, abs=0.01) or (
            ((falling - sine) % 360.0) == pytest.approx(180.0, abs=0.01)
        )
    finally:
        s.close()


def test_the_dynamic_reserve_follows_the_sensitivity_table():
    """Manual p.4-8: the actual reserve per RMOD column, log-interpolated between its decades."""
    s = open_bench(BENCH)
    try:
        amp = s.instruments["lia"].components["amp"]
        for rmod, sens, db in (("0", "26", 0.0), ("0", "20", 40.0), ("1", "17", 40.0), ("2", "23", 0.0)):
            _q(s, f"RMOD {rmod};SENS {sens}")  # 26 = 1 V, 20 = 10 mV, 17 = 1 mV, 23 = 100 mV
            assert 20 * math.log10(amp.reserve()) == pytest.approx(db, abs=1e-9)
        _q(s, "RMOD 2;SENS 0")  # 2 nV: between 1 nV (124 dB) and 10 nV (110 dB)
        assert 20 * math.log10(amp.reserve()) == pytest.approx(124.0 - 14.0 * math.log10(2.0), abs=1e-9)
    finally:
        s.close()
