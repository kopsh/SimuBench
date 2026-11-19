"""The command list is executable, for every model (#1565): ``:SYSTem:HELP:HEADers?`` is generated
from :func:`entries`, so every line must be a command :func:`classify` supports, and every command
of the definition for the fitted options must be listed — for every option combination. A
parameter's value is not part of the header match, so a header that needs parameters is checked by
its presence only (issue 1565).

The sweep is one test per model; it needs no bench (definitions and patterns only), so the unit
tier's one-behaviour budget does not apply."""

from __future__ import annotations

from itertools import combinations

import pytest

from simubench.engine.commands import _header_key, classify
from simubench.engine.help_text import entries
from simubench.library.loader import DefinitionLibrary
from simubench.scpi.parse import Pattern

LIB = DefinitionLibrary()


def _option_sets(definition):
    ids = [option.id for option in definition.options or []]
    return [combination for n in range(len(ids) + 1) for combination in combinations(ids, n)]


def _message(header: str) -> str:
    """A concrete message for a listed header: long mnemonics and the first suffix. Long forms
    keep a mnemonic that carries a digit (``X1Y1source``) whole once parsed again."""
    return Pattern.compile(header).echo({}, long=True)


@pytest.mark.parametrize("model", sorted(LIB.ids()))
def test_every_listed_header_is_executable(model):
    definition = LIB.get(model)
    for options in _option_sets(definition):
        listed = entries(definition, options)
        for entry in listed:
            if entry.params:
                continue  # a parameter's value is not part of the header match
            message = _message(entry.header)
            for query in ([True] if entry.query else []) + ([False] if entry.set else []):
                match = classify(definition, message + ("?" if query else ""))[0]
                assert match.supported, (model, options, message, match.reason)
        keys = {_header_key(entry.header) for entry in listed}
        for command in definition.commands:
            if command.option and command.option not in options:
                continue
            assert _header_key(command.header) in keys, (model, options, command.header)
