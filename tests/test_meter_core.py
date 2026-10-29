"""The ranged meter core (issue 87, metrology-authoring.md 2): range selection, autorange with
hysteresis, overload readings, resolution and the switchable high-impedance input that reaches the
bench circuit."""

from pathlib import Path

import pytest

from simubench.api import open_bench
from simubench.engine.component import component_type, map_setting
from simubench.models.bench import BenchDefinition

DEFINITIONS = Path(__file__).resolve().parent / "models" / "meter-core-test"


def bench(level: float = 0.0, *, divider: bool = False, **state) -> BenchDefinition:
    """The meter on an ideal voltage source (or, with ``divider``, behind 1 MOhm in series)."""
    dmm: dict = {"model": "meter-core-test"}
    if state:
        dmm["state"] = state
    if divider:
        duts = {"src": {"type": "dc_supply", "params": {"v": level, "r": 1.0e6}}}
        wires = [{"from": "src.OUT", "to": "dmm.IN"}]
        instruments = {"dmm": dmm}
    else:
        duts = None
        wires = [{"from": "src.HI", "to": "dmm.IN"}]
        instruments = {
            "src": {
                "model": "sb-smu-1",
                "state": {"source.voltage.level": level, "output.enabled": True},
            },
            "dmm": dmm,
        }
    spec: dict = {"name": "meter", "instruments": instruments, "wires": wires, "run": {"clock": "command"}}
    if duts:
        spec["duts"] = duts
    return BenchDefinition.model_validate(spec)


@pytest.fixture
def open_():
    sessions = []

    def open(**kw):
        s = open_bench(bench(**kw), definition_dirs=[DEFINITIONS])
        sessions.append(s)
        return s

    yield open
    for s in sessions:
        s.close()


def q(s, inst: str, cmd: str) -> str:
    s.write(inst, (cmd + "\n").encode())
    out = s.read(inst)
    return out.decode().strip() if out else ""


def reading(s, cmd: str = "MEAS:VOLT?") -> float:
    return float(q(s, "dmm", cmd).lstrip("+"))


def actual_range(s) -> float:
    return float(q(s, "dmm", "SENS:VOLT:RANG:ACT?").lstrip("+"))


# ---- the parameter mapping's table -----------------------------------------------------------------


def test_table_mapping_takes_the_largest_entry_not_above_the_value():
    table = {"from": "$sense.nplc", "table": {0.02: 1, 0.2: 2, 1: 3}}
    assert [map_setting(table, v) for v in (0.02, 0.5, 1, 5, 0.01)] == [1, 2, 3, 3, 1]


# ---- range selection -------------------------------------------------------------------------------


@pytest.mark.parametrize("setting,want", [(0.05, 0.1), (3, 10), (10, 10), (5000, 1000)])
def test_manual_range_takes_the_smallest_fitting_range(open_, setting, want):
    s = open_(**{"sense.range": setting, "sense.nplc": 10})
    assert actual_range(s) == want


def test_ac_ranges_come_from_the_ac_function(open_):
    s = open_(**{"sense.function": "ACV", "sense.range": 600, "sense.nplc": 10})
    assert actual_range(s) == 750


# ---- autorange -------------------------------------------------------------------------------------


def test_autorange_follows_the_true_value(open_):
    for level, want in ((0.5, 1), (15, 100), (0.05, 0.1)):
        s = open_(level=level, **{"sense.range_auto": True, "sense.nplc": 10})
        assert actual_range(s) == want


def test_autorange_hysteresis(open_):
    s = open_(level=1.0, **{"sense.range_auto": True, "sense.nplc": 10})
    assert actual_range(s) == 1
    q(s, "src", "SOUR:VOLT 1.1")  # inside the band (up = 1.2): keep the range
    assert reading(s) == pytest.approx(1.1, abs=1e-6)
    assert actual_range(s) == 1
    q(s, "src", "SOUR:VOLT 0.05")  # below down = 0.1 x full scale: drop
    assert reading(s) == pytest.approx(0.05, abs=1e-6)
    assert actual_range(s) == 0.1
    q(s, "src", "SOUR:VOLT 0.11")  # inside the band again: keep 0.1
    assert reading(s) == pytest.approx(0.11, abs=1e-6)
    assert actual_range(s) == 0.1
    q(s, "src", "SOUR:VOLT 1.0")  # above up = 1.2 x 0.1: jump to the fitting range
    assert reading(s) == pytest.approx(1.0, abs=1e-6)
    assert actual_range(s) == 1


