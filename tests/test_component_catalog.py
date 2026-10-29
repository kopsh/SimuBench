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
    acq = next(c for c in components() if c.type == "siglent_sds_acquisition")
    params = {p.name: p for p in acq.params}
    assert params["record_length"].doc == "fixed | exact | rate_ladder"  # inherited from scope_acquisition
    assert params["level_divs"].doc == "trigger level range: +-4.1 div around the offset"
    assert params["rising"].default == ["POSitive", "EITHer", "RISing", "ALTernate"]
    front = next(c for c in components() if c.type == "scope_frontend")
    assert next(p for p in front.params if p.name == "bandwidth").unit == "Hz"
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
