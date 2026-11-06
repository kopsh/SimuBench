"""The pytest plugin, run the way a user's test suite runs it."""

import sys
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]


@pytest.fixture(autouse=True)
def _utf8_subprocess(monkeypatch):
    """The plugin's messages carry the definition's Chinese text; a Windows subprocess's console
    codec is cp1252 and mangles it before the parent decodes UTF-8. Force UTF-8 in the child."""
    monkeypatch.setenv("PYTHONUTF8", "1")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")


BENCH = Path(__file__).resolve().parents[1] / "acceptance" / "benches" / "awg_rc_scope.bench.yaml"
LOCKIN = Path(__file__).resolve().parents[1] / "acceptance" / "benches" / "lockin_rc.bench.yaml"


def test_marker_fixtures_and_fresh_benches(pytester):
    pytester.makepyfile(
        test_case=f"""
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
    pytester.runpytest_subprocess("--rootdir=.", "test_case.py", "-p", "no:cacheprovider").assert_outcomes(
        passed=3
    )


def test_failure_names_the_record(pytester, tmp_path):
    pytester.makepyfile(
        test_case="""
        def test_wrong(instruments):
            assert instruments["awg"].query(":SOURce1:FREQuency?") == "2000"
        """
    )
    runs = tmp_path / "runs"
    # --rootdir plus the test file as a positional argument keep the subprocess's collection in
    # this folder: with the repo's pyproject.toml as the configfile and no positional argument,
    # pytest 8.4 collects the invocation directory's parent chain up to the drive root, which on
    # Windows fails at the protected C:\Documents and Settings junction.
    result = pytester.runpytest_subprocess(
        "--rootdir=.", "test_case.py", "--simubench-bench", str(BENCH), "--simubench-record-dir", str(runs)
    )
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*SimuBench run record*", "*reproduce: simubench replay *run-*.jsonl"])
    assert list(runs.rglob("run-*.jsonl"))


def test_missing_bench_is_explained(pytester):
    pytester.makepyfile(test_case="def test_x(instruments):\n    pass\n")
    result = pytester.runpytest_subprocess("--rootdir=.", "test_case.py")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*no bench: use @pytest.mark.bench(path)*"])


def test_sleep_is_virtual_time_by_default(pytester):
    """D47: a slow machine reads the same numbers; time.sleep(t) is exactly t of virtual time."""
    pytester.makepyfile(
        test_case=f"""
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
    pytester.runpytest_subprocess("--rootdir=.", "test_case.py", "-p", "no:cacheprovider").assert_outcomes(
        passed=2
    )


def test_marker_opts_back_into_wall_idle(pytester):
    pytester.makepyfile(
        test_case=f"""
        import time, pytest

        @pytest.mark.bench({str(LOCKIN)!r}, idle="wall", clock="command")
        def test_wall_idle_really_waits(instruments, bench_session):
            t0 = time.perf_counter()
            time.sleep(0.15)
            assert time.perf_counter() - t0 > 0.1  # the historical behaviour, on request
        """
    )
    pytester.runpytest_subprocess("--rootdir=.", "test_case.py", "-p", "no:cacheprovider").assert_outcomes(
        passed=1
    )


def test_options_travel_in_the_resource_string(pytester, monkeypatch):
    """The marker's options ride the resource string's query (issue 640): the seed applies even
    with a conflicting SIMUBENCH_SEED in the environment, and an unknown option name is an
    error, not a silent default."""
    monkeypatch.setenv("SIMUBENCH_SEED", "3")
    pytester.makepyfile(
        test_case=f"""
        import pytest

        pytestmark = pytest.mark.bench({str(BENCH)!r}, seed=11)

        def test_seed_from_the_marker(instruments, bench_session):
            assert bench_session.seed == 11
        """
    )
    pytester.runpytest_subprocess("--rootdir=.", "test_case.py", "-p", "no:cacheprovider").assert_outcomes(
        passed=1
    )


def test_the_environment_configures_a_bench_without_query_options(pytester, monkeypatch):
    """The environment is the fallback (issue 640, issue 676): a bare ``bench.yaml@simubench``
    takes its seed and clock from SIMUBENCH_* and behaves value-for-value like the same
    settings given on the resource string (the plugin injects ``idle=sleep`` either way, D47)."""
    monkeypatch.setenv("SIMUBENCH_SEED", "9")
    monkeypatch.setenv("SIMUBENCH_CLOCK", "command")
    pytester.makepyfile(
        test_case=f"""
        import time, pytest

        @pytest.mark.bench({str(BENCH)!r})
        def test_from_the_environment(instruments, bench_session):
            _observe(bench_session, 9)

        @pytest.mark.bench({str(BENCH)!r}, seed=9, clock="command")
        def test_from_the_resource_string(instruments, bench_session):
            _observe(bench_session, 9)  # the same values, the same behaviour

        def _observe(bench_session, seed):
            assert bench_session.seed == seed
            before = bench_session.now_ns
            t0 = time.perf_counter()
            time.sleep(1.0)
            # the command clock's sleep idle: exactly t of virtual time, without really waiting
            assert time.perf_counter() - t0 < 0.3
            assert bench_session.now_ns - before == 1_000_000_000
        """
    )
    pytester.runpytest_subprocess("--rootdir=.", "test_case.py", "-p", "no:cacheprovider").assert_outcomes(
        passed=2
    )


def test_an_unknown_marker_option_is_named(pytester):
    pytester.makepyfile(
        test_case=f"""
        import pytest

        pytestmark = pytest.mark.bench({str(BENCH)!r}, cloc="wall")

        def test_never(instruments):
            pass
        """
    )
    result = pytester.runpytest_subprocess("--rootdir=.", "test_case.py", "-p", "no:cacheprovider")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*unknown SimuBench option*cloc*"])


def test_a_none_marker_value_means_not_given(pytester, monkeypatch):
    """An option explicitly passed as None is "not given" (issue 675): it is dropped from the
    resource string instead of being encoded as the string "None", so the environment applies."""
    monkeypatch.setenv("SIMUBENCH_SEED", "3")
    pytester.makepyfile(
        test_case=f"""
        import pytest

        pytestmark = pytest.mark.bench({str(BENCH)!r}, seed=None, clock=None)

        def test_env_seed_applies(instruments, bench_session):
            assert bench_session.seed == 3
        """
    )
    pytester.runpytest_subprocess("--rootdir=.", "test_case.py", "-p", "no:cacheprovider").assert_outcomes(
        passed=1
    )


def test_a_question_mark_in_the_bench_path_is_explained(pytester, tmp_path):
    """A bench path containing a literal '?' collides with the query separator (issue 675):
    the error says so, instead of listing a mangled path as an unknown option."""
    tricky = tmp_path / "odd?name"
    if sys.platform == "win32":
        pytest.skip("'?' cannot occur in a Windows file name; the collision is a POSIX-path one")
    tricky.mkdir()
    (tricky / "b.bench.yaml").write_text(BENCH.read_text())
    pytester.makepyfile(
        test_case=f"""
        import pytest

        pytestmark = pytest.mark.bench({str(tricky / "b.bench.yaml")!r})

        def test_never(instruments):
            pass
        """
    )
    result = pytester.runpytest_subprocess("--rootdir=.", "test_case.py", "-p", "no:cacheprovider")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*unknown SimuBench option*name*b.bench.yaml*idle*"])
    result.stdout.fnmatch_lines(["*contains a literal '?'*rename the file or folder*"])
