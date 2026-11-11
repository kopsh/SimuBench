"""Built-in real models cover the real block diagram of their class (D50, #201).

``docs/blocks/<class>.yaml`` lists, per instrument class, the real functional blocks and the
component types that may cover each. A definition covers a block when one of its components or
channel components has an accepted type. Blocks a built-in model does not cover yet are named in
``EXEMPT`` below; like ``ALLOWED`` in ``test_model_components.py``, the list may only shrink.

Keyed by ``instrument_class`` (the architecture follows the instrument, not the UI template).
Block lists may name component types that do not exist yet (future splits); the check only ever
runs against built-in definitions.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from simubench.library.loader import BUILTIN_DIR, COVERAGE_SUFFIX, DefinitionLibrary
from simubench.models.definition import InstrumentDefinition

BLOCKS_DIR = Path(__file__).resolve().parent.parent / "docs" / "blocks"

#: (definition id, block) — blocks the built-in models do not cover with their own components yet.
#: The debt each entry names is tracked in docs/component-map.md. May only shrink: a paid-off
#: entry fails the test until it is removed.
EXEMPT: set[tuple[str, str]] = {
    # siglent-sdg2042x: 参考、幅度、任意波表、调制由 siglent_sdg 专用组件或通道整块承担;
    # 拆分在第二波(缺口清单)。
    ("siglent-sdg2042x", "reference"),
    ("siglent-sdg2042x", "amplitude"),
    ("siglent-sdg2042x", "arb_memory"),
    ("siglent-sdg2042x", "modulation"),
}

#: (definition id, block) -> why — blocks the real instrument does not have at all, so there is
#: nothing to cover (not a debt: unlike EXEMPT it does not shrink). The reason is the manual's.
#: An entry whose block a component covers anyway is an error: the instrument has it after all.
NOT_APPLICABLE: dict[tuple[str, str], str] = {
    # M8194A: a baseband AWG, no AM/FM/PM/FSK/PWM (M8194-91020 lists none; data sheet 5992-3361).
    ("keysight-m8194a", "modulation"): "baseband AWG: the instrument has no modulation",
}


def _blocks(class_name: str) -> dict[str, set[str]] | None:
    path = BLOCKS_DIR / f"{class_name}.yaml"
    if not path.exists():
        return None
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if doc.get("schema") != "simubench.blocks/1" or doc.get("class") != class_name:
        raise ValueError(f"{path}: bad schema or class")
    return {b["block"]: set(b["components"]) for b in doc["blocks"]}


def builtin_definitions() -> list[InstrumentDefinition]:
    lib = DefinitionLibrary()
    ids = [p.stem for p in BUILTIN_DIR.glob("*.yaml") if not p.name.endswith(COVERAGE_SUFFIX)]
    return [lib.get(i) for i in sorted(ids)]


def problems(definitions: list[InstrumentDefinition]) -> list[str]:
    out = []
    needed: set[tuple[str, str]] = set()
    for d in definitions:
        blocks = _blocks(d.instrument_class)
        if blocks is None:
            out.append(f"{d.id}: no block list for class {d.instrument_class!r} (docs/blocks/)")
            continue
        types = {c.type for c in [*d.components, *d.channel_components]}
        for block, accepted in blocks.items():
            if (d.id, block) in NOT_APPLICABLE:
                if accepted & types:
                    out.append(
                        f"{d.id}: block {block!r} is listed in NOT_APPLICABLE but a component "
                        "covers it; remove the entry (tests/test_block_coverage.py)"
                    )
                continue
            if accepted & types:
                continue
            needed.add((d.id, block))
            if (d.id, block) not in EXEMPT:
                out.append(
                    f"{d.id}: block {block!r} of class {d.instrument_class!r} is not covered "
                    f"(accepted: {sorted(accepted)}); name it in EXEMPT (tests/test_block_coverage.py) "
                    "or cover it with a real component (D50)"
                )
    out += [
        f"{d}: block {b!r} no longer needs its exemption; remove it from EXEMPT "
        "(tests/test_block_coverage.py)"
        for d, b in sorted(set(EXEMPT) - needed)
    ]
    return out


def test_builtin_real_models_cover_their_blocks():
    assert problems(builtin_definitions()) == []


def test_a_new_definition_must_cover_every_block():
    acme = InstrumentDefinition.model_validate(
        {
            "schema": "simubench.instrument/1",
            "id": "acme-scope1",
            "manufacturer": "ACME",
            "class": "oscilloscope",
            "model": "CS1000",
            "version": 1,
            "description": "a scope with only a timebase",
            "components": [{"type": "scope_timebase", "name": "tb"}],
        }
    )
    found = [p for p in problems([acme]) if p.startswith("acme-scope1:")]
    missing = {p.split("'")[1] for p in found}
    assert missing == {
        "input_port",
        "decimator",
        "vertical",
        "probe",
        "adc",
        "acquisition",
        "trigger",
        "measurements",
        "waveform_transfer",
    }


def test_a_paid_off_block_must_leave_the_list():
    found = problems(builtin_definitions())
    assert found == []
    extra = EXEMPT | {("siglent-sds2104xplus", "vertical")}
    old = EXEMPT.copy()
    try:
        EXEMPT.clear()
        EXEMPT.update(extra)
        stale = [p for p in problems(builtin_definitions()) if "no longer needs" in p]
        assert stale == [
            "siglent-sds2104xplus: block 'vertical' no longer needs its exemption; "
            "remove it from EXEMPT (tests/test_block_coverage.py)"
        ]
    finally:
        EXEMPT.clear()
        EXEMPT.update(old)


def test_the_validation_smu_is_built_from_its_real_blocks():
    """#184 B1: the split is checkable on the definition the SMU tests actually run (validation/
    instruments), not only on built-in models — every smu block is covered by a real component."""
    root = Path(__file__).resolve().parent.parent
    d = DefinitionLibrary([root / "validation" / "instruments"]).get("sb-smu-1")
    blocks = _blocks(d.instrument_class)
    assert blocks is not None
    types = {c.type for c in [*d.components, *d.channel_components]}
    assert [block for block, accepted in blocks.items() if not accepted & types] == []


def test_the_validation_daq_is_built_from_its_real_blocks():
    """#191 E2: the DAQ definition the tests run covers the backplane, card, scan table and the
    internal DMM chain."""
    root = Path(__file__).resolve().parent.parent
    d = DefinitionLibrary([root / "validation" / "instruments"]).get("sb-daq-1")
    blocks = _blocks(d.instrument_class)
    assert blocks is not None
    types = {c.type for c in [*d.components, *d.channel_components]}
    assert [block for block, accepted in blocks.items() if not accepted & types] == []


def test_the_validation_spectrum_analyzer_is_built_from_its_real_blocks():
    """#187 D1: the analyzer definition covers the input, mixer, IF, detector, VBW, trace and the
    10 MHz reference."""
    root = Path(__file__).resolve().parent.parent
    d = DefinitionLibrary([root / "validation" / "instruments"]).get("sb-sa-1")
    blocks = _blocks(d.instrument_class)
    assert blocks is not None
    types = {c.type for c in [*d.components, *d.channel_components]}
    assert [block for block, accepted in blocks.items() if not accepted & types] == []


def test_the_validation_power_supply_is_built_from_its_real_blocks():
    """#185 B2: the supply definition covers the setpoint, loop, power stage, output, readback,
    protection and the cross-channel tracking."""
    root = Path(__file__).resolve().parent.parent
    d = DefinitionLibrary([root / "validation" / "instruments"]).get("sb-ps-1")
    blocks = _blocks(d.instrument_class)
    assert blocks is not None
    types = {c.type for c in [*d.components, *d.channel_components]}
    assert [block for block, accepted in blocks.items() if not accepted & types] == []


def test_the_validation_rf_generator_is_built_from_its_real_blocks():
    """#188 D2: the generator definition covers the reference, synthesizer, modulating source,
    modulator, level control, attenuator and the output."""
    root = Path(__file__).resolve().parent.parent
    d = DefinitionLibrary([root / "validation" / "instruments"]).get("sb-sg-1")
    blocks = _blocks(d.instrument_class)
    assert blocks is not None
    types = {c.type for c in [*d.components, *d.channel_components]}
    assert [block for block, accepted in blocks.items() if not accepted & types] == []


def test_the_validation_eload_is_built_from_its_real_blocks():
    """#186 C1: the load definition covers the input, power stage, mode loop, meter and protection."""
    root = Path(__file__).resolve().parent.parent
    d = DefinitionLibrary([root / "validation" / "instruments"]).get("sb-el-1")
    blocks = _blocks(d.instrument_class)
    assert blocks is not None
    types = {c.type for c in [*d.components, *d.channel_components]}
    assert [block for block, accepted in blocks.items() if not accepted & types] == []


