"""Built-in real models are definitions over generic components (metrology-authoring.md 9, #93).

A component written for one model's own behavior (``generic = False``) is debt: what the model
needs belongs in a generic component, with the model's values as parameters. The list below names
the uses that exist today; it may only get shorter.
"""

from __future__ import annotations

from simubench.engine.component import component_type
from simubench.library.loader import BUILTIN_DIR, COVERAGE_SUFFIX, DefinitionLibrary
from simubench.models.definition import InstrumentDefinition

#: (definition id, component type) uses of model-specific components still allowed. When one is
#: paid off, delete it here: the test fails while a listed use no longer exists.
ALLOWED = {
    ("siglent-sdg2042x", "siglent_sdg"),
    ("siglent-sdg2042x", "siglent_sdg_channel_limits"),
}


def specific_components(definition: InstrumentDefinition) -> set[tuple[str, str]]:
    """(definition id, component type) for every model-specific component the definition uses."""
    specs = [*definition.components, *definition.channel_components]
    return {(definition.id, c.type) for c in specs if not component_type(c.type).generic}


def builtin_definitions() -> list[InstrumentDefinition]:
    lib = DefinitionLibrary()
    ids = [p.stem for p in BUILTIN_DIR.glob("*.yaml") if not p.name.endswith(COVERAGE_SUFFIX)]
    return [lib.get(i) for i in sorted(ids)]


def problems(definitions: list[InstrumentDefinition], allowed: set[tuple[str, str]]) -> list[str]:
    used = set().union(*(specific_components(d) for d in definitions if d.reference is not None))
    out = [
        f"{d}: uses the model-specific component {c!r}; put the behavior in a generic component "
        "with the model's values as parameters (docs/metrology-authoring.md 9)"
        for d, c in sorted(used - allowed)
    ]
    out += [
        f"{d}: no longer uses {c!r}; remove it from ALLOWED in tests/test_model_components.py"
        for d, c in sorted(allowed - used)
    ]
    return out


def test_builtin_real_models_use_generic_components_only():
    assert problems(builtin_definitions(), ALLOWED) == []


def test_a_new_model_specific_use_is_named():
    sdg = DefinitionLibrary().get("siglent-sdg2042x")
    copy = sdg.model_copy(update={"id": "acme-fg1"})
    found = problems([*builtin_definitions(), copy], ALLOWED)
    assert found and all(p.startswith("acme-fg1: uses the model-specific component") for p in found)
    assert {p.split("'")[1] for p in found} == {"siglent_sdg", "siglent_sdg_channel_limits"}


def test_a_paid_off_use_must_leave_the_list():
    found = problems(builtin_definitions(), ALLOWED | {("siglent-sds2104xplus", "siglent_sdg")})
    assert found == [
        "siglent-sds2104xplus: no longer uses 'siglent_sdg'; "
        "remove it from ALLOWED in tests/test_model_components.py"
    ]


def test_the_catalog_says_which_components_are_generic():
    from simubench.server.catalog import components

    by_type = {c.type: c.generic for c in components()}
    assert by_type["siglent_sdg"] is False and by_type["dmm_core"] is True
    assert {t for t, g in by_type.items() if not g} == {c for _, c in ALLOWED}
