"""pytest plugin: test scripts against a SimuBench bench, the way they run against hardware (M5).

Select the bench per test or per module with a marker, or for the whole run on the command line::

    @pytest.mark.bench("benches/awg_rc_scope.bench.yaml", clock="command", seed=7)
    def test_gain(instruments):
        scope = instruments["scope"]          # opened through PyVISA, like a real instrument
        assert scope.query("*IDN?").startswith("SIMUBENCH")

    pytest --simubench-bench benches/awg_rc_scope.bench.yaml --simubench-record-dir runs

Every test gets a freshly powered bench (D31). Idle real time does not move the virtual clock:
the plugin defaults to ``idle: sleep`` (D47), so ``time.sleep(t)`` advances virtual time by
exactly ``t`` without waiting and a slow machine reads the same numbers as a fast one. A test
that needs the historical behaviour opts back in with ``idle="wall"``.

With ``--simubench-record-dir`` each test writes a run record; a failing test's report names it
and the ``simubench replay`` command (D36).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

ENV = ("SIMUBENCH_RECORD_DIR", "SIMUBENCH_CLOCK", "SIMUBENCH_SEED", "SIMUBENCH_IDLE")


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("simubench", "SimuBench virtual benches")
    group.addoption("--simubench-bench", help="bench file for tests without a bench marker")
    group.addoption("--simubench-record-dir", help="write a run record per test into this folder")
    parser.addini("simubench_bench", "bench file for tests without a bench marker", default="")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "bench(path, clock=None, seed=None, idle=None): the SimuBench bench file this test runs "
        "against; idle defaults to 'sleep' so results do not depend on machine speed (D47)",
    )


def _bench_path(request: pytest.FixtureRequest) -> tuple[Path, dict[str, Any]]:
    marker = request.node.get_closest_marker("bench")
    if marker is not None and marker.args:
        raw, options = str(marker.args[0]), dict(marker.kwargs)
    else:
        raw = request.config.getoption("--simubench-bench") or request.config.getini("simubench_bench")
        options = {}
        if not raw:
            pytest.fail(
                "no bench: use @pytest.mark.bench(path), --simubench-bench or simubench_bench in the ini file"
            )
    path = Path(raw)
    if not path.is_absolute():
        # Relative to the test file first, then to the rootdir.
        here = Path(str(request.node.fspath)).parent / path
        path = here if here.exists() else Path(str(request.config.rootpath)) / path
    if not path.exists():
        pytest.fail(f"bench file not found: {raw}")
    return path.resolve(), options


@pytest.fixture
def visa_rm(request: pytest.FixtureRequest) -> Iterator[Any]:
    """A PyVISA ResourceManager on a freshly powered bench."""
    import pyvisa

    path, options = _bench_path(request)
    saved = {k: os.environ.get(k) for k in ENV}
    record_dir = request.config.getoption("--simubench-record-dir")
    if record_dir:
        name = re.sub(r"[^0-9A-Za-z_.-]+", "_", request.node.nodeid)[-120:]
        os.environ["SIMUBENCH_RECORD_DIR"] = str(Path(record_dir).resolve() / name)
    if options.get("clock"):
        os.environ["SIMUBENCH_CLOCK"] = str(options["clock"])
    if options.get("seed") is not None:
        os.environ["SIMUBENCH_SEED"] = str(options["seed"])
    # A test must read the same numbers on a slow machine as on a fast one: idle real time does
    # not move the virtual clock, and time.sleep(t) advances it by exactly t (D47).
    os.environ["SIMUBENCH_IDLE"] = str(options.get("idle") or "sleep")
    try:
        rm = pyvisa.ResourceManager(f"{path}@simubench")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    recorder = getattr(rm.visalib.bench, "recorder", None)
    request.node.stash[RECORD_KEY] = str(recorder.path) if recorder else None
    yield rm
    rm.close()


@pytest.fixture
def bench_session(visa_rm: Any) -> Any:
    """The simulation behind the instruments, for what a script cannot do on a real bench:
    advancing virtual time, injecting faults, reading true values."""
    return visa_rm.visalib.bench


class Instruments:
    """Resources by instrument name or address, opened on first use with newline terminations."""

    def __init__(self, rm: Any) -> None:
        self._rm = rm
        self._open: dict[str, Any] = {}

    def __getitem__(self, name: str) -> Any:
        if name not in self._open:
            self._open[name] = self._rm.open_resource(name, read_termination="\n", write_termination="\n")
        return self._open[name]

    def names(self) -> list[str]:
        return list(self._rm.visalib.bench.instruments)


@pytest.fixture
def instruments(visa_rm: Any) -> Instruments:
    return Instruments(visa_rm)


RECORD_KEY = pytest.StashKey[str | None]()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Iterator[Any]:
    report = yield
    record = item.stash.get(RECORD_KEY, None)
    if record and report.when == "call" and report.failed:
        report.sections.append(("SimuBench run record", f"{record}\nreproduce: simubench replay {record}"))
    return report
