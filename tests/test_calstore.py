"""The generic calibration store (issue 89, metrology-authoring.md 4): coefficients per function and
range adjusted independently, the security code, the ledger that survives *RST, and sb-dmm-1's
replies unchanged (test_calibration.py stays as it is)."""

from pathlib import Path

import pytest

from simubench.api import open_bench
from simubench.engine.component import component_type
from simubench.models.bench import BenchDefinition

DEFINITIONS = Path(__file__).resolve().parent / "models" / "cal-store-test"

HIDDEN = {
    "meter": {"gain_error_ppm": {"DC": {1: 200.0, 10: 50.0}, "AC": {1: 400.0, 10: 100.0}}, "offset": 300e-6}
}


def bench(**run) -> BenchDefinition:
    return BenchDefinition.model_validate(
        {
            "name": "calstore",
            "instruments": {
                "src": {
                    "model": "sb-smu-1",
                    "state": {"source.voltage.level": 0.0, "output.enabled": True},
                },
                "dmm": {"model": "cal-store-test", "hidden": HIDDEN},
            },
            "wires": [{"from": "src.HI", "to": "dmm.IN"}],
            "run": {"clock": "command", "start": "2026-05-04T09:00:00Z", **run},
        }
    )


@pytest.fixture
def s():
    session = open_bench(bench(), definition_dirs=[DEFINITIONS])
    yield session
    session.close()


def q(s, cmd: str) -> str:
    s.write("dmm", (cmd + "\n").encode())
    out = s.read("dmm")
    return out.decode().strip() if out else ""


def reading(s, cmd: str = ":MEAS:VOLT?") -> float:
    return float(q(s, cmd).lstrip("+"))


def adjust(s, level: float, reference: float, cmd: str = ":CAL?") -> None:
    s.write("src", f":SOUR:VOLT {level}\n".encode())
    reading(s)  # the input settles before the adjustment reads it
    q(s, f":CAL:VAL {reference}")
    assert q(s, cmd) == "+0"


def test_coefficients_per_range_adjust_independently(s):
    assert q(s, ':CAL:SEC:STAT OFF,"SIMUBENCH"') == ""
    q(s, ":SENS:VOLT:RANG 1")
    s.write("src", b":SOUR:VOLT 1\n")
    assert reading(s) == pytest.approx(1.0 * (1 + 200e-6) + 300e-6, abs=1e-9)  # as-found: 200 ppm
    adjust(s, 1.0, 1.0)
    assert reading(s) == pytest.approx(1.0, abs=1e-9)  # as-left on the adjusted range
    q(s, ":SENS:VOLT:RANG 10")
    s.write("src", b":SOUR:VOLT 10\n")
    assert reading(s) == pytest.approx(10.0 * (1 + 50e-6) + 300e-6, abs=1e-9)  # the other range: untouched
    adjust(s, 10.0, 10.0)
    q(s, ":CAL:STOR")
    q(s, ":SENS:VOLT:RANG 1")
    s.write("src", b":SOUR:VOLT 1\n")
    assert reading(s) == pytest.approx(1.0, abs=1e-9)
    q(s, ":SENS:VOLT:RANG 10")
    s.write("src", b":SOUR:VOLT 10\n")
    assert reading(s) == pytest.approx(10.0, abs=1e-9)
    assert reading(s, ":MEAS:VOLT:AC?") == pytest.approx(0.0, abs=1e-12)  # no AC input, no DC offset leaked


def test_ac_gain_is_its_own_key(s):
    q(s, ':CAL:SEC:STAT OFF,"SIMUBENCH"')
    q(s, ":SENS:VOLT:RANG 1")
    adjust(s, 1.0, 1.0)  # DC 1 V range: the gain stored under (DC, 1), the offset untouched
    meter = s.instrument("dmm").components["meter"]
    assert meter._cal("DC") == pytest.approx((1.0 / 1.0005, 0.0), rel=1e-9)
    assert meter._cal("AC") == pytest.approx((1.0, 0.0), abs=1e-12)  # the AC key was never adjusted
    q(s, ":SENS:FUNC ACV")
    q(s, ":SENS:VOLT:RANG 1")
    assert reading(s, ":MEAS:VOLT:AC?") == pytest.approx(0.0, abs=1e-12)  # and the DC offset does not leak


