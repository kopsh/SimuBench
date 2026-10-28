"""Real models (D42, docs/real-models.md): manual cases, coverage tables and the shared framework.

Every ``tests/models/<id>/cases.yaml`` runs here through the PyVISA backend, one test per case,
named after the manual section it checks. Every real-model definition (one with a ``reference``)
must agree with its coverage table and its cases.
"""

import re
import shutil
from pathlib import Path

import pytest
import pyvisa
import yaml

from simubench.cli import main
from simubench.library.coverage import Coverage, check, coverage_path, load_cases, load_coverage
from simubench.library.loader import BUILTIN_DIR, DefinitionLibrary
from simubench.scpi import values as V
from simubench.scpi.parse import Pattern, parse_unit

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"
CASE_FILES = sorted(MODELS.glob("*/cases.yaml"))


def definition_path(model_id: str) -> Path:
    """A real model's definition: in the product library, or next to its cases (framework tests)."""
    return DefinitionLibrary([MODELS / model_id]).path(model_id)


def all_cases():
    for path in CASE_FILES:
        cases = load_cases(path)
        for k, case in enumerate(cases.cases, 1):
            # The id names the manual section (ASCII in practice); the case name is in the messages.
            yield pytest.param(path, cases, case, id=f"{cases.model} #{k} [{case.manual}]")


def open_instrument(tmp_path: Path, cases_file: Path, cases):
    """A fresh bench with the instrument, opened through the PyVISA backend like a real script."""
    if cases.bench:
        src = (cases_file.parent / cases.bench).resolve()
        bench = tmp_path / src.name
        shutil.copy(src, bench)
        if (src.parent / "instruments").is_dir():
            shutil.copytree(src.parent / "instruments", tmp_path / "instruments")
    else:
        bench = tmp_path / "case.bench.yaml"
        bench.write_text(
            yaml.safe_dump({"name": "case", "instruments": {cases.instrument: {"model": cases.model}}}),
            encoding="utf-8",
        )
    defn = definition_path(cases.model)
    if defn.parent != BUILTIN_DIR:
        (tmp_path / "instruments").mkdir(exist_ok=True)
        shutil.copy(defn, tmp_path / "instruments" / defn.name)
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    return rm, rm.open_resource(cases.instrument, read_termination="\n", write_termination="\n", timeout=2000)


def read_block(inst) -> bytes:
    """A definite-length block and what follows it on the line, read the way scripts read binary data
    (the payload may contain line feeds, so reading up to the termination character is not enough)."""
    head = inst.read_bytes(2)
    digits = inst.read_bytes(int(head[1:2]))
    payload = inst.read_bytes(int(digits))
    tail = b""
    while not tail.endswith(b"\n"):
        tail += inst.read_bytes(1)
    while True:  # a second line feed, as some instruments send (Siglent's "0A 0A")
        try:
            inst.timeout, saved = 50, inst.timeout
            more = inst.read_bytes(1)
        except pyvisa.errors.VisaIOError:
            break
        finally:
            inst.timeout = saved
        tail += more
    return head + digits + payload + tail


@pytest.mark.parametrize(("cases_file", "cases", "case"), list(all_cases()))
def test_manual_case(tmp_path, cases_file, cases, case):
    rm, inst = open_instrument(tmp_path, cases_file, cases)
    try:
        for i, step in enumerate(case.steps, 1):
            where = f"{case.name}: step {i} ({step.command!r}), manual: {case.manual}"
            if step.send is not None:
                inst.write(step.send)
                continue
            if step.binary:
                inst.write(step.query)
                reply = read_block(inst).decode("latin-1")
            else:
                reply = inst.query(step.query).strip()
            if step.expect is not None:
                assert reply == step.expect, where
            elif step.expect_match is not None:
                assert re.fullmatch(step.expect_match, reply, re.DOTALL), f"{where}: {reply[:80]!r}"
            else:
                tol = step.tolerance or 0.0
                assert abs(float(reply) - step.expect_value) <= tol, f"{where}: {reply!r}"
    finally:
        rm.close()


def real_models():
    """Definitions with a reference: in the product library, and the framework's own test model."""
    lib = DefinitionLibrary()
    paths = [lib.path(i) for i in lib.ids()]
    paths += [d / f"{d.name}.yaml" for d in sorted(MODELS.iterdir()) if (d / f"{d.name}.yaml").is_file()]
    for path in paths:
        if DefinitionLibrary([path.parent]).get(path.stem).reference is not None:
            yield pytest.param(path, id=path.stem)


@pytest.mark.parametrize("path", list(real_models()))
def test_coverage_table_agrees_with_definition_and_cases(path):
    lib = DefinitionLibrary([path.parent])
    definition = lib.get(path.stem)
    cov = coverage_path(path)
    assert cov.is_file(), f"{definition.id}: no coverage table {cov.name}"
    cases_file = MODELS / definition.id / "cases.yaml"
    assert cases_file.is_file(), f"{definition.id}: no manual cases {cases_file}"
    assert check(definition, load_coverage(cov), load_cases(cases_file)) == []


def test_framework_model_is_not_in_the_product_library():
    assert "acme-dm1" not in DefinitionLibrary().ids()


# ---- the checker names the command that disagrees ---------------------------------------------------


def _acme():
    path = MODELS / "acme-dm1" / "acme-dm1.yaml"
    defn = DefinitionLibrary([path.parent]).get("acme-dm1")
    return defn, load_coverage(coverage_path(path)), load_cases(MODELS / "acme-dm1" / "cases.yaml")


