"""The lock-in readings table (#383): OUTP?/SNAP?/SNAPD? by index or word, the %g response format
and phase in radians — what the SR86x reads with, on generic components.

``sb-lia-1`` is patched in the test (not a new validation instrument): its ``lockin_output`` gets an
SR86x-style table (X, Y, R, THeta, IN1, XNOise, OUT1, PHAse, FInt; index = position), and the
definition answers numbers in ``general`` format with 11 significant digits. The RC acceptance bench
(sine out -> 1 kOhm / 100 nF -> input) gives a settled, non-trivial X and Y.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from simubench.api import open_bench
from simubench.scpi import values as V
from simubench.units import parse_number

ROOT = Path(__file__).resolve().parents[1]
DEFS = ROOT / "validation" / "instruments"
BENCH = ROOT / "acceptance" / "benches" / "lockin_rc.bench.yaml"

TABLE = [
    {"word": "X", "source": "X"},
    {"word": "Y", "source": "Y"},
    {"word": "R", "source": "R"},
    {"word": "THeta", "source": "THETA"},
    {"word": "IN1", "source": "aux.AUX1"},
    {"word": "XNOise", "source": "XNOISE"},
    {"word": "OUT1", "source": "$aux.output1"},
    {"word": "PHAse", "source": "$reference.phase"},
    {"word": "FInt", "source": "osc.FREQUENCY"},
]


@pytest.fixture
def lia(tmp_path):
    d = yaml.safe_load((DEFS / "sb-lia-1.yaml").read_text("utf-8"))
    d["format"] = {"number": "general", "digits": 11, "sign": False, "readings": "format"}
    for c in d["components"]:
        if c["name"] == "demod":
            c.setdefault("params", {})["readings"] = TABLE
    for n in range(4):
        d["state"][f"display.dat{n}"] = {"type": "int", "min": 0, "max": len(TABLE) - 1, "default": n}
    # OUTP is the SR86x reading query here, not sb-lia-1's :OUTPut[:STATe] (its short form)
    d["commands"] = [c for c in d["commands"] if not c["header"].startswith(":OUTPut")]
    d["commands"] += [
        {"header": "OUTP", "op": "demod.readout", "set": False, "params": [{"name": "names", "type": "raw"}]},
        {
            "header": "SNAP",
            "op": "demod.readout",
            "set": False,
            "args": {"minimum": 2, "maximum": 3},
            "params": [{"name": "names", "type": "raw"}],
        },
        {
            "header": "SNAPD",
            "op": "demod.readout",
            "set": False,
            "args": {"names": [f"$display.dat{n}" for n in range(4)], "minimum": 4, "maximum": 4},
        },
        {"header": "PHAS", "state": "reference.phase"},
        {
            "header": "CDSP",
            "params": [
                {
                    "name": "ch",
                    "type": "int",
                    "min": 0,
                    "max": 3,
                    "index": True,
                    "index_numbers": True,
                    "index_words": {"DAT1": 0, "DAT2": 1, "DAT3": 2, "DAT4": 3},
                    "echo": False,
                },
                {"name": "value", "type": "int", "state": "display.dat{ch}"},
            ],
        },
    ]
    for n in range(4):  # the parameter may be written as its word (CDSP DAT1, R)
        d["state"][f"display.dat{n}"]["special"] = {"X": 0, "Y": 1, "R": 2, "TH": 3, "THETA": 3}
    (tmp_path / "sb-lia-1.yaml").write_text(yaml.safe_dump(d, allow_unicode=True), "utf-8")
    session = open_bench(BENCH, definition_dirs=[tmp_path, DEFS], clock="stepped")
    session.advance(1.0)  # 30 ms time constant, 24 dB/oct: settled

    def q(text: str) -> str:
        session.write("lia", (text + "\n").encode())
        reply = session.read("lia")
        assert reply is not None, text
        return reply.decode().strip()

    def w(text: str) -> None:
        session.write("lia", (text + "\n").encode())

    yield q, w
    session.close()


def _floats(text: str) -> list[float]:
    return [float(v) for v in text.split(",")]


def test_outp_takes_the_index_or_the_word(lia):
    q, _ = lia
    for index, word in ((0, "X"), (1, "Y"), (2, "R"), (3, "THeta")):
        by_index = float(q(f"OUTP? {index}"))
        assert by_index == pytest.approx(float(q(f"OUTP? {word}")), rel=1e-9)
    assert float(q("OUTP? TH")) == pytest.approx(float(q("OUTP? THETA")), rel=1e-9)
    # the RC near its corner: X > 0 > Y at the settled output, R = |X + jY|
    x, y = float(q("OUTP? X")), float(q("OUTP? Y"))
    assert x > 0 and y < 0
    assert float(q("OUTP? R")) == pytest.approx(math.hypot(x, y), rel=1e-6)
    # near the RC corner (the input's 25 pF loads it a little): theta is the angle of X + jY
    assert float(q("OUTP? THeta")) == pytest.approx(math.degrees(math.atan2(y, x)), abs=1e-3)
    assert -50.0 < float(q("OUTP? THeta")) < -40.0


def test_snap_reads_its_parameters_at_one_instant_in_the_order_asked(lia):
    q, _ = lia
    x, y, r = _floats(q("SNAP? 0,1,2"))
    assert r == pytest.approx(math.hypot(x, y), rel=1e-9)  # one instant: R agrees with X, Y exactly
    assert _floats(q("SNAP? Y, X")) == pytest.approx([y, x], rel=1e-6)


def test_snap_takes_two_or_three_parameters(lia):
    q, w = lia
    for bad in ("SNAP? 0", "SNAP? 0,1,2,3"):
        w(bad)
        assert q(":SYSTem:ERRor?").startswith("-109,")


def test_unknown_words_and_indexes_are_refused(lia):
    q, w = lia
    w("OUTP? ZZ")
    assert q(":SYSTem:ERRor?").startswith("-224,")
    w(f"OUTP? {len(TABLE)}")
    assert q(":SYSTem:ERRor?").startswith("-222,")


def test_setting_and_block_sources(lia):
    q, w = lia
    w("PHAS 30")
    assert float(q("OUTP? PHA")) == pytest.approx(30.0)
    assert float(q("OUTP? FI")) == pytest.approx(1591.55)  # the reference's frequency
    assert float(q("OUTP? OUT1")) == pytest.approx(0.0)  # AUX OUT1's DAC setting
    assert float(q("OUTP? IN1")) == pytest.approx(0.0, abs=1e-12)  # nothing on AUX IN1
    assert float(q("OUTP? XNO")) > 0.0  # the front end's noise density


def test_snapd_reads_the_four_assigned_data_channels(lia):
    q, _ = lia
    x, y, r, theta = _floats(q("SNAPD?"))
    assert r == pytest.approx(math.hypot(x, y), rel=1e-9)
    assert theta == pytest.approx(math.degrees(math.atan2(y, x)), abs=1e-6)


def test_replies_follow_the_general_format(lia):
    q, _ = lia
    reply = q("OUTP? X")
    assert reply == V.format_float(float(reply), 11, style="general", sign=False).decode()
    assert "E" not in reply and not reply.startswith("+")
    assert V.format_float(2.3346669042e-09, 11, style="general", sign=False) == b"2.3346669042e-09"
    assert V.format_float(19.749698639, 11, style="general", sign=False) == b"19.749698639"
    assert V.format_float(0.951359, 6, style="general", sign=False) == b"0.951359"


def test_phase_in_radians(lia):
    q, w = lia
    w("PHAS 1 RAD")
    assert float(q("PHAS?")) == pytest.approx(math.degrees(1.0), rel=1e-9)
    w("PHAS 500 MRAD")
    assert float(q("PHAS?")) == pytest.approx(math.degrees(0.5), rel=1e-9)
    w("PHAS 12340 MDEG")
    assert float(q("PHAS?")) == pytest.approx(12.34, rel=1e-9)
    assert parse_number("1 URAD", "DEG") == pytest.approx(math.degrees(1e-6))
    with pytest.raises(ValueError):
        parse_number("1 RAD", "HZ")


def test_a_mapped_parameter_value_may_name_another_setting(tmp_path):
    """{from: $on, map: {"1": $percent}} reads the percentage while on, 0 when off (#383, COFA/COFP)."""
    d = yaml.safe_load((DEFS / "sb-lia-1.yaml").read_text("utf-8"))
    d["state"]["output.offset_on"] = {"type": "int", "min": 0, "max": 1, "default": 0}
    d["state"]["output.offset_pct"] = {"type": "float", "min": -999.99, "max": 999.99, "default": 0.0}
    for c in d["components"]:
        if c["name"] == "demod":
            c["params"]["offset_x"] = {
                "from": "$output.offset_on",
                "map": {"0": 0.0, "1": "$output.offset_pct"},
            }
    (tmp_path / "sb-lia-1.yaml").write_text(yaml.safe_dump(d, allow_unicode=True), "utf-8")
    session = open_bench(BENCH, definition_dirs=[tmp_path, DEFS], clock="stepped")
    try:
        demod = session.instruments["lia"].components["demod"]
        session.instruments["lia"].set_state("output.offset_pct", 12.5)
        assert demod.param("offset_x") == 0.0
        session.instruments["lia"].set_state("output.offset_on", 1)
        assert demod.param("offset_x") == pytest.approx(12.5)
    finally:
        session.close()


def test_an_index_word_or_its_number_and_a_word_assignment(lia):
    """index_numbers (#333): CDSP DAT2 and CDSP 1 are the same channel; an assignment written as a
    word is stored as a number (1.0) that SNAPD? and the readings table still read."""
    q, w = lia
    w("CDSP DAT1, R")
    assert q("CDSP? 0") == q("CDSP? DAT1") == "2"
    w("CDSP 1, 0")
    assert q("CDSP? DAT2") == "0"
    r1, x, r3, theta = _floats(q("SNAPD?"))  # DAT1 R, DAT2 X, DAT3 R (default), DAT4 theta
    assert r1 == r3  # one instant
    assert x == pytest.approx(r1 * math.cos(math.radians(theta)), rel=1e-6)
    w("CDSP? 4")
    assert q(":SYSTem:ERRor?").startswith("-222,")


def test_a_map_keyed_by_an_int_matches_its_float_value(tmp_path):
    from simubench.engine.component import map_setting

    assert map_setting({"map": {"0": "A", "1": "A-B"}}, 1.0) == "A-B"
    assert map_setting({"map": {"0": "A", "1": "A-B"}}, 1) == "A-B"
    assert map_setting({"map": {"0": "A", "1": "A-B"}, "default": "A"}, 1.5) == "A"
