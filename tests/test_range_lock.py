"""The range lock of ``calibrator_setpoint`` and its ``lock_functions`` (#589), at the component
level (#592): a test-only definition (tests/definitions/test-range-lock.yaml), not the 5522A."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from helpers import session_query
from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

DEFS = Path(__file__).parent / "definitions"
SOURCE = DEFS / "test-range-lock.yaml"


def session(tmp_path: Path, lock_functions: list[str] | None = None):
    """A bench with the test calibrator; ``lock_functions`` replaces the definition's list."""
    data = yaml.safe_load(SOURCE.read_text("utf-8"))
    if lock_functions is not None:
        dac = next(c for c in data["components"] if c["name"] == "dac")
        dac["params"]["lock_functions"] = lock_functions
    (tmp_path / "test-range-lock.yaml").write_text(yaml.safe_dump(data), "utf-8")
    bench = BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "lock",
            "instruments": {"cal": {"model": "test-range-lock"}},
            "run": {"clock": "command", "idle": "off"},
        }
    )
    return open_bench(bench, definition_dirs=[tmp_path])


def q(s, cmd: str) -> str:
    return session_query(s, "cal", cmd, default="")


def err(s) -> str:
    return q(s, ":SYSTem:ERRor?").split(",")[0].lstrip("+")


@pytest.fixture
def listed(tmp_path):
    s = session(tmp_path)  # lock_functions: [DCV, DCI]
    yield s
    s.close()


def test_an_empty_list_locks_every_function(tmp_path):
    s = session(tmp_path, [])
    try:
        q(s, "FUNC ACV;LEV 0.5;RANG:LOCK ON")
        assert q(s, "RANG?") == "AC1V"
        q(s, "LEV 5")
        assert err(s) == "518"  # over the locked AC 1 V range
        q(s, "LEV 0.05")
        assert err(s) == "519"  # under its 0.1 V minimum
        assert float(q(s, "LEV?")) == pytest.approx(0.5)
    finally:
        s.close()


def test_the_range_lock_flow(listed):
    """One flow on the locked calibrator: a listed function locks the auto range and refuses
    levels outside it, a current function locks too, an unlisted function keeps the flag but
    keeps ranging with the level, and leaving any function clears the lock (#589)."""
    # -- a listed function refuses levels over the locked range
    # (was test_a_listed_function_refuses_levels_over_the_locked_range)
    q(listed, "FUNC DCV;LEV 5;RANG:LOCK ON")
    assert q(listed, "RANG?") == "DC10V"
    q(listed, "LEV 50")
    assert err(listed) == "518"
    assert float(q(listed, "LEV?")) == pytest.approx(5.0)
    q(listed, "LEV 0.5")  # below the auto range's boundary but inside the locked range
    assert err(listed) == "0"
    assert q(listed, "RANG?") == "DC10V"
    q(listed, "RANG:LOCK OFF")  # bridge: every act locks from the flag off, as on its own bench
    # -- a listed function refuses levels under the locked range
    # (was test_a_listed_function_refuses_levels_under_the_locked_range)
    q(listed, "FUNC DCV;LEV 50;RANG:LOCK ON")
    assert q(listed, "RANG?") == "DC100V"
    q(listed, "LEV 5")
    assert err(listed) == "519"  # under the 100 V range's 10 V minimum
    assert float(q(listed, "LEV?")) == pytest.approx(50.0)
    q(listed, "RANG:LOCK OFF")  # bridge
    # -- a listed current function is locked too (was test_a_listed_current_function_is_locked_too)
    q(listed, "FUNC DCI;LEV 0.005;RANG:LOCK ON")
    assert q(listed, "RANG?") == "DC10MA"
    q(listed, "LEV 0.05")
    assert err(listed) == "518"
    q(listed, "RANG:LOCK OFF")  # bridge
    # -- an unlisted function keeps the flag but ranges with the level
    # (was test_an_unlisted_function_keeps_the_flag_but_ranges_with_the_level)
    q(listed, "FUNC ACV;LEV 0.5;RANG:LOCK ON")
    assert q(listed, "RANG:LOCK?") == "1"  # the setting is kept and reads back
    assert q(listed, "RANG?") == "AC1V"
    q(listed, "LEV 5")
    assert err(listed) == "0"  # no lock error
    assert q(listed, "RANG?") == "AC10V"  # the range follows the level
    q(listed, "RANG:LOCK OFF")  # bridge: the next act locks from the flag off
    # -- leaving an unlisted function clears the lock (was test_leaving_an_unlisted_function_clears_the_lock)
    q(listed, "FUNC ACV;LEV 0.5;RANG:LOCK ON")
    assert q(listed, "RANG:LOCK?") == "1"
    q(listed, "FUNC DCV")
    assert q(listed, "RANG:LOCK?") == "0"
    q(listed, "LEV 50")
    assert err(listed) == "0"  # DCV is listed, but the lock went with the function change
    assert q(listed, "RANG?") == "DC100V"
    # -- leaving a listed function clears the lock (was test_leaving_a_listed_function_clears_the_lock;
    # the previous act left the flag off, as on the original bench)
    q(listed, "FUNC DCV;LEV 0.5;RANG:LOCK ON")
    q(listed, "FUNC DCI")
    assert q(listed, "RANG:LOCK?") == "0"
    q(listed, "LEV 0.05")
    assert err(listed) == "0"


def test_function_names_match_whatever_their_case(tmp_path):
    s = session(tmp_path, ["dcv"])
    try:
        q(s, "FUNC DCV;LEV 0.5;RANG:LOCK ON")
        q(s, "LEV 5")
        assert err(s) == "518"
    finally:
        s.close()
