"""The component catalog (instrument-authoring.md 5.3, issue 41): every registered component type,
internal ones included, with its ports, parameters and operations, read from the class itself.

Nothing is written twice: parameters are the class's ``defaults`` (with ``units``), each explained
by the comment next to it in the source; operations are the ``@op`` methods, their keyword
parameters taken from the signature and their description from the docstring.
"""

from __future__ import annotations

import ast
import inspect
import io
import textwrap
import tokenize
from functools import lru_cache
from typing import Any

from ..contracts import api as M
from ..engine.component import Component, component_types
from ..engine.validate import drives_net

#: Arguments every operation receives from the instrument, not from a command's parameters.
_OP_CONTEXT = {"self", "query", "channel", "suffixes"}

_cache: dict[type[Component], M.ComponentDoc] = {}

#: The library family of each module under ``library/components`` (#1214): what the build area's
#: component library groups by. A new module is added here; ``test_component_catalog`` checks it.
GROUPS: dict[str, M.LibraryGroup] = {
    **dict.fromkeys(
        (
            "electrical",
            "optical",
            "sampling",
            "adc",
            "modulation",
            "transfer",
            "curve",
            "ranging",
            "protection",
            "daq",
        ),
        "signal",
    ),
    **dict.fromkeys(
        (
            "meters",
            "measurements",
            "counter",
            "lockin",
            "reading_math",
            "specan",
            "specan_backend",
            "specan_detect",
            "specan_frontend",
            "specan_markers",
            "specan_state",
            "specan_sweep",
        ),
        "measure",
    ),
    **dict.fromkeys(
        (
            "sources",
            "power",
            "smu",
            "eload",
            "rfgen",
            "calibrator",
            "cal_output",
            "arb_segments",
            "siglent_sdg",
        ),
        "source",
    ),
    **dict.fromkeys(
        (
            "scope",
            "scope_acquisition",
            "scope_acquisition_host",
            "scope_autoscale",
            "scope_capture",
            "scope_dsp",
            "scope_math",
            "scope_math_controls",
            "scope_math_fft",
            "scope_math_host",
            "scope_math_ops",
            "scope_cursors",
            "scope_edges",
            "scope_kmarkers",
            "scope_measurements",
            "scope_measurements_host",
            "scope_pending",
            "scope_readouts",
            "scope_recorder",
            "scope_record",
            "scope_results",
            "scope_run",
            "scope_segments",
            "scope_trigger",
            "scope_trigger_advanced",
            "scope_trigger_crossings",
            "scope_trigger_host",
            "scope_trigger_runs",
            "scope_trigger_sources",
            "scope_values",
        ),
        "scope",
    ),
    "trigger": "trigger",
    **dict.fromkeys(("calstore", "datafiles", "files", "setups", "status", "system"), "system"),
}


@lru_cache(maxsize=1)
def class_usage() -> dict[str, frozenset[str]]:
    """Component type -> the instrument classes whose built-in models use it (P2-4): read from the
    shipped library, so a new model or component needs no list kept by hand. A definition that does
    not load is skipped (its own checks report it)."""
    from ..library.loader import BUILTIN_DIR, DefinitionLibrary

    lib = DefinitionLibrary([BUILTIN_DIR])
    usage: dict[str, set[str]] = {}
    for def_id in lib.ids():
        try:
            d = lib(def_id)
        except Exception:
            continue
        for c in [*d.components, *(d.channel_components or [])]:
            usage.setdefault(c.type, set()).add(d.instrument_class)
    return {k: frozenset(v) for k, v in usage.items()}


def group_of(cls: type[Component]) -> M.LibraryGroup:
    """The library family of a component class, from the module it is defined in."""
    module = cls.__module__
    prefix = "simubench.library.components."
    return GROUPS.get(module[len(prefix) :], "other") if module.startswith(prefix) else "other"


def components() -> list[M.ComponentDoc]:
    """All registered component types, by name."""
    return [component_info(name, cls) for name, cls in sorted(component_types().items())]