def test_the_validation_lockin_is_built_from_its_real_blocks():
    """#189 D3: the lock-in definition covers the input filter and amplifier, the mixer, the
    reference, the low-pass, the output, the data buffer and the AUX block."""
    root = Path(__file__).resolve().parent.parent
    d = DefinitionLibrary([root / "validation" / "instruments"]).get("sb-lia-1")
    blocks = _blocks(d.instrument_class)
    assert blocks is not None
    types = {c.type for c in [*d.components, *d.channel_components]}
    assert [block for block, accepted in blocks.items() if not accepted & types] == []


def test_the_validation_counter_is_built_from_its_real_blocks():
    """#192 F: the counter definition covers the two inputs, the gate, the timebase, the counting
    core, the statistics and the trigger."""
    root = Path(__file__).resolve().parent.parent
    d = DefinitionLibrary([root / "validation" / "instruments"]).get("sb-counter-1")
    blocks = _blocks(d.instrument_class)
    assert blocks is not None
    types = {c.type for c in [*d.components, *d.channel_components]}
    assert [block for block, accepted in blocks.items() if not accepted & types] == []


def test_block_lists_are_well_formed():
    classes = {d.instrument_class for d in builtin_definitions()}
    listed = {p.stem for p in BLOCKS_DIR.glob("*.yaml")}
    assert classes <= listed, f"built-in classes without a block list: {sorted(classes - listed)}"
    for path in BLOCKS_DIR.glob("*.yaml"):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert doc.get("schema") == "simubench.blocks/1"
        names = [b["block"] for b in doc["blocks"]]
        assert len(names) == len(set(names)), f"{path.name}: duplicate blocks"
        assert all(b["components"] for b in doc["blocks"]), f"{path.name}: block without components"
