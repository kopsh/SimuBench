"""Why a command was not executed: the event carries the classification, the file the declaration
comes from and the closest command of the author's command space (tui-design.md 1.1, D18).

One test per category; a built-in real model (with a coverage table) and a workspace definition
(``sb-*``, without one) both appear. The reply text is #172's business and stays as it is here.
"""

from pathlib import Path

import yaml

from simubench.api import open_bench
from simubench.library.coverage import load_coverage

ROOT = Path(__file__).resolve().parents[1]


def _open(tmp_path, model: str):
    """A bench with a single instrument of the given model, on the command clock."""
    bench = tmp_path / "bench.yaml"
    bench.write_text(
        yaml.safe_dump({"name": "classification", "instruments": {"dut": {"model": model}}}),
        encoding="utf-8",
    )
    return open_bench(bench, clock="command")


def _last(session, kind: str = "error") -> dict:
    return [e for e in session.history if e.get("kind") == kind][-1]


def test_a_command_the_coverage_marks_not_modeled(tmp_path):
    """覆盖表中标为未实现的命令：not_modeled，出处是覆盖表文件（内置真实型号）。"""
    s = _open(tmp_path, "siglent-sds2104xplus")
    try:
        s.write("dut", b":AUToset\n")
        e = _last(s)
        assert e["code"] == -113
        assert e["reason"] == "not_modeled"
        assert e["origin"].endswith("siglent-sds2104xplus.coverage.yaml")
        assert "not modeled by SimuBench" in e["message"]  # the reply text stays (#172)
    finally:
        s.close()


def test_a_command_the_coverage_does_not_list(tmp_path):
    """覆盖表中没有的命令：not_in_model，附命令树中最接近的命令（内置真实型号）。"""
    s = _open(tmp_path, "siglent-sds2104xplus")
    try:
        s.write("dut", b":ACQuire:DEPMoth 1200\n")
        e = _last(s)
        assert e["code"] == -113
        assert e["reason"] == "not_in_model"
        assert e["origin"].endswith("siglent-sds2104xplus.coverage.yaml")
        # The closest command comes from the author's command space (coverage + definition).
        definition = yaml.safe_load(
            (ROOT / "src/simubench/library/instruments/siglent-sds2104xplus.yaml").read_text("utf-8")
        )
        cov = load_coverage(ROOT / "src/simubench/library/instruments/siglent-sds2104xplus.coverage.yaml")
        space = {c["header"] for c in definition["commands"]} | {entry.header for _, entry in cov.entries()}
        assert e["closest"] in space
        assert e["closest"] == ":ACQuire:MDEPth"
    finally:
        s.close()


def test_a_command_the_definition_does_not_have(tmp_path):
    """无覆盖表、定义中也没有的命令：not_in_definition，出处是定义文件（工作区定义）。"""
    s = _open(tmp_path, "sb-smu-1")
    try:
        s.write("dut", b":SOUR:VOLTge 5\n")
        e = _last(s)
        assert e["code"] == -113
        assert e["reason"] == "not_in_definition"
        assert e["origin"].endswith("sb-smu-1.yaml")
        assert e["closest"] == "[:SOURce]:VOLTage[:LEVel][:IMMediate][:AMPLitude]"
    finally:
        s.close()


def test_an_ignored_command_names_its_header(tmp_path):
    """unknown_commands: ignore 的定义：事件记为 ignored，带头与出处（工作区定义）。"""
    s = open_bench(ROOT / "examples/lowfreq-cal/lowfreq_cal.bench.yaml")
    try:
        s.write("scope", b"C1:FOO 3\n")
        e = _last(s, "ignored")
        assert e["reason"] == "ignored"
        assert e["header"] == "C1:FOO"
        assert e["query"] is False
        assert e["origin"].endswith("lfcal-reference-scope.yaml")
        assert [x for x in s.history if x.get("kind") == "error"] == []  # not an error, and no reply
    finally:
        s.close()


def test_a_builtin_the_definition_leaves_out(tmp_path):
    """builtins 之外的公共命令：builtin_excluded 而不是 not_modeled（内置真实型号）。"""
    s = _open(tmp_path, "fluke-5522a")
    try:
        s.write("dut", b"*TRG\n")  # in the manual as a thermocouple measurement; not modeled
        e = _last(s)
        assert e["code"] == -113
        assert e["reason"] == "builtin_excluded"
        assert e["origin"].endswith("fluke-5522a.yaml")
        assert "not modeled by SimuBench" in e["message"]  # the reply text stays (#172)
    finally:
        s.close()


def test_a_parameter_out_of_range_names_the_declared_range(tmp_path):
    """参数超范围：bad_parameter，附参数与定义给出的范围（工作区定义、内置真实型号）。"""
    s = _open(tmp_path, "sb-smu-1")
    try:
        s.write("dut", b":SOUR:VOLT 300\n")
        e = _last(s)
        assert e["code"] == -222
        assert e["reason"] == "bad_parameter"
        assert e["param"] == "source.voltage.level"
        assert e["range"] == "-210 … 210 V"
        assert e["origin"].endswith("sb-smu-1.yaml")
    finally:
        s.close()
    s = _open(tmp_path, "siglent-sds2104xplus")
    try:
        s.write("dut", b":CHANnel1:SCALe 50\n")  # at most 10 V/div at the input (front end)
        e = _last(s)
        assert e["code"] == -222 and e["reason"] == "bad_parameter"
        assert e["param"] == "ch1.scale"
    finally:
        s.close()
