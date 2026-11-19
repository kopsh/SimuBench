"""The component catalog (instrument-authoring.md 5.3, issue 41): every component type with its ports,
parameters and operations, read from the class itself."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, ClassVar

from fastapi.testclient import TestClient

from simubench.engine.component import component_types
from simubench.engine.validate_definition import operations
from simubench.server.app import create_app
from simubench.server.benchfiles import _json_number
from simubench.server.catalog import GROUPS, components, param_docs
from simubench.server.manager import SessionManager

ROOT = Path(__file__).resolve().parents[1]


def test_non_finite_defaults_echo_jsonsafe_spelling():
    """The catalog's parameter defaults reach JSON through ``_json_number``: the one spelling of
    what JSON cannot carry (D60, simubench.jsonsafe) — INF, -INF and NAN, not a re-spelled INF
    pair that forgets NaN."""
    assert _json_number(math.inf) == "INF" and _json_number(-math.inf) == "-INF"
    assert _json_number(math.nan) == "NAN"
    assert _json_number(5.0) == 5.0 and _json_number("1 kS/s") == "1 kS/s" and _json_number(None) is None


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


def test_every_built_in_type_has_a_name_and_a_one_sentence_description_in_both_languages():
    """The UI shows the name first and the type as a grey tag (#1251): no fallback to the bare type."""
    for name, cls in sorted(component_types().items()):
        if not cls.__module__.startswith("simubench.library.components."):
            continue  # other tests register their own classes
        missing = [a for a in ("label", "label_en", "about", "about_en") if not getattr(cls, a)]
        assert not missing, f"{name}: missing {missing}"
        assert any("\u4e00" <= ch <= "\u9fff" for ch in cls.label), f"{name}: label is not Chinese"
        assert cls.label != name and cls.label_en != name, f"{name}: the name repeats the type"
        assert cls.about.endswith("。") and cls.about.count("。") == 1, f"{name}: not one sentence"
        assert cls.about_en.endswith(".") and "\n" not in cls.about_en, f"{name}: not one sentence"


def test_the_components_endpoint():
    app = create_app(SessionManager(ROOT / "validation", scpi_base_port=None), token="t", web_dist=Path("/x"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        assert c.get("/api/components").status_code == 401
        body = c.get("/api/components", headers={"x-simubench-token": "t"}).json()
    by_type = {c["type"]: c for c in body}
    assert by_type["output_stage"]["role"] == "driver"
    assert (by_type["scope_vertical"]["label"], by_type["scope_vertical"]["label_en"]) == (
        "垂直通道",
        "Vertical channel",
    )
    assert by_type["scope_vertical"]["about"] and by_type["scope_vertical"]["about_en"]
    assert {
        "name": "pin",
        "direction": "out",
        "medium": "electrical",
        "net": None,
        "channel": None,
        "drives": True,  # a source's output: one per net (#1269)
    } in by_type["output_stage"]["ports"]
    # the operations list is alphabetical; the point here is that the transfer's main op is in it
    assert "data" in {o["name"] for o in by_type["waveform_transfer"]["operations"]}
    # refs parameters (#1201): the build area offers the components a parameter may name
    refs = {r["param"]: r for r in by_type["waveform_transfer"]["refs"]}
    assert refs and all(r["scope"] in ("instrument", "channel") for r in refs.values())
    assert by_type["output_stage"]["refs"] == []
    # the library family (#1214): every internal component has one, from its module
    assert by_type["trigger_model"]["group"] == "trigger"
    assert by_type["calibration_store"]["group"] == "system"
    types = component_types()  # other tests register their own classes; only the library's count
    internal = [
        c
        for c in body
        if c["category"] == "internal"
        and types[c["type"]].__module__.startswith("simubench.library.components.")
    ]
    assert internal and all(c["group"] != "other" for c in internal)
    modules = {
        p.stem for p in (ROOT / "src/simubench/library/components").glob("*.py") if p.stem != "__init__"
    }
    assert modules <= set(GROUPS), modules - set(GROUPS)


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


def _compatible(base: Any, fn: Any) -> bool:
    """Whether ``fn`` accepts every call the base signature accepts: same leading parameter names,
    and only optional ones added."""
    import inspect

    kinds = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    want = list(inspect.signature(base).parameters.values())
    got = list(inspect.signature(fn).parameters.values())
    want = [p for p in want if p.name != "self"]
    got = [p for p in got if p.name != "self"]
    leading = all(w.kind in kinds or (i < len(got) and got[i].name == w.name) for i, w in enumerate(want))
    extra = [p for p in got[len(want) :] if p.default is p.empty and p.kind not in kinds]
    return leading and not extra


def test_no_operation_overrides_any_inherited_method():
    """#929: an ``@op`` method answers with an ``OpResult``; one that takes the name of a method a
    base class or mixin defines (``Triggerable.abort``) hands that method's callers the wrong
    thing. The rule is wider than ``Component``'s own hooks: it covers every class in the MRO."""
    clashes = []
    for tname, cls in component_types().items():
        for name, fn in vars(cls).items():
            if not callable(fn) or not hasattr(fn, "__op_name__") or name.startswith("_"):
                continue
            for base in cls.__mro__[1:]:
                inherited = vars(base).get(name)
                if callable(inherited) and not hasattr(inherited, "__op_name__"):
                    clashes.append(f"{tname}.{name} overrides {base.__name__}.{name}")
                    break
    assert not clashes, "\n".join(clashes)


def test_implementations_of_a_protocol_match_its_signatures():
    """#929: a component that fills a ``Protocol`` ref (``Acquisition``, ``TraceSource``, …) has
    each of the protocol's methods with a signature that accepts the protocol's calls."""
    import inspect

    from typing_extensions import get_protocol_members

    from simubench.engine import component as C
    from simubench.engine.validate_definition import _protocol_gaps

    protocols = [
        v
        for v in vars(C).values()
        if inspect.isclass(v) and getattr(v, "_is_protocol", False) and v.__module__ == C.__name__
    ]
    assert len(protocols) >= 5
    broken, implementations = [], 0
    for proto in protocols:
        methods = [
            n
            for n in get_protocol_members(proto)
            if not n.startswith("_") and callable(getattr(proto, n, None))
        ]
        for tname, cls in component_types().items():
            if _protocol_gaps(cls, proto):
                continue
            implementations += 1
            for name in methods:
                if not _compatible(getattr(proto, name), getattr(cls, name)):
                    broken.append(
                        f"{tname}.{name}{inspect.signature(getattr(cls, name))} vs "
                        f"{proto.__name__}.{name}{inspect.signature(getattr(proto, name))}"
                    )
    assert implementations > 0
    assert not broken, "\n".join(broken)


def test_the_operation_rule_catches_the_old_triggerable_clash():
    """The rule is real: a class that gives ``Triggerable.abort`` an ``@op`` of the same name trips it."""
    from simubench.engine.component import Component, OpResult, op
    from simubench.library.components.trigger import Triggerable

    class Clash(Triggerable, Component):
        @op("abort")
        def abort(self, **_: Any) -> OpResult:
            return OpResult()

    assert callable(vars(Triggerable).get("abort"))
    assert hasattr(vars(Clash)["abort"], "__op_name__")
    assert not hasattr(vars(Triggerable)["abort"], "__op_name__")


# ---- sample streams (D67, #946) ----------------------------------------------------------------


def _samples_gaps(cls: type) -> list[str]:
    """The sample-stream hooks a class with a samples output port leaves at the base's refusal."""
    from simubench.engine.component import Component

    made = [s for p in ("in1", "out1") if (s := cls.spec_of(p))] if cls.port_pattern else []
    specs = [*cls.ports.values(), *made]
    if not any(s.medium == "samples" and s.direction != "in" for s in specs):
        return []
    return [h for h in ("frames", "sample_interval") if getattr(cls, h) is getattr(Component, h)]


def test_a_type_with_a_samples_output_implements_the_stream_hooks():
    """A samples output with no ``frames`` would fail at the first read; find it here instead."""
    gaps = {tname: g for tname, cls in component_types().items() if (g := _samples_gaps(cls))}
    assert not gaps, gaps


def test_the_samples_rule_catches_a_missing_hook():
    from simubench.engine.component import Component, PortSpec

    class Half(Component):
        ports = {"out": PortSpec("out", "samples")}  # noqa: RUF012

        def frames(self, port, request):
            raise AssertionError

    assert _samples_gaps(Half) == ["sample_interval"]


def test_each_type_names_the_classes_whose_built_in_models_use_it():
    """P2-4: the build area lists a class's own types first; the classes come from the library."""
    from simubench.server.catalog import components

    classes = {c.type: c.classes for c in components()}
    assert "oscilloscope" in classes["scope_vertical"] and "multimeter" not in classes["scope_vertical"]
    assert "multimeter" in classes["dmm_adc"] and "oscilloscope" not in classes["dmm_adc"]
    assert {"oscilloscope", "spectrum_analyzer"} <= set(classes["input_port"])


def test_the_catalog_gives_a_parameter_its_physical_range():
    """#1455: the range the validator and the fault inject use is the one the catalog shows."""
    from simubench.server.catalog import components

    by_type = {c.type: {p.name: p for p in c.params} for c in components()}
    r = by_type["resistor"]["r"]
    assert (r.minimum, r.maximum, r.min_open, r.max_open) == (0.0, None, True, False)
    c = by_type["rc_lowpass"]["c"]
    assert (c.minimum, c.min_open) == (0.0, False)
    assert by_type["resistor"]["r"].unit == "OHM"
    assert all(p.minimum is None and p.maximum is None for p in by_type["tunable_laser"].values())
