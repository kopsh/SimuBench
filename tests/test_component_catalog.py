"""The component catalog (instrument-authoring.md 5.3, issue 41): every component type with its ports,
parameters and operations, read from the class itself."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from fastapi.testclient import TestClient

from simubench.engine.component import component_types
from simubench.engine.validate_definition import operations
from simubench.server.app import create_app
from simubench.server.catalog import components, param_docs
from simubench.server.manager import SessionManager

ROOT = Path(__file__).resolve().parents[1]


def test_every_registered_type_with_its_operations():
    catalog = {c.type: c for c in components()}
    assert set(catalog) == set(component_types())  # internal ones included
    for name, cls in component_types().items():
        assert [o.name for o in catalog[name].operations] == operations(cls)
        assert [p.name for p in catalog[name].params] == list(cls.defaults)


def test_operation_parameters_come_from_the_signature():
    acq = next(c for c in components() if c.type == "scope_acquisition")
    ops = {o.name: o for o in acq.operations}
    [mode] = ops["run_mode"].params  # query, channel and suffixes are the instrument's
    assert (mode.name, mode.required, mode.default) == ("mode", False, None)
    assert ops["wait"].params[0].name == "timeout"
    assert ops["run_mode"].doc and "single" in ops["run_mode"].doc


def test_parameter_descriptions_are_the_comments_next_to_the_defaults():
    acq = next(c for c in components() if c.type == "scope_acquisition")
    params = {p.name: p for p in acq.params}
    assert params["reacquire"].doc == "explicit | every_read | on_read_start"
    timebase = next(c for c in components() if c.type == "scope_timebase")
    assert (
        next(p for p in timebase.params if p.name == "record_length").doc
        == "fixed | exact | rate_ladder | rate_pow2"
    )
    trigger = next(c for c in components() if c.type == "scope_trigger")
    assert (
        next(p for p in trigger.params if p.name == "level_range_divs").doc
        == "the level stays within +-this many divisions of the source's screen"
    )
    assert next(p for p in trigger.params if p.name == "rising").default == [
        "POSitive",
        "EITHer",
        "RISing",
        "ALTernate",
    ]
    front = next(c for c in components() if c.type == "scope_frontend")
    assert next(p for p in front.params if p.name == "bandwidth").unit == "Hz"
    assert next(p for p in front.params if p.name == "coupling").doc == "DC | AC | GND"  # inherited
    assert acq.role is None and acq.port_pattern == r"in\d+"


def test_comment_lines_above_a_default_describe_it():
    from simubench.engine.component import Component

    class Demo(Component):
        defaults: ClassVar[dict[str, Any]] = {
            # The first line
            # and the second.
            "a": 1,
            "b": 2,  # trailing
            "c": 3,
        }

    assert param_docs(Demo) == {"a": "The first line and the second.", "b": "trailing"}


def test_every_parameter_has_a_description():
    for name, cls in sorted(component_types().items()):
        docs = param_docs(cls)
        missing = [k for k in cls.defaults if not docs.get(k)]
        assert not missing, f"{name}: parameters without a description: {missing}"


def test_the_components_endpoint():
    app = create_app(SessionManager(ROOT / "validation", scpi_base_port=None), token="t", web_dist=Path("/x"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        assert c.get("/api/components").status_code == 401
        body = c.get("/api/components", headers={"x-simubench-token": "t"}).json()
    by_type = {c["type"]: c for c in body}
    assert by_type["output_stage"]["role"] == "driver"
    assert {
        "name": "pin",
        "direction": "out",
        "medium": "electrical",
        "net": None,
        "channel": None,
    } in by_type["output_stage"]["ports"]
    assert by_type["waveform_transfer"]["operations"][0]["name"] == "data"


# ---- the hook contract (#898) ------------------------------------------------------------------


def _hooks() -> dict[str, Any]:
    from simubench.engine.component import Component

    return {n: f for n, f in vars(Component).items() if callable(f) and not n.startswith("_")}


def test_overridden_hooks_accept_the_base_signature():
    """A subclass that overrides a ``Component`` hook takes the same leading parameters (by name
    and position) and adds only optional ones, so a caller holding a ``Component`` never needs to
    know the concrete class."""
    import inspect

    broken = []
    for tname, cls in component_types().items():
        for name, base in _hooks().items():
            fn = getattr(cls, name, None)
            if fn is None or fn is base:
                continue
            want = list(inspect.signature(base).parameters.values())[1:]
            got = list(inspect.signature(fn).parameters.values())[1:]
            kinds = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
            leading_ok = all(
                w.kind in kinds or (i < len(got) and got[i].name == w.name) for i, w in enumerate(want)
            )
            required_extra = [p for p in got[len(want) :] if p.default is p.empty and p.kind not in kinds]
            if not leading_ok or required_extra:
                broken.append(
                    f"{tname}.{name}{inspect.signature(fn)} vs Component.{name}{inspect.signature(base)}"
                )
    assert not broken, "\n".join(broken)


def test_no_operation_shadows_a_component_hook():
    """An ``@op`` method named like a hook (``describe``, ``sync``, ``abort``) answers the hook's
    callers with an ``OpResult``: name the method ``op_<name>`` and keep the SCPI name in ``@op``."""
    clashes = [
        f"{tname}.{name}"
        for tname, cls in component_types().items()
        for name in _hooks()
        if hasattr(getattr(cls, name, None), "__op_name__")
    ]
    assert not clashes, clashes


def test_retry_pending_flag_matches_the_override():
    from simubench.engine.component import Component

    for tname, cls in component_types().items():
        overrides = cls.retry_pending is not Component.retry_pending
        assert cls.has_retry_pending == overrides, tname