def component_info(name: str, cls: type[Component]) -> M.ComponentDoc:
    if cls not in _cache:
        docs = param_docs(cls)
        _cache[cls] = M.ComponentDoc(
            type=name,
            category=cls.category,  # type: ignore[arg-type]
            role=cls.role,
            generic=cls.generic,
            label=cls.label or None,
            label_en=cls.label_en or None,
            about=cls.about or None,
            about_en=cls.about_en or None,
            group=group_of(cls),
            classes=sorted(class_usage().get(name, ())),
            doc=inspect.getdoc(cls),
            ports=[
                M.PortInfo(name=p, direction=s.direction, medium=s.medium, drives=drives_net(cls, s))
                for p, s in cls.ports.items()
            ],
            port_pattern=cls.port_pattern,
            params=[
                M.ParamInfo(name=k, default=_json(v), unit=cls.units.get(k), doc=docs.get(k))
                for k, v in cls.defaults.items()
            ],
            operations=operations(cls),
            refs=[
                M.RefInfo(
                    param=k,
                    scope=scope,
                    optional=k in cls.optional_refs,
                    target=target if isinstance(target, str) else None,
                )
                for k, (target, scope) in cls.refs.items()
            ],
        )
    return _cache[cls]


def operations(cls: type[Component]) -> list[M.OperationInfo]:
    found: dict[str, M.OperationInfo] = {}
    for attr in dir(cls):
        fn = getattr(cls, attr)
        name = getattr(fn, "__op_name__", None)
        if name is None or name in found:
            continue
        params = []
        for p in inspect.signature(fn).parameters.values():
            if p.name in _OP_CONTEXT or p.kind in (p.VAR_KEYWORD, p.VAR_POSITIONAL):
                continue
            required = p.default is p.empty
            params.append(
                M.OperationParam(
                    name=p.name,
                    type=None if p.annotation is p.empty else str(p.annotation),
                    default=None if required else _json(p.default),
                    required=required,
                )
            )
        found[name] = M.OperationInfo(name=name, doc=inspect.getdoc(fn), params=params)
    return [found[k] for k in sorted(found)]


def param_docs(cls: type[Component]) -> dict[str, str]:
    """The comment next to each default in ``defaults`` (or the comment lines just above it),
    through the base classes whose defaults the class spreads in."""
    docs: dict[str, str] = {}
    for klass in reversed(cls.__mro__):
        if "defaults" in vars(klass):
            docs.update(_own_param_docs(klass))
    return docs


def _own_param_docs(klass: type) -> dict[str, str]:
    try:
        source = textwrap.dedent(inspect.getsource(klass))
    except (OSError, TypeError):  # no source (built without it): no descriptions
        return {}
    comments: dict[int, str] = {}
    code_lines: set[int] = set()
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            comments[tok.start[0]] = tok.string.lstrip("#").strip()
        elif tok.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
            code_lines.add(tok.start[0])
    tree = ast.parse(source)
    body = tree.body[0].body if tree.body and isinstance(tree.body[0], ast.ClassDef) else []
    out: dict[str, str] = {}
    for node in body:
        target = node.targets[0] if isinstance(node, ast.Assign) else getattr(node, "target", None)
        value = getattr(node, "value", None)
        if not (isinstance(target, ast.Name) and target.id == "defaults" and isinstance(value, ast.Dict)):
            continue
        for key, val in zip(value.keys, value.values, strict=True):
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                continue  # **Base.defaults
            trailing = [
                comments[n] for n in range(key.lineno, (val.end_lineno or key.lineno) + 1) if n in comments
            ]
            above = []
            n = key.lineno - 1
            while n in comments and n not in code_lines:  # whole comment lines right above the key
                above.insert(0, comments[n])
                n -= 1
            text = " ".join(above + trailing).strip()
            if text:
                out[key.value] = text
    return out


def _json(value: Any) -> Any:
    """Defaults as JSON: tuples as lists, infinity as the bench files spell it."""
    if isinstance(value, float) and value in (float("inf"), float("-inf")):
        return "INF" if value > 0 else "-INF"
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)
