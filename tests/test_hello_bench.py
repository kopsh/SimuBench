"""The packaged Hello World bench: copied into a workspace once, then it is the user's file; on the
engine it shows one square wave in the time and the frequency domain without a warning on screen
(no mixer overload, a measurable frequency)."""

from __future__ import annotations

import math
import statistics

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
        awg = rm.open_resource("awg", read_termination="\n", write_termination="\n")
        scope = rm.open_resource("scope", read_termination="\n", write_termination="\n")
        sa = rm.open_resource("sa", read_termination="\n", write_termination="\n")

        # Time domain: five periods on screen, so the scope's own counter measures 1 MHz; the UXR's
        # 50 ohm input sees the generator's 1 Vpp (its load setting is 50 ohms).
        freq = float(scope.query(":MEASure:FREQuency? CHANnel1"))
        assert math.isclose(freq, 1.0e6, rel_tol=1e-3), freq
        amplitude = float(scope.query(":MEASure:VAMPlitude? CHANnel1"))
        assert math.isclose(amplitude, 1.0, rel_tol=0.02), amplitude
        assert scope.query(":SYSTem:ERRor?").strip() == "0"

        # The analyzer's 50 ohm input gets the same 1 Vpp (CH2's load setting is 50 ohms too, #1220):
        # the fundamental of a 1 Vpp square into 50 ohms is (4/pi * 0.5 V)^2 / 2 / 50 ohm = +6.1 dBm,
        # and the marker the bench sets reads it at 1 MHz.
        assert float(sa.query(":CALC:MARK1:X?")) == 1.0e6
        assert abs(float(sa.query(":CALC:MARK1:Y?")) - 6.1) < 0.5

        # Frequency domain: the odd harmonics fall as 1/n (0, -9.5, -14 dB), the even ones are absent.
        trace = [float(v) for v in sa.query(":TRAC:DATA? TRACE1").split(",")]

        def at(mhz: float) -> float:
            i = round(mhz / 8 * (len(trace) - 1))
            return max(trace[i - 3 : i + 4])

        assert abs(at(1) - 6.1) < 1.0, at(1)
        assert abs((at(1) - at(3)) - 9.54) < 1.0, at(3)
        assert abs((at(1) - at(5)) - 13.98) < 1.0, at(5)
        # The even harmonics are absent: at 2 MHz there is only the analyzer's noise. The noise is
        # judged by its median on either side (the N9320B's floor at 30 dB attenuation and 100 kHz
        # RBW, 55-60 dB under the fundamental); peak-detected, the 7 bins around 2 MHz reach up to
        # 11.3 dB over that median (2000 sweeps), so a line is what stands 16 dB over it. A fixed
        # "50 dB below the fundamental" was inside the noise peaks and failed now and then.
        noise = trace[round(1.5 / 8 * (len(trace) - 1)) : round(1.9 / 8 * (len(trace) - 1))]
        noise += trace[round(2.1 / 8 * (len(trace) - 1)) : round(2.5 / 8 * (len(trace) - 1))]
        floor = statistics.median(noise)
        assert floor < at(1) - 50, floor
        assert at(2) < floor + 16, (at(2), floor)

        # No warning on the first screen: the reference level leaves headroom, attenuation follows.
        assert float(sa.query(":SENS:POW:RF:ATT?")) == 30.0
        assert sa.query(":SYST:ERR?").startswith("+0")

        # The source is symmetric: both channels run with their load setting at 50 ohms and the same
        # waveform settings, so each instrument really does see the same 1 Vpp on its 50 ohm input
        # (#1225). If a load fell back to HZ (high impedance), the amplitude on that input would double.
        outp1 = awg.query("C1:OUTP?")
        outp2 = awg.query("C2:OUTP?")
        assert outp1 == "C1:OUTP ON,LOAD,50,PLRT,NOR", outp1
        assert outp2 == "C2:OUTP ON,LOAD,50,PLRT,NOR", outp2
        bswv1 = awg.query("C1:BSWV?")
        bswv2 = awg.query("C2:BSWV?")
        assert bswv1.removeprefix("C1:BSWV ") == bswv2.removeprefix("C2:BSWV "), (bswv1, bswv2)
        assert "AMP,1V," in bswv1, bswv1
    finally:
        rm.close()
