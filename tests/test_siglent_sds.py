"""Siglent SDS2104X Plus (issue 11): the manual's waveform reading, against the engine.

The manual cases (tests/models/siglent-sds2104xplus/cases.yaml) check command by command; these
tests run the guide's waveform reading procedure (examples/scripts/siglent_sds_read_waveform.py)
and compare what it reconstructs with the signal the bench actually carries.
"""

import importlib.util
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest
import pyvisa
import yaml

ROOT = Path(__file__).resolve().parents[1]
CASES_BENCH = ROOT / "tests" / "models" / "siglent-sds2104xplus" / "bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "siglent_sds_read_waveform.py"


def _script():
    spec = importlib.util.spec_from_file_location("siglent_sds_read_waveform", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


reader = _script()


@pytest.fixture
def scope(tmp_path):
    bench = tmp_path / "bench.yaml"
    shutil.copy(CASES_BENCH, bench)
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    sds = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=5000)
    sds.write(":TIMebase:SCALe 2E-4;:CHANnel1:SCALe 0.5")
    yield sds, rm.visalib.bench
    rm.close()


def _truth(session, rec):
    """The net signal at the record's sample instants (the 100 MHz front end is flat at 1 kHz)."""
    net = session.port_net["scope.CH1"]
    return session.net_signal(net).sample_rel(rec.t_start_ns, np.arange(rec.volts.size) * rec.dt_s)


def _record(session):
    return session.instruments["scope"].components["acq"].records[1]


@pytest.mark.parametrize("resolution", ["8Bits", "10Bits"])
def test_guide_procedure_reconstructs_the_bench_signal(scope, resolution):
    sds, session = scope
    sds.write(f":ACQuire:RESolution {resolution}")
    sds.write(":TRIGger:MODE SINGle")
    times, volts = reader.read_waveform(sds, "C1")
    rec = _record(session)
    assert len(volts) == rec.volts.size == 10_000
    lsb = 0.5 / (30 if resolution == "8Bits" else 120)  # volts per code at 0.5 V/div
    # The codes are the record quantized; the record is the net seen through the front end
    # (100 MHz, 1 MOhm || 17 pF): at 1 kHz that is a few microvolts.
    assert np.max(np.abs(np.array(volts) - rec.volts)) <= lsb / 2 + 1e-9
    assert np.max(np.abs(rec.volts - _truth(session, rec))) < 5e-5
    # Time axis: delay 0 puts the trigger (rising zero crossing of the sine) at t = 0.
    assert times[0] == pytest.approx(-5 * 2e-4)
    i0 = int(np.argmin(np.abs(np.array(times))))
    assert abs(volts[i0]) <= 2 * lsb and volts[i0 + 20] > volts[i0 - 20]


def test_byte_and_word_carry_the_same_codes(scope):
    sds, _ = scope
    sds.write(":TRIGger:MODE SINGle;:WAVeform:SOURce C1;:WAVeform:WIDTh BYTE")
    as_bytes = sds.query_binary_values(":WAVeform:DATA?", datatype="b", container=np.array)
    sds.read_bytes(1)
    sds.write(":WAVeform:WIDTh WORD")
    words = sds.query_binary_values(":WAVeform:DATA?", datatype="h", container=np.array)
    sds.read_bytes(1)
    assert np.array_equal(words >> 8, as_bytes)


def test_start_point_and_interval_select_the_transferred_points(scope):
    sds, _ = scope
    sds.write(":TRIGger:MODE SINGle;:WAV:SOUR C1;:WAV:WIDT BYTE")
    full = sds.query_binary_values(":WAVeform:DATA?", datatype="b", container=np.array)
    sds.read_bytes(1)
    sds.write(":WAVeform:STARt 100;:WAVeform:POINt 50;:WAVeform:INTerval 3")
    part = sds.query_binary_values(":WAVeform:DATA?", datatype="b", container=np.array)
    sds.read_bytes(1)
    assert np.array_equal(part, full[100 : 100 + 150 : 3])


