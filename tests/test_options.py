"""Hardware options (选件, D68) from the bench editor, the session view and *OPT? (#1007)."""

from pathlib import Path

import pytest
import yaml
from pydantic import TypeAdapter

from helpers import raises_with, session_query
from simubench.api import open_bench
from simubench.engine.commands import classify
from simubench.engine.edit import EditError, EditOp, apply
from simubench.library.loader import DefinitionLibrary
from simubench.models.bench import BenchDefinition
from simubench.server import views

LIB = DefinitionLibrary()
OPS = TypeAdapter(list[EditOp])
N9320B = Path(__file__).resolve().parents[1] / "src/simubench/library/instruments/keysight-n9320b.yaml"


def ops(*items):
    return OPS.validate_python(list(items))


def bench(options: list[str] | None = None) -> BenchDefinition:
    sa = {"model": "keysight-n9320b", **({"options": options} if options is not None else {})}
    return BenchDefinition.model_validate({"name": "t", "instruments": {"sa": sa}})


def preamp_figure(b: BenchDefinition, dirs: list[Path] | None = None):
    s = open_bench(b, definition_dirs=dirs)
    try:
        return s.instruments["sa"].components["input"].param("preamp_noise_figure")
    finally:
        s.close()


def test_set_options_adds_and_removes_an_option_that_takes_effect_when_the_bench_opens():
    with_p03 = apply(
        bench(), ops({"op": "set_options", "instrument": "sa", "options": ["P03", "P03"]}), LIB.get
    )
    assert with_p03.instruments["sa"].options == ["P03"]
    assert preamp_figure(with_p03) == 0.0  # the option's preamplifier is in the chain
    without = apply(with_p03, ops({"op": "set_options", "instrument": "sa", "options": []}), LIB.get)
    assert without.instruments["sa"].options == [] and preamp_figure(without) is None


def test_set_options_refuses_an_option_the_definition_does_not_declare():
    with raises_with(EditError, "E-OPTION-UNKNOWN", "P07", "P03"):
        apply(bench(), ops({"op": "set_options", "instrument": "sa", "options": ["P07"]}), LIB.get)
    with raises_with(EditError, "nope"):
        apply(bench(), ops({"op": "set_options", "instrument": "nope", "options": []}), LIB.get)


def test_replacing_the_model_drops_the_options_it_does_not_declare():
    b = apply(
        bench(["P03"]), ops({"op": "replace_instrument", "name": "sa", "model": "keysight-n9042b"}), LIB.get
    )
    assert b.instruments["sa"].options == []


def test_the_session_view_lists_the_models_options_and_which_are_installed():
    for installed in ([], ["P03"]):
        s = open_bench(bench(installed))
        try:
            (p03,) = views.option_infos(s.instruments["sa"].definition, s.instruments["sa"].options)
        finally:
            s.close()
        assert p03.id == "P03" and p03.installed is bool(installed)
        assert p03.params == ["input.preamp_gain", "input.preamp_noise_figure"]
        assert p03.commands == ["[:SENSe]:POWer[:RF]:GAIN[:STATe]"]
    assert views.option_infos(LIB.get("sb-dso-4"), []) == []


@pytest.fixture
def opt_dir(tmp_path):
    """The N9320B with a built-in *OPT? that lists the unit's options."""
    data = yaml.safe_load(N9320B.read_text("utf-8"))
    data["identity"]["options_reply"] = "{options}"
    (tmp_path / N9320B.name).write_text(yaml.safe_dump(data, allow_unicode=True), "utf-8")
    return tmp_path


def test_opt_answers_the_installed_options_when_the_definition_asks_for_it(opt_dir):
    for installed, reply in (([], "0"), (["P03"], "P03")):
        s = open_bench(bench(installed), definition_dirs=[opt_dir])
        try:
            assert session_query(s, "sa", "*OPT?") == reply
            s.write("sa", b"*OPT\n")
            assert session_query(s, "sa", ":SYST:ERR?").startswith("-113")
            (match,) = classify(s.instruments["sa"].definition, "*OPT?")
            assert match.kind == "common"
        finally:
            s.close()


def test_opt_without_options_reply_is_unchanged():
    s = open_bench(bench(["P03"]))
    try:
        s.write("sa", b"*OPT?\n")  # the N9320B's definition answers no *OPT?
        assert session_query(s, "sa", ":SYST:ERR?").startswith("-113")
    finally:
        s.close()
    s = open_bench(
        BenchDefinition.model_validate({"name": "t", "instruments": {"awg": {"model": "keysight-m8194a"}}})
    )
    try:
        assert session_query(s, "awg", "*OPT?") == "004"  # its own command, bound to a setting
    finally:
        s.close()
