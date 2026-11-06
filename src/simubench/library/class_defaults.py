"""Category default blocks and the deep merge behind them (instrument-authoring.md 2.9).

The library ships one default block per instrument class; today it holds the generic errors table
the built-in definitions used to copy verbatim into every file. The loader merges the block of the
definition's ``class`` under the definition's own data before ``model_validate`` (definition data
wins key by key, ``errors`` by code), so a file only lists the codes whose wording or set differs
from its class. The default serves real models (D42, a ``reference``): a table entry replaces the
engine's "standard text; detail" reply, which a definition without a reference relies on.

The same deep merge carries sibling inheritance: a definition with ``extends`` starts from the
named built-in model (the loader resolves its chain first), then merges its own data on top. The
order is 类别默认 → 父 → 子; dicts merge recursively, lists are replaced whole, a single parent,
no cycles (the loader checks; this module is pure).

``CLASS_ERRORS`` holds, per class, the entries every built-in file of that class carried with the
same wording — wording that differs or codes that one model alone defines stay in that model's
file and win over the table. When a model joins a class, extend its table only with entries the
new model's manual states the same way; anything else stays in the file.
"""

from __future__ import annotations

from typing import Any

#: The generic error texts per class: what every built-in definition of the class stated, verbatim.
CLASS_ERRORS: dict[str, dict[int, str]] = {
    "calibrator": {
        0: "No Error",
    },
    "daq": {
        -440: "Query UNTERMINATED after indefinite response",
        -430: "Query DEADLOCKED",
        -420: "Query UNTERMINATED",
        -410: "Query INTERRUPTED",
        -400: "Query error",
        -350: "Error queue overflow",
        -310: "System error",
        -300: "Device-specific error",
        -261: "Math error in expression",
        -260: "Expression error",
        -241: "Hardware missing",
        -240: "Hardware error",
        -231: "Data questionable",
        -225: "Out of memory",
        -224: "Illegal parameter value",
        -223: "Too much data",
        -222: "Data out of range",
        -221: "Settings conflict",
        -220: "Parameter error",
        -214: "Trigger deadlock",
        -213: "INIT ignored",
        -211: "Trigger ignored",
        -200: "Execution error",
        -178: "Expression data not allowed",
        -171: "Invalid expression",
        -170: "Expression error",
        -168: "Block data not allowed",
        -161: "Invalid block data",
        -160: "Block data error",
        -158: "String data not allowed",
        -151: "Invalid string data",
        -150: "String data error",
        -148: "Character data not allowed",
        -144: "Character data too long",
        -141: "Invalid character data",
        -140: "Character data error",
        -138: "Suffix not allowed",
        -134: "Suffix too long",
        -131: "Invalid suffix",
        -130: "Suffix error",
        -128: "Numeric data not allowed",
        -124: "Too many digits",
        -123: "Numeric overflow",
        -121: "Invalid character in number",
        -120: "Numeric data error",
        -114: "Header suffix out of range",
        -113: "Undefined header",
        -112: "Program mnemonic too long",
        -111: "Header separator error",
        -110: "Command header error",
        -109: "Missing parameter",
        -108: "Parameter not allowed",
        -105: "GET not allowed",
        -104: "Data type error",
        -103: "Invalid separator",
        -102: "Syntax error",
        -101: "Invalid character",
        -100: "Command error",
        0: "No error",
    },
    "frequency_counter": {
        -440: "Query UNTERMINATED after indefinite response",
        -430: "Query DEADLOCKED",
        -420: "Query UNTERMINATED",
        -410: "Query INTERRUPTED",
        -350: "Error queue overflow",
        -314: "Save/recall memory lost;memory corruption detected",
        -313: "Calibration memory lost;memory corruption detected",
        -310: "System Error;internal software error",
        -241: "Hardware missing",
        -240: "Hardware error",
        -230: "Data corrupt or stale",
        -224: "Illegal Parameter Value",
        -223: "Too much data",
        -222: "Data out of range",
        -221: "Settings conflict",
        -214: "Trigger deadlock",
        -213: "INIT ignored",
        -211: "Trigger ignored",
        -203: "Command Protected",
        -178: "Expression data not allowed",
        -168: "Block data not allowed",
        -158: "String data not allowed",
        -151: "Invalid string data",
        -148: "Character data not allowed",
        -141: "Invalid character data",
        -138: "Suffix not allowed",
        -131: "Invalid suffix",
        -130: "Suffix error",
        -128: "Numeric data not allowed",
        -120: "Numeric data error",
        -114: "Header suffix out of range",
        -113: "Undefined header",
        -110: "Command header error",
        -109: "Missing parameter",
        -108: "Parameter not allowed",
        -104: "Data type error",
        -103: "Invalid separator",
        -102: "Syntax error",
        -101: "Invalid character",
        -100: "Command error",
        0: "No error",
    },
    "lockin": {
        -230: "Data corrupt or stale",
        -224: "Illegal parameter value",
        -222: "Data out of range",
        -221: "Settings conflict",
        -213: "Init ignored",
        -211: "Trigger ignored",
        -113: "Undefined header",
        -109: "Missing parameter",
        -108: "Parameter not allowed",
        -104: "Data type error",
        0: "No error",
    },
    "multimeter": {
        -350: "Queue overflow",
        -310: "System error",
        -230: "Data corrupt or stale",
        -224: "Illegal parameter value",
        -223: "Too much data",
        -222: "Data out of range",
        -221: "Settings conflict",
        -214: "Trigger deadlock",
        -213: "Init ignored",
        -211: "Trigger ignored",
        -151: "Invalid string data",
        -131: "Invalid suffix",
        -113: "Undefined header",
        -109: "Missing parameter",
        -108: "Parameter not allowed",
        -104: "Data type error",
        -102: "Syntax error",
        0: "No error",
    },
    "power_supply": {
        -440: "Query UNTERMINATED after indefinite response",
        -430: "Query DEADLOCKED",
        -420: "Query UNTERMINATED",
        -410: "Query INTERRUPTED",
        -241: "Hardware missing",
        -240: "Hardware error",
        -231: "Data questionable",
        -230: "Data corrupt or stale",
        -225: "Out of memory",
        -224: "Illegal parameter value",
        -223: "Too much data",
        -222: "Data out of range",
        -221: "Settings conflict",
        -220: "Parameter error",
        -200: "Execution error",
        -158: "String data not allowed",
        -151: "Invalid string data",
        -150: "String data error",
        -148: "Character data not allowed",
        -144: "Character data too long",
        -141: "Invalid character data",
        -140: "Character data error",
        -138: "Suffix not allowed",
        -134: "Suffix too long",
        -131: "Invalid suffix",
        -130: "Suffix error",
        -128: "Numeric data not allowed",
        -124: "Too many digits",
        -121: "Invalid character in number",
        -120: "Numeric data error",
        -114: "Header suffix out of range",
        -113: "Undefined header",
        -112: "Program mnemonic too long",
        -111: "Header separator error",
        -110: "Command header error",
        -109: "Missing parameter",
        -108: "Parameter not allowed",
        -105: "GET not allowed",
        -104: "Data type error",
        -103: "Invalid separator",
        -102: "Syntax error",
        -101: "Invalid character",
        -100: "Command error",
        0: "No error",
    },
    "smu": {
        -440: "Query UNTERMINATED after indefinite response",
        -430: "Query DEADLOCKED",
        -420: "Query UNTERMINATED",
        -410: "Query INTERRUPTED",
        -400: "Query error",
        -350: "Queue overflow",
        -321: "Out of memory",
        -315: "Configuration memory lost",
        -313: "Calibration memory lost",
        -311: "Memory error",
        -310: "System error",
        -300: "Device-specific error",
        -241: "Hardware missing",
        -240: "Hardware error",
        -233: "Invalid version",
        -232: "Invalid format",
        -231: "Data questionable",
        -230: "Data corrupt or stale",
        -224: "Illegal parameter value",
        -223: "Too much data",
        -222: "Data out of range",
        -221: "Settings conflict",
        -220: "Parameter error",
        -214: "Trigger deadlock",
        -213: "Init ignored",
        -211: "Trigger ignored",
        -200: "Execution error",
        -178: "Expression data not allowed",
        -171: "Invalid expression",
        -170: "Expression error",
        -168: "Block data not allowed",
        -161: "Invalid block data",
        -160: "Block data error",
        -158: "String data not allowed",
        -151: "Invalid string data",
        -150: "String data error",
        -148: "Character data not allowed",
        -144: "Character data too long",
        -141: "Invalid character data",
        -140: "Character data error",
        -138: "Suffix not allowed",
        -134: "Suffix too long",
        -131: "Invalid suffix",
        -130: "Suffix error",
        -128: "Numeric data not allowed",
        -124: "Too many digits",
        -123: "Exponent too large",
        -121: "Invalid character in number",
        -120: "Numeric data error",
        -114: "Header suffix out of range",
        -113: "Undefined header",
        -112: "Program mnemonic too long",
        -111: "Header separator error",
        -110: "Command header error",
        -109: "Missing parameter",
        -108: "Parameter not allowed",
        -105: "GET not allowed",
        -104: "Data type error",
        -103: "Invalid separator",
        -102: "Syntax error",
        -101: "Invalid character",
        -100: "Command error",
        0: "No error",
    },
}


