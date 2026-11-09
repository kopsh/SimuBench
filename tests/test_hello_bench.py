"""The packaged Hello World bench: copied into a workspace once, then it is the user's file; on the
engine it shows one square wave in the time and the frequency domain without a warning on screen
(no mixer overload, a measurable frequency)."""

from __future__ import annotations

import math

import pyvisa

from simubench.server.benchfiles import HELLO, BenchFiles


def test_hello_is_copied_once_and_never_overwritten(tmp_path):
    files = BenchFiles(tmp_path)
    doc = files.create_hello()
    assert doc.path == "hello.bench.yaml"
    assert (tmp_path / "hello.bench.yaml").read_bytes() == HELLO.read_bytes()  # comments kept
    assert [i for i in doc.data["instruments"]] == ["awg", "scope", "sa"]
    assert len(files.versions("hello.bench.yaml")) == 1  # the copy is the first version (D35)

    (tmp_path / "hello.bench.yaml").write_text("schema: simubench.bench/2\nname: mine\n", encoding="utf-8")
    again = files.create_hello()
    assert again.data["name"] == "mine"  # the user's edit survives a second click


def test_hello_runs_clean_in_both_domains(tmp_path):
    (tmp_path / "hello.bench.yaml").write_bytes(HELLO.read_bytes())
    rm = pyvisa.ResourceManager(f"{tmp_path / 'hello.bench.yaml'}@simubench")
    try:
        scope = rm.open_resource("scope", read_termination="\n", write_termination="\n")
        sa = rm.open_resource("sa", read_termination="\n", write_termination="\n")

        # Time domain: five periods on screen, so the scope's own counter measures 1 MHz.
        freq = float(scope.query(":MEASure:SIMPle:VALue? FREQ"))
        assert math.isclose(freq, 1.0e6, rel_tol=1e-3), freq

        # Frequency domain: the odd harmonics fall as 1/n (0, -9.5, -14 dB), the even ones are absent.
        trace = [float(v) for v in sa.query(":TRAC:DATA? TRACE1").split(",")]

        def at(mhz: float) -> float:
            i = round(mhz / 8 * (len(trace) - 1))
            return max(trace[i - 3 : i + 4])

        assert abs(at(1) - 0.0) < 1.0, at(1)
        assert abs((at(1) - at(3)) - 9.54) < 1.0, at(3)
        assert abs((at(1) - at(5)) - 13.98) < 1.0, at(5)
        assert at(2) < at(1) - 50, at(2)

        # No warning on the first screen: the reference level leaves headroom, attenuation follows.
        assert float(sa.query(":SENS:POW:RF:ATT?")) == 30.0
        assert sa.query(":SYST:ERR?").startswith("+0")
    finally:
        rm.close()
