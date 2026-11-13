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
        "schema": "simubench.instrument/1",
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