def test_measurements_match_the_waveform(scope):
    sds, _ = scope
    sds.write(":TIMebase:SCALe 1E-3;:TRIGger:MODE SINGle")
    _, volts = reader.read_waveform(sds, "C1")
    pkpk = float(sds.query(":MEASure:SIMPle:VALue? PKPK"))
    freq = float(sds.query(":MEASure:SIMPle:VALue? FREQ"))
    rms = float(sds.query(":MEASure:SIMPle:VALue? RMS"))
    v = np.array(volts)
    assert pkpk == pytest.approx(v.max() - v.min(), abs=0.02)  # 3 digits, codes of 16.7 mV
    assert pkpk == pytest.approx(2.0, abs=0.01)
    assert freq == pytest.approx(1000.0, rel=5e-3)
    assert rms == pytest.approx(2.0 / 2 / np.sqrt(2), rel=5e-3)


def test_settings_out_of_the_data_sheet_ranges_are_refused(scope):
    sds, session = scope
    sds.write(":CHANnel1:SCALe 20")  # 1 MOhm: at most 10 V/div at the input
    assert sds.query(":CHANnel1:SCALe?").strip() == "5.00E-01"
    sds.write(":CHANnel1:PROBe VALue,10;:CHANnel1:SCALe 20")  # 2 V/div at the input: fine
    assert sds.query(":CHANnel1:SCALe?").strip() == "2.00E+01"
    sds.write(":CHANnel1:PROBe DEFault;:CHANnel1:OFFSet 5")  # 2 V/div: offset up to +-20 V
    assert sds.query(":CHANnel1:OFFSet?").strip() == "5.00E+00"
    sds.write(":CHANnel1:SCALe 0.05")  # 50 mV/div allows only +-2 V of offset: refused
    assert sds.query(":CHANnel1:SCALe?").strip() == "2.00E+00"
    sds.write(":TRIGger:EDGE:LEVel 50")  # +-4.1 div around the offset
    assert sds.query(":TRIGger:EDGE:LEVel?").strip() == "0.00E+00"
    sds.write(":TIMebase:DELay 1")  # at most 5 div
    assert sds.query(":TIMebase:DELay?").strip() == "0.00E+00"
    refused = [e for e in session.history if e.get("kind") == "error" and e["code"] == -222]
    assert len(refused) == 3 + 1  # scale, scale, level, delay


def test_only_the_common_commands_of_the_manual_answer(scope):
    sds, session = scope
    sds.write("*CLS")
    sds.write(":SYSTem:ERRor?")
    errors = [e for e in session.history if e.get("kind") == "error"]
    assert [e.get("reason") for e in errors[-2:]] == ["not_modeled", "not_modeled"]
    assert sds.query("*OPC?").strip() == "1"  # and nothing was queued for the script to read


def test_example_bench_and_script_run_as_on_the_real_bench(tmp_path, capsys, monkeypatch):
    """The guide's procedure on the example bench, addressed by the real bench's VISA address."""
    data = yaml.safe_load((ROOT / "examples" / "benches" / "siglent_scope_rc.bench.yaml").read_text("utf-8"))
    data["instruments"]["awg"]["state"]["ch1.output"] = True  # outputs are off at power-on
    bench = tmp_path / "siglent_scope_rc.bench.yaml"
    bench.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["x", "TCPIP0::192.168.1.31::INSTR", "C1", "--bench", str(bench)])
    reader.main()
    out = capsys.readouterr().out
    # 2 Vpp is set for a 50 Ohm load (D13): the high-impedance RC sees 4 Vpp, passes 0.847 of it at
    # 1 kHz, and the 10x probe divides by 10 while the scope still assumes 1X.
    assert out.startswith("10000 points")
    lo, hi = (float(x.split()[0]) for x in out.split(" s, ")[1].split(" to "))
    assert hi - lo == pytest.approx(4 * 0.847 / 10, rel=0.03)
