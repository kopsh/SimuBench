"""Definition layering (#596): 类别默认与兄弟继承（extends）.

The merge order is 类别默认 → 父 → 子: the class's default errors table lowest, then the
extends chain (dicts merge recursively, lists are replaced whole, ``errors`` by code), the file's
own data last. The pure merge semantics are tested on dicts, the loader's on small files in
``tmp_path``; no session is built.
"""

from __future__ import annotations

import os

import pytest
import yaml

from helpers import raises_with
from simubench.library.class_defaults import CLASS_ERRORS, deep_merge, default_block
from simubench.library.loader import DefinitionLibrary

# ---- the merge itself ---------------------------------------------------------------------------------


def test_deep_merge_recurses_dicts_and_replaces_lists():
    base = {"errors": {-222: "out of range", -999: "device"}, "state": {"a": {"min": 1, "options": ["X"]}}}
    override = {"errors": {-222: "range"}, "state": {"a": {"options": ["X", "Y"], "max": 9}}, "z": 1}
    out = deep_merge(base, override)
    assert out["errors"] == {-222: "range", -999: "device"}  # by code, override wins
    assert out["state"]["a"] == {"min": 1, "options": ["X", "Y"], "max": 9}  # dicts recurse, lists replace
    assert out["z"] == 1


def test_deep_merge_changes_neither_input():
    base = {"a": {"b": 1}}
    override = {"a": {"c": 2}}
    out = deep_merge(base, override)
    assert out == {"a": {"b": 1, "c": 2}}
    assert base == {"a": {"b": 1}} and override == {"a": {"c": 2}}


def test_default_block_by_class():
    block = default_block("multimeter")
    assert block["errors"] == CLASS_ERRORS["multimeter"]
    assert default_block("no_such_class") == {}


# ---- the loader layers files --------------------------------------------------------------------------


