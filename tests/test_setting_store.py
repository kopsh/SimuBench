"""The setting store (#895) on its own: a definition, no instrument, no session."""

from __future__ import annotations

from helpers import raises_with
from simubench.engine.settings import SettingStore
from simubench.library.loader import DefinitionLibrary

LIB = DefinitionLibrary()


def _store(model: str = "bk-8614", channels: int | None = None) -> SettingStore:
    d = LIB.get(model)
    return SettingStore(d, channels if channels is not None else d.channels.count, "t")


def _numeric(store: SettingStore) -> str:
    return next(
        p for p, s in store.specs.items() if s.type == "float" and s.min is not None and s.max is not None
    )


def test_the_specs_follow_the_definition_per_channel():
    d = LIB.get("sb-dso-2x")
    store = SettingStore(d, d.channels.count, "t")
    assert set(d.state) <= set(store.specs)
    assert all(f"ch{n}.{k}" in store.specs for n in range(1, d.channels.count + 1) for k in d.channel_state)


def test_reset_gives_every_setting_a_value_of_its_type():
    store = _store()
    store.reset(power_on=True)
    assert set(store.values) == set(store.specs)
    for path, spec in store.specs.items():
        if spec.type == "float":
            assert isinstance(store.values[path], float), path
        if spec.type == "bool":
            assert isinstance(store.values[path], bool), path


def test_coerce_follows_the_spec_type():
    store = _store()
    kinds = {}
    for path, spec in store.specs.items():
        kinds.setdefault(spec.type, path)
    if "float" in kinds:
        assert store.coerce(kinds["float"], "3") == 3.0
    if "int" in kinds:
        assert store.coerce(kinds["int"], 3.0) == 3
    if "bool" in kinds:
        assert store.coerce(kinds["bool"], 1) is True


def test_bounds_move_and_restore_against_the_definitions_own():
    store = _store()
    path = _numeric(store)
    lo, hi = store.specs[path].min, store.specs[path].max
    store.set_bounds(path, maximum=hi + 5)
    assert (store.specs[path].min, store.specs[path].max) == (lo, hi + 5)
    store.set_bounds(path, minimum=lo - 1)  # relative to the definition's, not to the last move
    assert (store.specs[path].min, store.specs[path].max) == (lo - 1, hi)
    store.restore_bounds(path)
    assert (store.specs[path].min, store.specs[path].max) == (lo, hi)
    store.restore_bounds(path)  # nothing moved: no-op


def test_an_unknown_setting_names_the_instrument():
    with raises_with(KeyError, "instrument 't' has no state 'nope'"):
        _store().get("nope")