def default_block(instrument_class: object) -> dict[str, Any]:
    """The class's default block: a partial definition merged under the definition's own data.

    Empty (no defaults at all) for a class the library has no table for."""
    table = CLASS_ERRORS.get(instrument_class) if isinstance(instrument_class, str) else None
    if not table:
        return {}
    return {"errors": dict(table)}


def deep_merge(base: dict, override: dict) -> dict:
    """Two definition data blocks merged deep: dicts recurse key by key (``errors`` by code, a
    setting by field), lists and every other value are replaced whole. ``override`` wins; neither
    input is changed. A ``None`` in ``override`` is kept as the value (a deletion marker for
    :func:`drop_deleted`, which the layering calls once after every layer is merged: dropping it
    here would let a lower layer bring the key back)."""
    out = dict(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def drop_deleted(data: Any) -> Any:
    """``data`` without the dict keys whose value is ``None`` (recursively through dicts and the
    dicts inside lists): the deletion markers of an ``extends`` child (D59, ``key: null`` drops the
    inherited key, an ``errors`` code, a setting, a command field). A copy; a list is not
    thinned — its items are kept whole, as a list is replaced whole."""
    if isinstance(data, dict):
        return {k: drop_deleted(v) for k, v in data.items() if v is not None}
    if isinstance(data, list):
        return [drop_deleted(v) for v in data]
    return data
