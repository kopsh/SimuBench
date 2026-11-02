"""Generate RF signals with a Rigol DSG815 and measure them on an oscilloscope.

The generator flow follows the DSG800 Programming Guide (PGG02104-1110) Chapter 3's application
examples: ``*IDN?`` to check the link, ``:SYSTem:PRESet`` for a known state, ``:FREQuency`` and
``:LEVel`` for the carrier, ``:AM:...`` for amplitude modulation and ``:OUTPut`` to turn the RF on.
The oscilloscope is the bench's stand-in for a spectrum analyzer or power meter: it reads the
carrier's peak-to-peak voltage (the generator's 50 Ω output into the scope's 1 MΩ input is the
open-circuit EMF, so 0 dBm appears as 1.26 Vpp, not the 0.63 Vpp it delivers into a matched load),
the 10× per 20 dB level step, and the 1 + m envelope of AM.

The script runs unchanged against a real DSG815 and a real DSO-X 2024A, or against a SimuBench
bench with both models:

    python rigol_dsg815_generate.py TCPIP0::192.168.1.50::INSTR --scope TCPIP0::192.168.1.32::INSTR
    python rigol_dsg815_generate.py sg --scope scope --bench examples/benches/rigol_dsg815.bench.yaml
"""

from __future__ import annotations

import argparse
import math

import pyvisa

Z0 = 50.0


def vpp_into_high_z(dbm: float) -> float:
    """Peak-to-peak open-circuit voltage of a source defined for ``dbm`` into a matched 50 Ω load."""
    emf_peak = 2 * math.sqrt(2 * Z0 * 1e-3 * 10 ** (dbm / 10))
    return 2 * emf_peak


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

        # A known state on both instruments, then the carrier at 10 MHz, 0 dBm (Chapter 3:
        # "To Output RF signal").
        sg.write("*RST")
        scope.write("*RST")
        scope.write(":CHANnel1:PROBe 1")
        scope.write(":CHANnel1:SCALe 0.5")
        scope.write(":TIMebase:SCALe 2E-7")
        sg.write(":FREQuency 10MHz")
        sg.write(":LEVel 0")
        sg.write(":OUTPut ON")

        carrier = vpp(scope)
        carrier_frequency = float(scope.query(":MEASure:FREQuency?"))

        # 20 dB down is a tenth of the voltage.
        sg.write(":LEVel -20")
        low = vpp(scope)
        sg.write(":LEVel 0")

        # 50 % AM: the envelope's peak is 1 + m = 1.5 times the carrier. As in the manual's example,
        # the AM switch and the master Mod/on switch must both be on.
        sg.write(":AM:DEPTh 50")
        sg.write(":AM:FREQuency 10kHz")
        sg.write(":AM:STATe ON")
        sg.write(":MODulation:STATe ON")
        scope.write(":TIMebase:SCALe 2E-5")
        modulated = vpp(scope)
        sg.write(":AM:STATe OFF")
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
    assert abs(carrier_frequency - 10e6) < 1.0, carrier_frequency
    assert abs(carrier - expected) <= 0.05 * expected, carrier
    assert abs(carrier / low - 10.0) <= 0.2, (carrier, low)
    assert abs(modulated / carrier - 1.5) <= 0.05, (modulated, carrier)


if __name__ == "__main__":
    main()
