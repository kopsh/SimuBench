"""The pytest plugin, run the way a user's test suite runs it."""

from pathlib import Path

pytest_plugins = ["pytester"]

BENCH = Path(__file__).resolve().parents[1] / "acceptance" / "benches" / "awg_rc_scope.bench.yaml"


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
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(passed=3)


def test_failure_names_the_record(pytester, tmp_path):
    pytester.makepyfile(
        """
        def test_wrong(instruments):
            assert instruments["awg"].query(":SOURce1:FREQuency?") == "2000"
        """
    )
    runs = tmp_path / "runs"
    result = pytester.runpytest_subprocess(
        "--simubench-bench", str(BENCH), "--simubench-record-dir", str(runs)
    )
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*SimuBench run record*", "*reproduce: simubench replay *run-*.jsonl"])
    assert list(runs.rglob("run-*.jsonl"))


def test_missing_bench_is_explained(pytester):
    pytester.makepyfile("def test_x(instruments):\n    pass\n")
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*no bench: use @pytest.mark.bench(path)*"])
