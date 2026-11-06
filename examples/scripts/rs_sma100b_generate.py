"""Generate RF signals with an R&S SMA100B and measure them on an oscilloscope.

The generator flow follows the SMA100B User Manual (1178.3834.02) Chapter 15 examples: ``*IDN?`` to
check the link, ``*RST`` for a known state, ``SOURce1:FREQuency:CW`` and ``SOURce1:POWer`` for the
carrier, ``SOURce1:AM1:...`` with ``SOURce1:LFOutput1:FREQuency`` and the master switch
``SOURce1:MODulation:ALL:STATe`` for amplitude modulation ("Creating an amplitude modulated RF
signal"), and ``:OUTPut1`` to turn the RF on. The oscilloscope reads the carrier's peak-to-peak
voltage (the 50 Ω output into the scope's 1 MΩ input is the open-circuit EMF, so 0 dBm appears as
1.26 Vpp), the 10× per 20 dB level step, and the 1 + m envelope of AM.

The script runs unchanged against a real SMA100B and a real DSO-X 2024A, or against a SimuBench
bench with both models:

    python rs_sma100b_generate.py TCPIP0::192.168.1.60::hislip0::INSTR --scope TCPIP0::192.168.1.32::INSTR
    python rs_sma100b_generate.py sg --scope scope --bench examples/benches/rs-sma100b.bench.yaml
"""

from __future__ import annotations

import argparse
import math

import pyvisa

Z0 = 50.0


def vpp_into_high_z(dbm: float) -> float:
    """Peak-to-peak open-circuit voltage of a source defined for ``dbm`` into a matched 50 Ω load."""
    return 4 * math.sqrt(2 * Z0 * 1e-3 * 10 ** (dbm / 10))


def vpp(scope, channel: str = "CHANnel1") -> float:
    """One acquisition of ``channel`` and its peak-to-peak voltage."""
    scope.write(f":DIGitize {channel}")
    scope.write(f":MEASure:SOURce {channel}")
    return float(scope.query(":MEASure:VPP?"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("resource", help="generator VISA resource (or its name on a SimuBench bench)")
    ap.add_argument("--scope", required=True, help="oscilloscope VISA resource (or its bench name)")
    ap.add_argument("--bench", help="run against this SimuBench bench instead of the instruments")
    args = ap.parse_args()

    rm = pyvisa.ResourceManager(f"{args.bench}@simubench" if args.bench else "")
    sg = rm.open_resource(args.resource, read_termination="\n", write_termination="\n", timeout=10000)
    scope = rm.open_resource(args.scope, read_termination="\n", write_termination="\n", timeout=10000)
    try:
        print(sg.query("*IDN?").strip())
        print(scope.query("*IDN?").strip())

        # Each reading gets a range that fits the signal in about six of the screen's eight
        # divisions, so the range's own noise stays a few percent of it (#774).
        sg.write("*RST")
        scope.write("*RST")
        scope.write(":CHANnel1:PROBe 1")
        scope.write(":TIMebase:SCALe 2E-7")
        sg.write("SOURce1:FREQuency:CW 10 MHz")
        sg.write("SOURce1:POWer:LEVel:IMMediate:AMPLitude 0")
        sg.write(":OUTPut1:STATe ON")

        scope.write(":CHANnel1:SCALe 0.2")  # 1.26 Vpp spans 6.3 div; 4 mV rms of range noise
        carrier = vpp(scope)
        carrier_frequency = float(scope.query(":MEASure:FREQuency?"))

        # 20 dB down is a tenth of the voltage.
        sg.write("SOURce1:POWer -20")
        scope.write(":CHANnel1:SCALe 0.02")  # 126 mVpp spans 6.3 div; 0.4 mV rms of range noise
        low = vpp(scope)
        scope.write(":CHANnel1:SCALe 0.5")  # the 1.9 Vpp envelope fits; the noise is 4 % of it
        sg.write("SOURce1:POWer 0")

        # 50 % AM from the LF1 generator at 10 kHz; the master switch must be on as well.
        sg.write("SOURce1:AM1:DEPTh 50")
        sg.write("SOURce1:AM1:SOURce LF1")
        sg.write("SOURce1:LFOutput1:FREQuency 10 kHz")
        sg.write("SOURce1:AM1:STATe 1")
        sg.write("SOURce1:MODulation:ALL:STATe 1")
        scope.write(":TIMebase:SCALe 2E-5")
        modulated = vpp(scope)
        sg.write("SOURce1:AM1:STATe 0")
        scope.write(":TIMebase:SCALe 2E-7")

        error = sg.query(":SYSTem:ERRor?").strip()
        scope_error = scope.query(":SYSTem:ERRor?").strip()
    finally:
        sg.close()
        scope.close()
        rm.close()

    expected = vpp_into_high_z(0.0)
    print(f"carrier: {carrier_frequency / 1e6:.6f} MHz, {carrier:.4f} Vpp into 1 MOhm")
    print(f"expected for 0 dBm: {expected:.4f} Vpp")
    print(f"20 dB down: {low:.4f} Vpp ({carrier / low:.2f}x)")
    print(f"AM 50 %: {modulated:.4f} Vpp ({modulated / carrier:.2f}x)")
    print(error)
    print(scope_error)

    assert error.startswith("0"), error
    # the crossings ride the range table's noise and the codes' interpolation (#745):
    # some 0.15 % on the LSQ period at 10 MHz
    assert abs(carrier_frequency - 10e6) < 25.0e3, carrier_frequency
    assert abs(carrier - expected) <= 0.05 * expected, carrier
    # each reading's VPP carries its own range's noise peak-to-peak (#745): a few % of the
    # reading, near-equal on both, so the 10x of 20 dB holds to a few parts in a hundred (#774)
    assert abs(carrier / low - 10.0) <= 0.2, (carrier, low)
    assert abs(modulated / carrier - 1.5) <= 0.05, (modulated, carrier)


if __name__ == "__main__":
    main()