def test_check_reports_a_definition_command_missing_from_the_table():
    defn, cov, cases = _acme()
    cov.sections[1].commands = [c for c in cov.sections[1].commands if "RANGe" not in c.header]
    assert check(defn, cov, cases) == [
        "[:SENSe]:VOLTage[:DC]:RANGe:AUTO: in the definition but not in the coverage table"
    ]


def test_check_reports_a_listed_command_missing_from_the_definition():
    defn, cov, cases = _acme()
    defn = defn.model_copy(update={"commands": [c for c in defn.commands if c.header != "OUT"]})
    assert check(defn, cov, cases) == [
        "OUT: marked implemented (Command Reference > Source (Fluke-style, "
        "no colon hierarchy)) but not in the definition"
    ]


def test_check_reports_a_command_without_a_case():
    defn, cov, cases = _acme()
    cases.cases = [c for c in cases.cases if "DISPlay" not in c.manual]
    assert check(defn, cov, cases) == [
        ":DISPlay[:STATe]: marked accepted (Command Reference > DISPlay Subsystem) but no manual case uses it"
    ]


def test_check_reports_a_defined_command_marked_not_modeled():
    defn, cov, cases = _acme()
    data = cov.model_dump(by_alias=True)
    data["sections"][4]["commands"][0]["level"] = "not_modeled"
    problems = check(defn, Coverage.model_validate(data), cases)
    assert problems == [":TRIGger:SOURce: in the definition but marked not modeled in the coverage table"]


# ---- framework behavior on the made-up model ----------------------------------------------------------


@pytest.fixture
def acme(tmp_path):
    cases = load_cases(MODELS / "acme-dm1" / "cases.yaml")
    rm, inst = open_instrument(tmp_path, MODELS / "acme-dm1" / "cases.yaml", cases)
    yield inst, rm.visalib.bench
    rm.close()


def test_error_queue_overflow_follows_the_definition(acme):
    inst, _ = acme
    for _ in range(6):
        inst.write("VOLT:NPLC 1000")
    replies = [inst.query("SYST:ERR?").strip() for _ in range(5)]
    # Length 4: three errors, then -350 in the last slot; then the queue is empty.
    assert replies == [
        '-222,"Data out of range"',
        '-222,"Data out of range"',
        '-222,"Data out of range"',
        '-350,"Queue overflow"',
        '+0,"No error"',
    ]


def test_device_specific_error_text_from_the_table(acme):
    inst, bench = acme
    bench.instruments["dut"].push_error(501, "framing")
    assert inst.query("SYST:ERR?").strip() == '+501,"Isolator UART framing error"'


def test_not_modeled_commands_are_marked_in_the_event_history(acme):
    inst, bench = acme
    inst.write("CURR:NPLC 1")
    errors = [e for e in bench.history if e.get("kind") == "error"]
    assert errors[-1]["reason"] == "not_modeled"
    assert errors[-1]["code"] == -113 and "CURR:NPLC" in errors[-1]["message"]


def test_sb_models_keep_their_replies(tmp_path):
    """Instruments without a reference behave as before: -113 with the header, default formats."""
    bench = tmp_path / "sb.bench.yaml"
    bench.write_text(yaml.safe_dump({"name": "sb", "instruments": {"awg": {"model": "sb-awg-2"}}}))
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    awg = rm.open_resource("awg", read_termination="\n", write_termination="\n")
    awg.write("FOO:BAR")
    assert awg.query("SYST:ERR?").strip() == '-113,"Undefined header; FOO:BAR"'
    assert awg.query("FREQ?").strip() == "+1.000000E+03"
    rm.close()


def test_coverage_command_prints_the_table_and_counts(capsys):
    assert main(["coverage", "acme-dm1", "--definitions", str(MODELS / "acme-dm1")]) == 0
    out = capsys.readouterr().out
    assert "ACME DM1 Programming Manual" in out
    assert "[:SENSe]:CURRent[:DC]:NPLC" in out and "未实现" in out
    assert "实现 8 · 接受 1 · 未实现 1" in out


# ---- IEEE 488.2 syntax and response formats ------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "unit", "value"),
    [(b"1 V", "V", 1.0), (b"10 mV", "V", 0.01), (b"1 kHz", "HZ", 1e3), (b"2.5MV", "V", 2.5e-3)],
)
def test_space_between_number_and_unit(text, unit, value):
    assert V.parse_float(text, unit) == pytest.approx(value)


def test_several_parameters_with_units_and_a_header_without_colons():
    unit = parse_unit(b"OUT 1 V, 1 kHz")
    assert [t.keyword for t in unit.tokens] == ["OUT"]
    assert [V.parse_float(unit.params[0], "V"), V.parse_float(unit.params[1], "HZ")] == [1.0, 1e3]
    assert Pattern.compile("OUT").match(unit.tokens) == {}
    assert Pattern.compile("ERR").match(parse_unit(b"ERR?").tokens) == {}


@pytest.mark.parametrize(
    ("kw", "value", "text"),
    [
        ({}, 1e3, b"+1.000000E+03"),
        ({"digits": 8}, 10.0, b"+1.00000000E+01"),
        ({"digits": 3, "exponent_digits": 3}, -0.0125, b"-1.250E-002"),
        ({"sign": False}, 2.0, b"2.000000E+00"),
        ({"style": "nr2", "digits": 3}, 1.5, b"+1.500"),
        ({"style": "nr1", "sign": False}, 42.4, b"42"),
        ({"digits": 5}, float("inf"), b"+9.90000E+37"),
        ({"digits": 5}, float("nan"), b"+9.91000E+37"),
    ],
)
def test_number_formats(kw, value, text):
    assert V.format_float(value, **kw) == text