def write(dir_path, def_id: str, **extra) -> None:
    data = {
        "schema": "simubench.instrument/2",
        "id": def_id,
        "manufacturer": "ACME",
        "model": def_id,
        "class": "multimeter",
        "reference": {
            "title": f"{def_id} Programming Manual"
        },  # a real model (D42): the class default applies
        **extra,
    }
    (dir_path / f"{def_id}.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.fixture
def lib(tmp_path):
    return DefinitionLibrary([tmp_path])


def test_a_definition_without_reference_gets_no_class_default(tmp_path, lib):
    write(tmp_path, "toy", reference=None)  # the sb-* validation instruments look like this
    assert lib.get("toy").errors == {}  # the engine's standard texts decide, no class table


def test_priority_child_over_parent_over_class_default(tmp_path, lib):
    write(tmp_path, "base", errors={-224: "base wording", -999: "device code"})
    write(tmp_path, "derived", extends="base", errors={-224: "derived wording"})
    d = lib.get("derived")
    assert d.errors[-224] == "derived wording"  # child beats parent
    assert d.errors[-999] == "device code"  # inherited from the parent
    assert d.errors[-222] == "Data out of range"  # the class default supplies what no file lists
    assert d.errors[-350] == "Queue overflow"  # class default: multimeter's own -350 wording
    assert d.extends == "base"


def test_lists_are_replaced_whole_not_joined(tmp_path, lib):
    write(tmp_path, "base", state={"rng": {"type": "enum", "options": ["A", "B"], "default": "A"}})
    write(
        tmp_path,
        "derived",
        extends="base",
        state={"rng": {"type": "enum", "options": ["A", "C"], "default": "A"}},
    )
    d = lib.get("derived")
    assert d.state["rng"].options == ["A", "C"]  # the parent's "B" is gone, not appended to


def test_a_chain_goes_several_steps(tmp_path, lib):
    write(tmp_path, "grand", errors={-998: "grand"})
    write(tmp_path, "mid", extends="grand", errors={-997: "mid"})
    write(tmp_path, "leaf", extends="mid")
    d = lib.get("leaf")
    assert d.errors[-998] == "grand" and d.errors[-997] == "mid"
    assert d.errors[-113] == "Undefined header"  # class default under the whole chain


def test_a_cycle_is_refused(tmp_path, lib):
    write(tmp_path, "a", extends="b")
    write(tmp_path, "b", extends="a")
    with raises_with(ValueError, "成环"):
        lib.get("a")


def test_extending_itself_is_refused(tmp_path, lib):
    write(tmp_path, "a", extends="a")
    with raises_with(ValueError, "成环"):
        lib.get("a")


def test_an_unknown_parent_is_refused(tmp_path, lib):
    write(tmp_path, "a", extends="no-such-model")
    with raises_with(ValueError, "no definition"):
        lib.get("a")


def test_an_extends_edit_of_the_parent_rebuilds_the_child(tmp_path, lib):
    write(tmp_path, "base", errors={-999: "first"})
    write(tmp_path, "derived", extends="base")
    assert lib.get("derived").errors[-999] == "first"
    write(tmp_path, "base", errors={-999: "second"})
    p = tmp_path / "base.yaml"
    os.utime(p, (10**9, 10**9 + 1))  # a distinct mtime re-keys the loader's cache
    assert lib.get("derived").errors[-999] == "second"


# ---- the built-in library ------------------------------------------------------------------------------


def test_builtin_siblings_inherit_and_class_tables_supply_the_errors():
    lib = DefinitionLibrary()
    counters, daqs = lib.get("keysight-53220a"), lib.get("keysight-53230a")
    assert daqs.extends == "keysight-53220a"
    assert daqs.state["measure.channels"] == counters.state["measure.channels"]  # inherited
    assert daqs.errors == CLASS_ERRORS["frequency_counter"]  # both files' tables are the class's
    daq = lib.get("keysight-daq970a")
    assert daq.extends == "keysight-34970a"
    assert daq.builtins == lib.get("keysight-34970a").builtins  # the parent's list, whole
    assert daq.errors[-230] == "Data corrupt or stale"  # the child's wording beats the parent's


def test_single_file_classes_keep_their_own_tables():
    lib = DefinitionLibrary()
    d = lib.get("keysight-33622a")  # the only function_generator: no class default may leak in
    assert len(d.errors) == 67 and d.errors[786] == "Specified arb waveform already exists"
    d = lib.get("fluke-8588a")  # its own wording beats the class default
    assert d.errors[-200] == "Execution Error"
    assert lib.get("keysight-34465a").errors[-350] == "Queue overflow"  # agrees with the table


# ---- ``key: null`` drops an inherited key (D59, #644) -------------------------------------------------


def test_null_drops_an_inherited_dict_key_and_a_single_errors_code(tmp_path, lib):
    write(
        tmp_path,
        "base",
        errors={-999: "device code", -998: "other"},
        state={"a": {"type": "bool"}, "b": {"type": "bool"}},
    )
    write(tmp_path, "derived", extends="base", errors={-999: None}, state={"a": None})
    d = lib.get("derived")
    assert -999 not in d.errors and d.errors[-998] == "other"  # one code gone, its neighbour stays
    assert set(d.state) == {"b"}


def test_null_beats_a_list_the_parent_replaced_and_drops_it_whole(tmp_path, lib):
    write(tmp_path, "base", capabilities=["smu"])
    write(tmp_path, "derived", extends="base", capabilities=None)
    assert lib.get("derived").capabilities == []  # the list is dropped, not merged
    assert lib.get("base").capabilities == ["smu"]


def test_null_survives_the_chain_and_the_class_default(tmp_path, lib):
    write(tmp_path, "grand", errors={-998: "grand"})
    write(tmp_path, "mid", extends="grand")
    write(tmp_path, "leaf", extends="mid", errors={-998: None, -222: None})
    d = lib.get("leaf")
    assert -998 not in d.errors  # a lower layer cannot bring it back
    assert -222 not in d.errors  # nor can the class default
    assert d.errors[-113] == "Undefined header"


def test_null_without_anything_inherited_is_just_absent(tmp_path, lib):
    write(tmp_path, "base")
    write(tmp_path, "derived", extends="base", errors={-777: None})
    assert -777 not in lib.get("derived").errors


def test_the_nearest_parent_beats_the_grandparent(tmp_path, lib):
    write(tmp_path, "grand", errors={-998: "grand wording"})
    write(tmp_path, "mid", extends="grand", errors={-998: "mid wording"})
    write(tmp_path, "leaf", extends="mid")
    assert lib.get("leaf").errors[-998] == "mid wording"


def test_b2912b_extends_b2901a_without_its_single_channel_state_and_ports():
    lib = DefinitionLibrary()
    single, dual = lib.get("keysight-b2901a"), lib.get("keysight-b2912b")
    assert dual.extends == "keysight-b2901a"
    # the single channel's instrument-level state and ports do not leak into the dual one
    assert {"output.enabled", "source.function", "sweep.mode", "trigger.source"} <= set(single.state)
    assert not any(
        k.split(".")[0] in {"output", "source", "sense", "sweep", "trigger", "compliance"} for k in dual.state
    )
    assert "output.enabled" in dual.channel_state
    assert not dual.ports.instrument and set(dual.ports.channel) == {
        "HI{n}",
        "LO{n}",
        "SENSE_HI{n}",
        "SENSE_LO{n}",
    }
    assert dual.components == [] and dual.display.summary[0] == "ch1.source.function"
    assert dual.state["display.view"].default == "DUAL"  # its own default beats the parent's


def test_daq970a_and_53230a_do_not_keep_the_parents_device_rows():
    lib = DefinitionLibrary()
    daq = lib.get("keysight-daq970a")
    assert not {111, 112, 113, 201, 301, 303} & set(daq.errors)  # 34970A device codes
    assert lib.get("keysight-34970a").errors.get(111)
    counter = lib.get("keysight-53230a")
    assert "53220A" not in counter.specs["gate_time_s"] and "resolution_digits_53220a" not in counter.specs


# ---- command_overrides: one command, a group or null at a time (D59, #1480) ------------------------

_STATE = {"a": {"type": "float", "default": 0}, "b": {"type": "float", "default": 0}}


def _cmd(header: str, **fields) -> dict:
    return {"header": header, "state": "a", **fields}


def test_command_overrides_replace_add_and_remove_whole_commands(tmp_path, lib):
    write(tmp_path, "base", state=_STATE, commands=[_cmd(":A"), _cmd(":B", query=False), _cmd(":C")])
    write(
        tmp_path,
        "derived",
        extends="base",
        command_overrides={":B": {"state": "b"}, ":C": None, ":D": {"state": "b"}},
    )
    commands = {c.header: c for c in lib.get("derived").commands}
    assert list(commands) == [":A", ":B", ":D"]  # replaced in place, removed, appended
    assert commands[":B"].state == "b" and commands[":B"].query is True  # whole: query not kept


def test_command_overrides_a_list_value_replaces_the_header_as_a_group(tmp_path, lib):
    # the when/option variants of one header are one unbreakable group (#1480)
    fmt = {"name": "fmt", "type": "string"}
    write(
        tmp_path,
        "base",
        state=_STATE,
        commands=[_cmd(":A"), _cmd(":B", params=[fmt], when={"fmt": "WORD"}), _cmd(":B", option="01G")],
    )
    write(
        tmp_path,
        "derived",
        extends="base",
        command_overrides={
            ":B": [
                {"state": "b", "params": [fmt], "when": {"fmt": "BYTE"}},
                {"state": "a", "option": "01G"},
                {"state": "b"},
            ]
        },  # any number of entries: three replace two
    )
    commands = lib.get("derived").commands
    assert [c.header for c in commands] == [":A", ":B", ":B", ":B"]  # at the first :B's place
    assert commands[1].when == {"fmt": "BYTE"} and commands[1].query is True  # whole: query not kept
    assert commands[2].option == "01G" and commands[2].state == "a"


def test_command_overrides_null_removes_every_entry_of_the_header(tmp_path, lib):
    write(tmp_path, "base", state=_STATE, commands=[_cmd(":A"), _cmd(":B"), _cmd(":B", option="01G")])
    write(tmp_path, "derived", extends="base", command_overrides={":B": None})
    assert [c.header for c in lib.get("derived").commands] == [":A"]


def test_command_overrides_one_command_names_the_header_whole(tmp_path, lib):
    # a single value on a multi-variant header is that header's whole group: one command (#1480)
    write(tmp_path, "base", state=_STATE, commands=[_cmd(":A"), _cmd(":B"), _cmd(":B", query=False)])
    write(tmp_path, "derived", extends="base", command_overrides={":B": {"state": "b"}})
    commands = lib.get("derived").commands
    assert [c.header for c in commands] == [":A", ":B"]
    assert commands[1].state == "b" and commands[1].query is True


def test_command_overrides_stack_through_the_chain_above_the_nearest_list(tmp_path, lib):
    write(tmp_path, "grand", state=_STATE, commands=[_cmd(":A"), _cmd(":B")])
    write(tmp_path, "mid", extends="grand", command_overrides={":B": None})
    write(tmp_path, "leaf", extends="mid", command_overrides={":C": {"state": "b"}})
    assert [c.header for c in lib.get("leaf").commands] == [":A", ":C"]
    write(tmp_path, "own", extends="mid", commands=[_cmd(":X")], command_overrides={":Y": {"state": "b"}})
    assert [c.header for c in lib.get("own").commands] == [":X", ":Y"]  # its own list replaces mid's


def test_command_overrides_refuse_what_they_cannot_name(tmp_path, lib):
    write(tmp_path, "base", state=_STATE, commands=[_cmd(":A"), _cmd(":A", query=False)])
    write(tmp_path, "gone", extends="base", command_overrides={":Z": None})
    with raises_with(ValueError, "无从删除") as info:
        lib.get("gone")
    assert info.value.code == "E-DEF-COMMAND-OVERRIDE"  # type: ignore[attr-defined]


# ---- component_overrides (#1425) ----------------------------------------------------------------------

_SEL = {"from": "$a", "map": {"1": 50.0, "2": 75.0}, "default": 50.0}


def _comp(name: str, **params) -> dict:
    return {"name": name, "type": "input_port", "params": {"z_in": 50.0, **params}}


def _components(lib, def_id: str, key: str = "components") -> dict:
    return {c.name: c for c in getattr(lib.get(def_id), key)}


def test_component_overrides_change_one_parameter_and_keep_the_rest(tmp_path, lib):
    base = [{**_comp("a", k=1.0, sel=_SEL), "label": "A"}, _comp("b"), _comp("c")]
    write(tmp_path, "base", state=_STATE, components=base)
    write(
        tmp_path,
        "derived",
        extends="base",
        component_overrides={
            "a": {
                "params": {"z_in": 75.0, "k": None, "sel": {"from": "$a", "map": {"1": 60.0}}},
                "label": None,
            },
            "c": None,
            "d": {"type": "input_port", "params": {"z_in": 1.0e6}},
        },
    )
    comps = _components(lib, "derived")
    assert list(comps) == ["a", "b", "d"]  # changed in place, removed, appended
    a = comps["a"]
    assert a.type == "input_port" and a.label is None  # type kept, label removed
    assert a.params["z_in"] == 75.0 and "k" not in a.params  # one parameter changed, one removed
    assert a.params["sel"] == {"from": "$a", "map": {"1": 60.0}}  # a mapping is one value: no merge
    assert comps["d"].params == {"z_in": 1.0e6}
    assert lib.get("derived").component_overrides is None  # empty once loaded


def test_component_overrides_follow_later_fixes_of_the_parent(tmp_path, lib):
    write(tmp_path, "base", state=_STATE, components=[_comp("a"), _comp("b")])
    write(tmp_path, "derived", extends="base", component_overrides={"a": {"params": {"z_in": 75.0}}})
    assert _components(lib, "derived")["b"].params["z_in"] == 50.0
    write(tmp_path, "base", state=_STATE, components=[_comp("a"), _comp("b", z_in=1.0e6)])
    os.utime(tmp_path / "base.yaml", (10**9, 10**9 + 1))  # a distinct mtime re-keys the loader's cache
    comps = _components(lib, "derived")
    assert comps["b"].params["z_in"] == 1.0e6 and comps["a"].params["z_in"] == 75.0


def test_component_overrides_stack_through_the_chain_above_the_nearest_list(tmp_path, lib):
    write(
        tmp_path, "grand", state=_STATE, components=[_comp("a"), _comp("b")], channel_components=[_comp("in")]
    )
    write(tmp_path, "mid", extends="grand", component_overrides={"b": None})
    write(
        tmp_path,
        "leaf",
        extends="mid",
        component_overrides={"a": {"params": {"z_in": 75.0}}},
        channel_component_overrides={"in": {"params": {"z_in": 1.0e6}}},
    )
    comps = _components(lib, "leaf")
    assert list(comps) == ["a"] and comps["a"].params["z_in"] == 75.0
    assert _components(lib, "leaf", "channel_components")["in"].params["z_in"] == 1.0e6
    write(tmp_path, "own", extends="mid", components=[_comp("x")], component_overrides={"x": {"label": "X"}})
    assert [(c.name, c.label) for c in lib.get("own").components] == [("x", "X")]  # its list replaces mid's


def test_component_overrides_refuse_what_they_cannot_name(tmp_path, lib):
    write(tmp_path, "base", state=_STATE, components=[_comp("a"), _comp("a"), _comp("b")])
    cases = {
        "twice": ({"a": {"params": {"z_in": 1.0}}}, "出现 2 次"),
        "gone": ({"z": None}, "无从删除"),
        "retype": ({"b": {"type": "output_stage"}}, "类型不能改"),
        "untyped": ({"n": {"params": {"z_in": 1.0}}}, "要写明 type"),
    }
    for def_id, (overrides, _) in cases.items():
        write(tmp_path, def_id, extends="base", component_overrides=overrides)
    for def_id, (_, words) in cases.items():
        with raises_with(ValueError, words) as info:
            lib.get(def_id)
        assert info.value.code == "E-DEF-COMPONENT-OVERRIDE"  # type: ignore[attr-defined]


def test_a_uxr1104b_child_that_changes_the_adc_is_the_whole_list_copy(tmp_path, lib):
    """#1425's acceptance: a child changing two ADC parameters stays a few lines and loads the same
    as the child that copies the parent's component list whole and edits it."""
    parent = DefinitionLibrary([]).get("keysight-uxr1104b")
    head = {"schema": "simubench.instrument/2", "extends": "keysight-uxr1104b", "manufacturer": "ACME",
            "class": "oscilloscope"}  # fmt: skip
    small = {**head, "id": "small", "model": "small",
             "component_overrides": {"adc": {"params": {"bits": 12, "codes_per_div": 512.0}}}}  # fmt: skip
    text = yaml.safe_dump(small, sort_keys=False, default_flow_style=None)
    assert len(text.splitlines()) <= 10
    (tmp_path / "small.yaml").write_text(text, encoding="utf-8")
    whole = [c.model_dump(exclude_none=True) for c in parent.components]
    for c in whole:
        if c["name"] == "adc":
            c["params"].update(bits=12, codes_per_div=512.0)
    (tmp_path / "whole.yaml").write_text(
        yaml.safe_dump({**head, "id": "whole", "model": "whole", "components": whole}), encoding="utf-8"
    )
    dump = lambda d: [c.model_dump() for c in lib.get(d).components]  # noqa: E731
    assert dump("small") == dump("whole")
    assert dump("small") != [c.model_dump() for c in parent.components]


# ---- a hypothetical instrument built on a real model (#1428) ----------------------------------------


def test_a_child_that_drops_the_reference_keeps_the_class_default(tmp_path, lib):
    write(tmp_path, "base", errors={-999: "device code"})
    write(tmp_path, "derived", extends="base", reference=None)  # no real model, built on one
    d = lib.get("derived")
    assert d.reference is None
    assert d.errors[-222] == "Data out of range" and d.errors[-999] == "device code"


def test_a_hypothetical_34465a_answers_errors_as_the_model_it_is_built_on(tmp_path):
    """#1428's acceptance: the 34465A's file lists none of its error texts — the multimeter class
    table supplies them — so a child without a reference must still get the table."""
    from simubench.api import open_bench
    from simubench.models.bench import BenchDefinition

    child = {"schema": "simubench.instrument/2", "id": "my-dmm", "extends": "keysight-34465a",
             "manufacturer": "MyLab", "model": "DMM-1", "class": "multimeter", "reference": None}  # fmt: skip
    (tmp_path / "my-dmm.yaml").write_text(yaml.safe_dump(child), encoding="utf-8")
    replies = []
    for model in ("keysight-34465a", "my-dmm"):
        bench = BenchDefinition.model_validate(
            {"schema": "simubench.bench/2", "name": "b", "instruments": {"m": {"model": model}}}
        )
        s = open_bench(bench, definition_dirs=[tmp_path])
        got = []
        for command in (b":SENSe:VOLTage:DC:NPLC abc", b"*TRG"):  # -104 and -211: class-table texts
            s.write("m", command + b"\n")
            s.write("m", b":SYSTem:ERRor?\n")
            got.append(s.read("m").decode().strip())
        replies.append(got)
        s.close()
    assert replies[0] == ['-104,"Data type error"', '-211,"Trigger ignored"']
    assert replies[1] == replies[0]  # without the table: the engine's '-211,"Error; Trigger ignored"'
