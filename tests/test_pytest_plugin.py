"""The pytest plugin, run the way a user's test suite runs it."""

from pathlib import Path

pytest_plugins = ["pytester"]

BENCH = Path(__file__).resolve().parents[1] / "acceptance" / "benches" / "awg_rc_scope.bench.yaml"
LOCKIN = Path(__file__).resolve().parents[1] / "acceptance" / "benches" / "lockin_rc.bench.yaml"


def test_marker_fixtures_and_fresh_benches(pytester):
    pytester.makepyfile(
        f"""
        import pytest

        pytestmark = pytest.mark.bench({str(BENCH)!r}, seed=11)

        def test_identity(instruments, bench_session):
            assert instruments["scope"].query("*IDN?").startswith("SIMUBENCH,SB-DSO-4")
            assert sorted(instruments.names()) == ["awg", "scope"]
            assert bench_session.seed == 11
            instruments["awg"].write(":SOURce1:FREQuency 5000")

        def test_fresh_bench(instruments):
            assert float(instruments["awg"].query(":SOURce1:FREQuency?")) == 1000  # not 5000

        def test_real_address(instruments):
            assert "SB-AWG-2" in instruments["TCPIP0::192.168.1.20::5025::SOCKET"].query("*IDN?")
        """
    )
    pytester.runpytest_subprocess("--rootdir=.", "-p", "no:cacheprovider").assert_outcomes(passed=3)


def test_failure_names_the_record(pytester, tmp_path):
    pytester.makepyfile(
        """
        def test_wrong(instruments):
            assert instruments["awg"].query(":SOURce1:FREQuency?") == "2000"
        """
    )
    runs = tmp_path / "runs"
    # --rootdir keeps the subprocess's collection in this folder: with the repo's pyproject.toml
    # as the configfile, pytest 8.4 walks the parent directories up to the drive root, which on
    # Windows fails at the protected C:\Documents and Settings junction.
    result = pytester.runpytest_subprocess(
        "--rootdir=.", "--simubench-bench", str(BENCH), "--simubench-record-dir", str(runs)
    )
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*SimuBench run record*", "*reproduce: simubench replay *run-*.jsonl"])
    assert list(runs.rglob("run-*.jsonl"))


def test_missing_bench_is_explained(pytester):
    pytester.makepyfile("def test_x(instruments):\n    pass\n")
    result = pytester.runpytest_subprocess("--rootdir=.")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*no bench: use @pytest.mark.bench(path)*"])


def test_sleep_is_virtual_time_by_default(pytester):
    """D47: a slow machine reads the same numbers; time.sleep(t) is exactly t of virtual time."""
    pytester.makepyfile(
        f"""
        import time, pytest

        SEEN = {{}}
        BENCH = {str(LOCKIN)!r}

        @pytest.mark.bench(BENCH, clock="command")
        def test_by_sleep(instruments, bench_session):
            lia = instruments["lia"]
            lia.write(":SOURce:VOLTage 0.5")
            lia.write(":OUTPut:STATe ON")
            before = bench_session.now_ns
            t0 = time.perf_counter()
            time.sleep(2.0)
            assert time.perf_counter() - t0 < 0.1  # the test does not really wait
            assert bench_session.now_ns - before == 2_000_000_000
            SEEN["r"] = lia.query(":MEASure:R?")

        @pytest.mark.bench(BENCH, clock="command")
        def test_by_advance(instruments, bench_session):
            lia = instruments["lia"]
            lia.write(":SOURce:VOLTage 0.5")
            lia.write(":OUTPut:STATe ON")
            bench_session.advance(2.0)
            assert lia.query(":MEASure:R?") == SEEN["r"]  # the same instant, the same reading
        """
    )
    pytester.runpytest_subprocess("--rootdir=.", "-p", "no:cacheprovider").assert_outcomes(passed=2)


def test_marker_opts_back_into_wall_idle(pytester):
    pytester.makepyfile(
        f"""
        import time, pytest

        @pytest.mark.bench({str(LOCKIN)!r}, idle="wall", clock="command")
        def test_wall_idle_really_waits(instruments, bench_session):
            t0 = time.perf_counter()
            time.sleep(0.15)
            assert time.perf_counter() - t0 > 0.1  # the historical behaviour, on request
        """
    )
    pytester.runpytest_subprocess("--rootdir=.", "-p", "no:cacheprovider").assert_outcomes(passed=1)