# ---- overload and resolution -----------------------------------------------------------------------


def test_overload_reads_the_overload_value(open_):
    s = open_(level=0.13, **{"sense.range": 0.1, "sense.nplc": 10})
    assert q(s, "dmm", "MEAS:VOLT?") == "+9.900000000E+37"
    s2 = open_(level=0.12, **{"sense.range": 0.1, "sense.nplc": 10})  # 1.2 x full scale still reads
    assert q(s2, "dmm", "MEAS:VOLT?") == "+1.200000000E-01"


def test_resolution_quantizes_the_reading(open_):
    s = open_(level=1.0000004, **{"sense.range": 1, "sense.nplc": 1})
    assert reading(s) == pytest.approx(0.999999, abs=1e-9)  # 3e-6 x 1 V steps
    s2 = open_(level=1.0000004, **{"sense.range": 1, "sense.nplc": 0.5})  # NPLC 0.5 uses the 0.2 row
    assert reading(s2) == pytest.approx(0.99999, abs=1e-9)  # 3e-5 x 1 V steps


# ---- errors per function and range -----------------------------------------------------------------


def test_gain_error_per_range(open_):
    s = open_(
        level=10.0,
        **{"sense.range": 10, "sense.nplc": 10},
    )
    s.instruments["dmm"].components["meter"].overrides["gain_error_ppm"] = {"DC": {1: 100.0, 10: 5.0}}
    assert reading(s) == pytest.approx(10.00005, abs=3e-6)  # 5 ppm on the 10 V range
    q(s, "dmm", "SENS:VOLT:RANG 1")
    q(s, "src", "SOUR:VOLT 1.0")
    assert reading(s) == pytest.approx(1.0001, abs=2e-6)  # 100 ppm on the 1 V range


# ---- the input impedance reaches the circuit -------------------------------------------------------


def load(r: float) -> float:
    """The meter's input resistance with the solver's 1e-12 S leak to ground in parallel (GMIN,
    engine/circuit.py): a 1 MOhm series resistance turns that leak into a ~1 ppm drop, so the hand
    calculation has to include it."""
    return 1.0 / (1.0 / r + 1e-12)


def divided(emf: float, r_series: float, r_in: float) -> float:
    return emf * load(r_in) / (load(r_in) + r_series)


def test_high_z_changes_a_divided_reading(open_):
    # 10 V EMF behind 1 MOhm: the meter's input impedance divides it down, as on a real bench.
    s = open_(divider=True, level=10.0, **{"sense.range": 10, "sense.nplc": 10})
    low_z = divided(10.0, 1.0e6, 10.0e6)
    assert reading(s) == pytest.approx(low_z, rel=1e-6)  # within 1 ppm of the hand calculation
    q(s, "dmm", "INP:IMP:AUTO ON")
    high_z = divided(10.0, 1.0e6, 10.0e9)
    assert reading(s) == pytest.approx(high_z, rel=1e-6)
    assert reading(s) != pytest.approx(low_z, rel=1e-3)


def test_autorange_into_high_z_re_stamps_the_circuit(open_):
    # The first reading autoranges into a high-z range; the reading itself is taken after the
    # circuit has been re-solved with the new input impedance.
    s = open_(divider=True, level=0.5, **{"sense.range_auto": True, "input.high_z": True, "sense.nplc": 10})
    assert actual_range(s) == 1
    assert reading(s) == pytest.approx(divided(0.5, 1.0e6, 10.0e9), rel=1e-6)


# ---- the component catalog --------------------------------------------------------------------------


def test_catalog_lists_the_new_parameters_with_descriptions():
    from simubench.server.catalog import param_docs

    cls = component_type("dmm_core")
    docs = param_docs(cls)
    for name in (
        "function",
        "ranges",
        "range",
        "autorange",
        "autorange_up",
        "autorange_down",
        "overrange",
        "overload_value",
        "resolution",
        "z_in",
        "z_in_high",
        "high_z",
        "high_z_ranges",
    ):
        assert name in cls.defaults and docs.get(name), name