def test_security_code(s):
    q(s, ":CAL:VAL 1")
    q(s, ":CAL?")  # secured: refused
    assert q(s, ":SYST:ERR?").startswith("701,")
    q(s, ':CAL:SEC:STAT OFF,"WRONG"')
    assert q(s, ":SYST:ERR?").startswith("701,")
    q(s, ':CAL:SEC:STAT OFF,"SIMUBENCH"')
    q(s, ':CAL:CODE "NEWCODE"')
    q(s, ":CAL:SEC:STAT ON")
    q(s, ':CAL:SEC:STAT OFF,"SIMUBENCH"')  # the factory code no longer unsecures
    assert q(s, ":SYST:ERR?").startswith("701,")
    q(s, ':CAL:SEC:STAT OFF,"NEWCODE"')
    assert q(s, ":CAL:SEC:STAT?") == "0"


def test_failed_adjustment_reports_and_changes_nothing(s):
    q(s, ':CAL:SEC:STAT OFF,"SIMUBENCH"')
    q(s, ":SENS:VOLT:RANG 1")
    s.write("src", b":SOUR:VOLT 1\n")
    reading(s)
    q(s, ":CAL:VAL 2")
    assert q(s, ":CAL?") == "+1"
    assert q(s, ":SYST:ERR?").startswith("702,")
    s.write("src", b":SOUR:VOLT 10\n")  # not zero: the zero adjustment refuses too
    reading(s)
    assert q(s, ":CAL:ZERO?") == "+1"
    assert q(s, ":SYST:ERR?").startswith("702,")
    q(s, ":SENS:VOLT:RANG 10")  # the input fits this range: the reading is unchanged
    assert reading(s) == pytest.approx(10.0 * (1 + 50e-6) + 300e-6, abs=1e-9)


def test_ledger_survives_rst_but_volatile_adjustments_do_not(s):
    q(s, ':CAL:SEC:STAT OFF,"SIMUBENCH"')
    q(s, ':CAL:STR "SN 001, lab 2"')
    q(s, ":SENS:VOLT:RANG 1")
    s.write("src", b":SOUR:VOLT 0\n")
    reading(s)
    assert q(s, ":CAL:ZERO?") == "+0"
    adjust(s, 1.0, 1.0)
    q(s, ":CAL:STOR")
    assert q(s, ":CAL:COUN?") == "1"
    assert q(s, ":CAL:DATE?") == '"2026-05-04"'
    assert q(s, ":CAL:TIME?") == '"09:00:00"'
    s.write("src", b":SOUR:VOLT 0\n")
    assert reading(s) == pytest.approx(0.0, abs=1e-12)  # as-left: zeroed and gained
    s.write("src", b":SOUR:VOLT 1\n")
    assert reading(s) == pytest.approx(1.0, abs=1e-9)
    # An adjustment that is never stored is volatile on this model: pending on the 10 V range.
    q(s, ":SENS:VOLT:RANG 10")
    adjust(s, 10.0, 10.0)
    q(s, "*RST")
    assert q(s, ":CAL:COUN?") == "1" and q(s, ":CAL:DATE?") == '"2026-05-04"'
    assert q(s, ":CAL:STR?") == '"SN 001, lab 2"'
    q(s, ":SENS:VOLT:RANG 1")
    s.write("src", b":SOUR:VOLT 0\n")
    assert reading(s) == pytest.approx(0.0, abs=1e-12)  # the stored adjustment still applies
    q(s, ":SENS:VOLT:RANG 10")
    s.write("src", b":SOUR:VOLT 10\n")
    assert reading(s) == pytest.approx(10.0 * (1 + 50e-6) + 300e-6, abs=1e-9)  # the volatile one is gone


def test_count_is_not_settable(s):
    q(s, ":CAL:COUN 0")
    assert q(s, ":SYST:ERR?").startswith("-221,")


def test_catalog_lists_the_store():
    from simubench.engine.validate_definition import operations
    from simubench.server.catalog import param_docs

    cls = component_type("calibration_store")
    docs = param_docs(cls)
    for name in (
        "secured",
        "code",
        "key",
        "value",
        "tolerance",
        "ok_reply",
        "fail_reply",
        "volatile_until_store",
    ):
        assert name in cls.defaults and docs.get(name), name
    assert set(operations(cls)) == {
        "secure",
        "code",
        "value",
        "adjust",
        "store",
        "count",
        "date",
        "time",
        "string",
    }
