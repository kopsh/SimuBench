"""Setup memory (#1070): a setup holds the settings a command can change and puts them back exactly;
read-only variables and drawn individual values are never in it; registers outlive *RST and runs."""

from __future__ import annotations

import math

from simubench.api import open_bench
from simubench.library.components.setups import decode_setup, encode_setup
from simubench.models.bench import BenchDefinition


def bench(model: str = "keysight-33622a", **extra) -> BenchDefinition:
    return BenchDefinition.model_validate(
        {"schema": "simubench.bench/2", "name": "t", "instruments": {"dut": {"model": model, **extra}}}
    )


def ask(s, text: str) -> bytes | None:
    s.write("dut", text.encode() + b"\n")
    return s.read("dut") if "?" in text.split()[0] else None


def test_snapshot_change_restore_gives_every_setting_back():
    with open_bench(bench()) as s:
        inst = s.instrument("dut")
        ask(s, "APPL:SQU 5e3,2,0.5")
        ask(s, "SOUR2:FREQ 777")
        saved = inst.settings_snapshot()
        ask(s, "*RST")
        assert inst.settings_snapshot() != saved
        inst.restore_settings(saved)
        assert inst.settings_snapshot() == saved


def test_read_only_variables_are_not_in_a_setup():
    with open_bench(bench("keysight-34465a")) as s:
        inst = s.instrument("dut")
        readonly = {p for p, spec in inst.specs.items() if spec.readonly}
        assert readonly and not readonly & set(inst.settings_snapshot())


def test_a_drawn_individual_value_is_not_in_a_setup():
    with open_bench(bench("sb-awg-2", deviation="from_specs")) as s:
        inst = s.instrument("dut")
        drawn = s.deviations()
        assert drawn  # the bench did draw values
        assert set(inst.settings_snapshot()) <= set(inst.specs)
        assert not any(key.split(".", 1)[-1] in inst.settings_snapshot() for key in drawn)


def test_a_setup_of_another_model_or_garbage_changes_nothing():
    with open_bench(bench()) as s:
        ask(s, "FREQ 1234")
        before = s.instrument("dut").settings_snapshot()
        ask(s, 'MMEM:DOWN:FNAM "INT:\\other.sta"')
        ask(s, "MMEM:DOWN:DATA #211not a setup")
        ask(s, 'MMEM:LOAD:STAT "INT:\\other.sta"')
        assert ask(s, "SYST:ERR?") == b'+292,"State file size error"\n'
        other = encode_setup("keysight-uxr0334b", {"timebase.scale": 1e-9})
        ask(s, 'MMEM:DOWN:FNAM "INT:\\uxr.sta"')
        s.write(
            "dut",
            b"MMEM:DOWN:DATA #"
            + str(len(str(len(other)))).encode()
            + str(len(other)).encode()
            + other
            + b"\n",
        )
        ask(s, 'MMEM:LOAD:STAT "INT:\\uxr.sta"')
        assert ask(s, "SYST:ERR?").startswith(b"+292")
        assert s.instrument("dut").settings_snapshot() == before


def test_registers_survive_rst_and_the_next_run(tmp_path):
    with open_bench(bench(), files_dir=tmp_path) as s:
        ask(s, "FREQ 4321")
        ask(s, "*SAV 2")
        ask(s, "*RST")
        assert ask(s, "MEM:STAT:VAL? 2") == b"1\n"
    assert (tmp_path / "dut" / "INT" / "Settings" / "STATE_2.STA").is_file()
    with open_bench(bench(), files_dir=tmp_path) as s:
        ask(s, "*RCL 2")
        assert ask(s, "FREQ?") == b"+4.321000000000000E+03\n"


def test_internal_registers_are_kept_beside_the_drives(tmp_path):
    with open_bench(bench("keysight-uxr0334b"), files_dir=tmp_path) as s:
        ask(s, ":TIMebase:SCALe 2E-9")
        ask(s, "*SAV 7")
        assert ask(s, ':DISK:DIRectory? "C:\\"') == b"1\nUsers\\\n"  # not on the scope's drive
    assert (tmp_path / "dut" / "_registers" / "7.json").is_file()


def test_infinity_survives_the_setup_format():
    data = encode_setup("m", {"ch1.load": math.inf, "x": 1.5, "w": "SQU"})
    assert decode_setup(data, "m") == {"ch1.load": math.inf, "x": 1.5, "w": "SQU"}
    assert decode_setup(data, "other") is None and decode_setup(b"\xff", "m") is None


def test_the_learn_string_sent_back_restores_the_setup():
    with open_bench(bench("keysight-uxr0334b")) as s:
        ask(s, ":TIMebase:SCALe 2E-9")
        learn = ask(s, "*LRN?")
        ask(s, "*RST")
        s.write("dut", learn)
        assert ask(s, ":TIMebase:SCALe?") == b"+2.00000E-09\n"
        assert ask(s, ":SYSTem:ERRor?") == b"0\n"
