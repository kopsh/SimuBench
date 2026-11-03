"""Drive a Keysight 33622A the way the Trueform Operating and Service Guide's examples do, and check
it with a Keysight DSOX2024A.

Channel 1 gets ``APPLy:SINusoid`` (10 kHz, 2 Vpp into the high-impedance load setting), channel 2
``APPLy:SQUare``; a scope's 1 MΩ input reads them. Then an arbitrary waveform is loaded by name
(``DATA:ARBitrary``), selected (``FUNCtion:ARBitrary``) and played with ``APPLy:ARBitrary`` at a sample
rate that makes it 10 kHz, and finally the guide's AM example (``AM:DEPTh``, ``AM:INTernal:FREQuency``,
``AM:STATe ON``) puts 100 % AM on the sine. The scope measures frequency and peak-to-peak each time.

The same script runs against the real instruments or a SimuBench bench:

    python keysight_33622a_waves.py TCPIP0::192.168.1.50::inst0::INSTR TCPIP0::192.168.1.51::inst0::INSTR
    python keysight_33622a_waves.py awg scope --bench examples/benches/keysight-33622a.bench.yaml
"""

from __future__ import annotations

import argparse
import math

import pyvisa

#: One period of a triangle in eight points, -1..+1 (the DATA:ARBitrary value range).
TRIANGLE8 = (0.0, 0.5, 1.0, 0.5, 0.0, -0.5, -1.0, -0.5)


def measure(scope, channel: int) -> tuple[float, float]:
    """The scope's frequency and peak-to-peak of one channel, after one acquisition."""
    scope.write(f":DIGitize CHANnel{channel}")
    freq = float(scope.query(f":MEASure:FREQuency? CHANnel{channel}"))
    vpp = float(scope.query(f":MEASure:VPP? CHANnel{channel}"))
    return freq, vpp


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("awg", help="VISA resource of the 33622A (or its name on a SimuBench bench)")
    ap.add_argument("scope", help="VISA resource of the DSOX2024A (or its name on the bench)")
    ap.add_argument("--bench", help="run on a SimuBench bench file instead of real instruments")
    args = ap.parse_args()
    rm = pyvisa.ResourceManager(f"{args.bench}@simubench" if args.bench else "")
    awg = rm.open_resource(args.awg, read_termination="\n", write_termination="\n", timeout=10000)
    scope = rm.open_resource(args.scope, read_termination="\n", write_termination="\n", timeout=10000)
    results: dict[str, tuple[float, float]] = {}
    try:
        print(awg.query("*IDN?").strip())
        awg.write("*RST")
        awg.write("*CLS")
        scope.write("*RST")
        for ch in (1, 2):
            scope.write(f":CHANnel{ch}:PROBe 1")
            scope.write(f":CHANnel{ch}:SCALe 0.5")
        scope.write(":TIMebase:SCALe 100E-6")

        # Standard waves with APPLy (it turns the output on); amplitudes for an open-circuit load.
        awg.write("OUTPut1:LOAD INF")
        awg.write("OUTPut2:LOAD INF")
        awg.write("SOURce1:APPLy:SINusoid 1E4,2,0")
        awg.write("SOURce2:APPLy:SQUare 5E3,1,0")
        print("ch1: " + awg.query("SOURce1:APPLy?").strip())
        results["sine"] = measure(scope, 1)
        results["square"] = measure(scope, 2)

        # An arbitrary waveform by name: 8 points at 80 kSa/s is a 10 kHz triangle.
        awg.write("SOURce1:DATA:ARBitrary tri8," + ",".join(f"{v:g}" for v in TRIANGLE8))
        awg.write("SOURce1:FUNCtion:ARBitrary tri8")
        awg.write("SOURce1:APPLy:ARBitrary 8E4,2,0")
        print("arb points: " + awg.query("SOURce1:FUNCtion:ARBitrary:POINts?").strip())
        results["arb"] = measure(scope, 1)

        # The guide's AM example on the sine: 100 % depth at 500 Hz doubles the peak-to-peak.
        awg.write("SOURce1:APPLy:SINusoid 1E4,1,0")
        awg.write("SOURce1:AM:DEPTh 100")
        awg.write("SOURce1:AM:INTernal:FREQuency 500")
        awg.write("SOURce1:AM:STATe ON")
        scope.write(":TIMebase:SCALe 500E-6")
        results["am"] = measure(scope, 1)

        for name, (freq, vpp) in results.items():
            print(f"{name}: {freq:.6g} Hz {vpp:.4g} Vpp")
        error = awg.query("SYSTem:ERRor?").strip()
        print(error)
    finally:
        awg.close()
        scope.close()
        rm.close()

    assert math.isclose(results["sine"][0], 1e4, rel_tol=1e-3), results
    assert math.isclose(results["sine"][1], 2.0, rel_tol=0.03), results
    assert math.isclose(results["square"][0], 5e3, rel_tol=1e-3), results
    assert 0.95 < results["square"][1] < 1.2, results  # a band-limited square overshoots a little
    assert math.isclose(results["arb"][0], 1e4, rel_tol=1e-3), results
    assert math.isclose(results["arb"][1], 2.0, rel_tol=0.05), results
    assert math.isclose(results["am"][1], 2.0, rel_tol=0.05), results  # (1 + m) x 1 Vpp
    assert error.startswith("+0"), error


if __name__ == "__main__":
    main()
