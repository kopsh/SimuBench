"""Macros: a component written the earlier, one-component way expands into standard modules when a
definition loads (instrument-authoring.md 8.1). The expanded form is the only implementation; a
definition written the earlier way and one with the modules written out are the same definition.

The registry maps a component type to the function that rewrites the raw definition data in place
(components, channel components, wiring) for its spec; it does nothing when the modules are already
written out. The rewriting knowledge lives here, with the loader, not in the components themselves.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

Data = dict[str, Any]

#: Component type -> its expansion (data, spec) -> None, in place.
MACROS: dict[str, Callable[[Data, Data], None]] = {}


def macro(type_name: str) -> Callable[[Callable[[Data, Data], None]], Callable[[Data, Data], None]]:
    """Register the expansion of a component type written the earlier way."""

    def deco(fn: Callable[[Data, Data], None]) -> Callable[[Data, Data], None]:
        MACROS[type_name] = fn
        return fn

    return deco


def expand_macros(data: Any) -> Any:
    """The definition data with every macro expanded (a copy; ``data`` is left as it is).

    Data that is not shaped like a definition is returned unchanged for the schema to report.
    Raises ValueError when a macro cannot expand."""
    if not isinstance(data, dict) or not isinstance(data.get("components"), list):
        return data
    out = copy.deepcopy(data)
    for spec in list(out["components"]):
        if not isinstance(spec, dict) or not isinstance(spec.get("type"), str):
            continue
        expand = MACROS.get(spec["type"])
        if expand is not None:
            expand(out, spec)
    return out


@macro("scope_acquisition")
def scope_acquisition(data: Data, spec: Data) -> None:
    """Expand an acquisition written the earlier way (no module named) into the modules.

    The parameters each module takes move to it under the module's names (``MACRO_MODULES``);
    the per-channel component wired to ``<acq>.in{n}`` becomes the ``vertical`` one, and a
    ``scope_probe`` named ``probe`` is put between it and the acquisition. Raises ValueError when
    the definition is not shaped so (write the modules out instead)."""
    _expand_measurements(data, spec)
    _expand_preamble(data, spec)
    params = dict(spec.get("params") or {})
    if any(key in params for key in MACRO_MODULES):
        _expand_stream(data, spec)
        return  # the modules are written out
    acq = str(spec["name"])
    comps = data.setdefault("components", [])
    chans = data.setdefault("channel_components", [])
    wiring = data.setdefault("wiring", {})
    inst_wires = wiring.setdefault("instrument", [])
    chan_wires = wiring.setdefault("channel", [])
    feed: tuple[int, str] | None = None
    for i, (src, dst) in enumerate(inst_wires):
        if dst == f"{acq}.in{{n}}" and src.startswith("ch{n}.") and src.endswith(".out"):
            feed = (i, src[len("ch{n}.") : -len(".out")])
    taken = {str(c.get("name")) for c in comps} | {f"ch.{c.get('name')}" for c in chans}
    clash = [n for n in (*MACRO_NAMES.values(), "ch.probe") if n in taken]
    if feed is None or clash:
        why = f"names {', '.join(clash)} are taken" if clash else f"nothing is wired to {acq}.in{{n}}"
        raise ValueError(f"{acq}: cannot expand the one-component acquisition ({why}); write the modules out")
    modules = []
    for key, (type_name, moves) in MACRO_MODULES.items():
        moved = {new: params.pop(old) for old, new in moves.items() if old in params}
        modules.append({"name": MACRO_NAMES[key], "type": type_name, "params": moved})
    index, vertical = feed
    if not any(c.get("name") == vertical for c in chans):
        raise ValueError(f"{acq}: {vertical!r} wired to {acq}.in{{n}} is not a channel component")
    spec["params"] = {
        **{k: MACRO_NAMES[k] for k in MACRO_MODULES},
        "vertical": vertical,
        "probe": "probe",
        **params,
    }
    at = next(i for i, c in enumerate(comps) if c is spec) + 1
    comps[at:at] = modules
    after = next(i for i, c in enumerate(chans) if c.get("name") == vertical) + 1
    if chans[after - 1].get("type") == "scope_frontend":
        chans[after - 1]["type"] = "scope_vertical"  # the earlier name of the same component
    chans.insert(after, {"name": "probe", "type": "scope_probe", "params": {}})
    chan_wires.append([f"{vertical}.out", "probe.in"])
    inst_wires[index] = ["ch{n}.probe.out", f"{acq}.in{{n}}"]
    _expand_stream(data, spec)


#: The acquisition-mode parameters an acquisition written without a decimator gives it.
STREAM_PARAMS = ("processing", "averages", "eres_bits", "peak_max_oversample")
#: The name the expansion gives the decimator.
DECIMATOR_NAME = "dec"


def _expand_stream(data: Data, spec: Data) -> None:
    """Put the sample stream between the channels and an acquisition written without one (D67,
    signal-chain.md 4.2): the wire ``<signal> -> <acq>.in{n}`` becomes ``<signal> -> <adc>.in{n}``,
    ``<adc>.out{n} -> dec.in{n}``, ``dec.out{n} -> <acq>.in{n}`` with a ``scope_decimator`` named
    ``dec`` that takes the acquisition-mode parameters; the ADC is told which per-channel modules
    it converts for. Does nothing when the acquisition names its decimator."""
    params = spec.setdefault("params", {})
    if not isinstance(params, dict) or "decimator" in params or not isinstance(params.get("adc"), str):
        return
    acq = str(spec["name"])
    comps = data.setdefault("components", [])
    chans = data.setdefault("channel_components", [])
    inst_wires = data.setdefault("wiring", {}).setdefault("instrument", [])
    adcs = [c for c in comps if isinstance(c, dict) and c.get("type") == "scope_adc"]
    # The ADC the acquisition names; when the name is wrong (the checks report that, once), the
    # scope's only ADC, so the wrong name is not followed by complaints about the wiring.
    adc_spec = next((c for c in adcs if c.get("name") == params["adc"]), adcs[0] if len(adcs) == 1 else None)
    if adc_spec is None:
        return
    adc = str(adc_spec.get("name"))
    if any(isinstance(c, dict) and c.get("name") == DECIMATOR_NAME for c in comps):
        raise ValueError(
            f"{acq}: cannot add the decimator (the name {DECIMATOR_NAME!r} is taken); "
            "write the scope_decimator out and name it in the acquisition's decimator parameter"
        )

    def named(key: str, among: list[Any], type_name: str) -> str:
        """The acquisition's reference ``key`` when it names a component of the type, else nothing:
        a wrong name is reported once, on the acquisition, not again on the modules it is copied to."""
        value = params.get(key)
        fits = (isinstance(c, dict) and c.get("name") == value and c.get("type") == type_name for c in among)
        return value if any(fits) else ""

    moved = {key: params.pop(key) for key in STREAM_PARAMS if key in params}
    dec = {
        "name": DECIMATOR_NAME,
        "type": "scope_decimator",
        "params": {"adc": adc, "horizontal": named("horizontal", comps, "scope_timebase"), **moved},
    }
    adc_params = adc_spec.setdefault("params", {})
    for key in ("vertical", "probe"):
        if named(key, chans, f"scope_{key}"):
            adc_params.setdefault(key, params[key])
    params["decimator"] = DECIMATOR_NAME
    # Added at the end, so the positions of what the author wrote (which the checks point at) stay.
    comps.append(dec)
    feed = next((i for i, (_, dst) in enumerate(inst_wires) if dst == f"{acq}.in{{n}}"), None)
    if feed is not None:
        inst_wires[feed] = [inst_wires[feed][0], f"{adc}.in{{n}}"]
        inst_wires.append([f"{adc}.out{{n}}", f"{DECIMATOR_NAME}.in{{n}}"])
        inst_wires.append([f"{DECIMATOR_NAME}.out{{n}}", f"{acq}.in{{n}}"])


#: Parameters an acquisition written the earlier way (one component) gives each module, and the
#: module's name for them (instrument-authoring.md 8.4: the big component is a macro).
MACRO_MODULES: dict[str, tuple[str, dict[str, str]]] = {
    "horizontal": (
        "scope_timebase",
        {"timebase": "scale", "delay": "delay", "record_length": "record_length", "points": "points"},
    ),
    "trigger": (
        "scope_trigger",
        {
            "trigger_source": "source",
            "trigger_level": "level",
            "trigger_slope": "slope",
            "rising": "rising",
            "falling": "falling",
            "search_windows": "search_windows",
        },
    ),
    "adc": (
        "scope_adc",
        {
            "adc_bits": "bits",
            "adc_codes_per_div": "codes_per_div",
            "adc_divs": "divs",
            "adc_offset": "offset_sign",
            "max_rate": "max_rate",
            "max_rate_paired": "max_rate_paired",
            "rate_pairs": "rate_pairs",
        },
    ),
}
#: The names the expansion gives the modules.
MACRO_NAMES = {"horizontal": "timebase", "trigger": "trigger", "adc": "adc"}


#: The Keysight ten-field preamble the earlier ``<acq>.preamble`` op produced (format, type, points,
#: count, x increment, x origin, x reference, y increment, y origin, y reference), as the
#: ``header`` declaration of a ``waveform_transfer`` (op ``header``); floats as NR3, six digits.
PREAMBLE_HEADER: Data = {
    "encoding": "text",
    "separator": ",",
    "fields": [
        {"setting": "waveform.format", "map": {"BYTE": 0, "WORD": 1, "ASCii": 4}},
        {"value": 0},
        {"figure": "points"},
        {"value": 1},
        {"figure": "x_increment", "format": "%+.6E"},
        {"figure": "x_start", "format": "%+.6E"},
        {"value": 0},
        {"figure": "y_increment", "format": "%+.6E"},
        {"figure": "y_origin", "format": "%+.6E"},
        {"figure": "y_reference"},
    ],
}


def _expand_preamble(data: Data, spec: Data) -> None:
    """A command with op ``<acq>.preamble`` (the acquisition's own preamble, removed by #633) becomes
    ``<xfer>.header`` on the definition's ``waveform_transfer``, which gets the Keysight ten-field
    ``header`` declaration unless it already has one. Raises ValueError when there is no single
    transfer to carry it."""
    old = f"{spec['name']}.preamble"
    commands = [c for c in data.get("commands") or [] if isinstance(c, dict) and c.get("op") == old]
    if not commands:
        return
    transfers = [
        c for c in data["components"] if isinstance(c, dict) and c.get("type") == "waveform_transfer"
    ]
    if len(transfers) != 1:
        raise ValueError(
            f"{old}: the acquisition's preamble op is now the transfer's header (xfer.header); "
            f"it needs exactly one waveform_transfer component to declare it on, found {len(transfers)}"
        )
    xfer = transfers[0]
    xfer.setdefault("params", {}).setdefault("header", copy.deepcopy(PREAMBLE_HEADER))
    for command in commands:
        command["op"] = f"{xfer['name']}.header"


def _expand_measurements(data: Data, spec: Data) -> None:
    """Measurements written on the acquisition (``measurements``, ``invalid_value``, ``status``
    and the commands' ``<acq>.measure`` / ``<acq>.measurement``) move to a ``scope_measurements``
    module named ``meas`` (``meas2``... when taken), added after the other components."""
    acq = str(spec["name"])
    params = spec.get("params") or {}
    moved = {k: params[k] for k in ("measurements", "invalid_value", "status") if k in params}
    ops = {f"{acq}.measure": "measure", f"{acq}.measurement": "named"}
    commands = [c for c in data.get("commands") or [] if isinstance(c, dict) and c.get("op") in ops]
    if not moved and not commands:
        return
    taken = {str(c.get("name")) for c in data["components"]}
    name = next(n for n in ("meas", *(f"meas{i}" for i in range(2, 100))) if n not in taken)
    meas: dict[str, Any] = {"record": acq}
    if "measurements" in moved:
        meas["names"] = moved.pop("measurements")
    meas.update(moved)
    if any(c.get("op") == f"{acq}.measure" for c in commands) and "waveform.source" in (
        data.get("state") or {}
    ):
        meas["source"] = "$waveform.source"  # the earlier measure read the waveform source
    spec["params"] = {k: v for k, v in params.items() if k not in ("measurements", "invalid_value", "status")}
    data["components"].append({"name": name, "type": "scope_measurements", "params": meas})
    for c in commands:
        c["op"] = f"{name}.{ops[c['op']]}"


@macro("specan_input")
def specan_input(data: Data, spec: Data) -> None:
    """``mixer_limit`` is ``max_mixer_level`` (the D50 naming audit, #952)."""
    _rename(spec, "mixer_limit", "max_mixer_level")


@macro("specan_trace")
def specan_trace(data: Data, spec: Data) -> None:
    """Expand an analyzer written before its front end passed signals (#952, no ``lo`` named).

    A ``specan_lo`` is added: it takes the reference (``clock``, ``source``) from the mixer and the
    sweep rate (``lo_rate``, ``sweep_overhead``) from the trace, and tunes to ``freq.center``; the
    commands' ``<mixer>.status`` become its ``status``. The front end is wired
    ``<input>.out → <mixer>.in`` and ``<mixer>.out → <if_filter>.in``. ``maxhold_limit`` is
    ``max_hold_sweeps``. The component and the wires go to the end of their tables, so the
    pointers of validation findings keep pointing at what the author wrote. Raises ValueError
    when the definition is not shaped so (write the oscillator out instead)."""
    _rename(spec, "maxhold_limit", "max_hold_sweeps")
    params = spec.setdefault("params", {})
    if not isinstance(params, dict) or "lo" in params:
        return  # the oscillator is written out
    trace = str(spec["name"])
    comps = data["components"]
    by_name = {str(c.get("name")): c for c in comps if isinstance(c, dict)}
    mixer = by_name.get(str(params.get("mixer")))
    blocks = {key: by_name.get(str(params.get(key))) for key in ("input", "if_filter")}
    missing = [key for key, block in (("mixer", mixer), *blocks.items()) if block is None]
    if mixer is None or missing:
        raise ValueError(
            f"{trace}: cannot add the local oscillator ({', '.join(missing)} not named); "
            "write a specan_lo out and name it in lo"
        )
    name = next(n for n in ("lo", *(f"lo{i}" for i in range(2, 100))) if n not in by_name)
    mixer_params = mixer.setdefault("params", {})
    lo: Data = {key: mixer_params.pop(key) for key in ("clock", "source") if key in mixer_params}
    lo.update({key: params.pop(key) for key in ("lo_rate", "sweep_overhead") if key in params})
    lo["if_filter"] = params["if_filter"]
    if "freq.center" in (data.get("state") or {}):
        lo["center"] = "$freq.center"
    mixer_name = params.pop("mixer")
    mixer_params["lo"] = params["lo"] = name
    comps.append({"name": name, "type": "specan_lo", "params": lo})
    wires = data.setdefault("wiring", {}).setdefault("instrument", [])
    for wire in (
        [f"{params['input']}.out", f"{mixer_name}.in"],
        [f"{mixer_name}.out", f"{params['if_filter']}.in"],
    ):
        if wire not in wires:
            wires.append(wire)
    for command in data.get("commands") or []:
        if isinstance(command, dict) and command.get("op") == f"{mixer_name}.status":
            command["op"] = f"{name}.status"


def _rename(spec: Data, old: str, new: str) -> None:
    """A parameter's earlier name becomes its present one (the present one wins when both are written)."""
    params = spec.get("params")
    if isinstance(params, dict) and old in params:
        params.setdefault(new, params.pop(old))
