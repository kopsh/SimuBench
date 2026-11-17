"""Every enum option of a built-in model has a Chinese name (#1257): the inspector shows "中文名 +
灰色助记符", never the bare mnemonic. A name comes from the setting's own ``option_labels``, the
shared word list ``web/src/i18n/option-words.json`` (by the option as written, then its SCPI short
form) or a numbered family there (``CHAN#`` → 通道 1). The Web's ``optionLabel`` looks names up the
same way."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simubench.library.loader import BUILTIN_DIR, COVERAGE_SUFFIX, DefinitionLibrary

ROOT = Path(__file__).resolve().parents[1]
WORDS: dict[str, list[str]] = json.loads(
    (ROOT / "web/src/i18n/option-words.json").read_text(encoding="utf-8")
)
#: Cases the Python and the TypeScript lookup must agree on; ``web/src/features/workbench/model.test.ts``
#: runs the same table through the Web's ``wordOf`` (#1286). One list, one verdict — a divergence
#: fails here and there at once.
CASES: list[list[str | None]] = json.loads(
    (ROOT / "web/src/i18n/option-words.cases.json").read_text(encoding="utf-8")
)
#: Settings whose options are identities, not words — the option reads as itself, a name would be
#: wrong. Each is a real built-in enum (checked 2026-11, #1286): ``serial`` (system.serial of the
#: DSO-X 2024A and both UXR), ``model`` (the UXR's system.model), ``module`` (N6705C's plug-in
#: module), ``options`` (M8194A's system.options — an option list, not a word), ``language``
#: (53220A/53230A/DSG815 menu language — a language code), ``installed_options`` (FLUKE 5522A).
IDENTITIES = ("serial", "model", "module", "options", "language", "installed_options")


def short_form(option: str) -> str:
    s = "".join(c for c in option if c.isupper() or c.isdigit())
    return s or option.upper()


def word_of(option: str) -> str | None:
    """The option's Chinese name from the shared list, as the Web looks it up."""
    if option in WORDS:
        return WORDS[option][0]
    short = short_form(option)
    if short in WORDS:
        return WORDS[short][0]
    letters = short.rstrip("0123456789")
    digits = short[len(letters) :]
    if letters and digits and f"{letters}#" in WORDS:
        return WORDS[f"{letters}#"][0].replace("{n}", digits)
    return None


def is_number(option: str) -> bool:
    """A value that reads as itself: 10, 10k, 100M, (@1)."""
    core = option.strip("()@").lstrip("+-").rstrip("kKMG")
    return core.replace(".", "", 1).isdigit()


def test_every_built_in_enum_option_has_a_chinese_name():
    lib = DefinitionLibrary()
    builtin = sorted(p.stem for p in BUILTIN_DIR.glob("*.yaml") if not p.name.endswith(COVERAGE_SUFFIX))
    missing: list[str] = []
    for def_id in builtin:
        d = lib.get(def_id)
        for path, spec in (*d.state.items(), *d.channel_state.items()):
            if spec.type != "enum" or path.rsplit(".", 1)[-1] in IDENTITIES:
                continue
            for option in spec.options or []:
                if (spec.option_labels or {}).get(option) or is_number(option) or word_of(option):
                    continue
                missing.append(f"{def_id} {path}: {option}")
    assert missing == [], f"{len(missing)} options without a Chinese name, e.g. {missing[:20]}"


def test_the_word_list_names_numbered_families_and_keeps_both_languages():
    assert word_of("CHANnel3") == "通道 3"
    assert word_of("FUNCtion12") == "函数 12"
    assert word_of("COMMa") == "逗号" and word_of("COMMonmode") == "共模"  # one short form, two words
    assert word_of("PERCent") == "百分比"
    assert all(len(v) == 2 and v[0] and v[1] for v in WORDS.values())


@pytest.mark.parametrize(("option", "zh"), CASES)
def test_the_word_list_agrees_with_the_web_lookup(option: str, zh: str | None):
    """The shared case table runs through both lookups — the Web's vitest runs the same JSON
    through ``wordOf`` (#1286), so the two cannot drift apart silently."""
    assert word_of(option) == zh
