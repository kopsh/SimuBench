"""Read-only projections of a served session: the DTOs the HTTP API, the MCP tools and the TUI show
(session, instruments, internals, scope screen, readings, nets, faults). Nothing here changes a
session; actions are in :mod:`simubench.server.drive`, lifecycle in :mod:`simubench.server.manager`."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

import numpy as np

from ..access.scpi_server import scpi_socket_address
from ..contracts import api as M
from ..engine.component import ScreenMeasurer, ScreenSource
from ..engine.deviation import deviation_keys as _deviation_keys
from ..engine.faults import FaultState
from ..engine.instrument import Instrument
from ..engine.portref import split
from ..engine.record import jsonable
from ..engine.session import Session
from ..engine.silence import why_silent
from ..engine.stream import ModelLimit
from ..engine.validate import drives_net
from ..library.loader import load_bench
from ..models import categories
from ..models.definition import InstrumentDefinition
from ..models.panels import effective_panel
from ..scpi.errors import ScpiError
from .benchfiles import scan
from .errors import Unknown
from .internals_links import drivers, influence_edges, selections
from .internals_links import readings as readings_of

#: Bins of the envelope a deep record's screen trace gets when the caller asks for every sample (#1012).
DEEP_SCREEN_BINS = 1000

if TYPE_CHECKING:
    from ..library.components.scope_record import Record
    from .manager import ServedSession, SessionManager

log = logging.getLogger(__name__)


class SessionViews:
    """The projections that need the manager (the workspace, the stale-definition check)."""

    def __init__(self, manager: SessionManager) -> None:
        self.manager = manager

    def bench_files(self) -> list[M.BenchFile]:
        """The workspace's bench files as the benches list shows them: name, description, instrument
        count; a file that does not load is listed with its first error instead of hidden."""
        out: list[M.BenchFile] = []
        for p in scan(self.manager.files.workspace):
            rel = p.relative_to(self.manager.files.workspace).as_posix()
            try:
                b = load_bench(p)
                out.append(
                    M.BenchFile(
                        path=rel, name=b.name, description=b.description, instruments=len(b.instruments)
                    )
                )
            except Exception as exc:  # show broken files instead of hiding them
                out.append(M.BenchFile(path=rel, name=p.name, error=str(exc).splitlines()[0]))
        return out

    def describe(self, served: ServedSession, values_only: bool = False) -> M.SessionInfo:
        """The session as the API shows it; ``values_only`` gives each instrument's settings as values
        by path instead of full definitions (#1299: a 1 MB session down to its moving parts)."""
        s = served.session
        b = s.bench
        ports = {lst.instrument: lst.port for lst in (served.scpi.listeners if served.scpi else [])}
        hislip = served.hislip.addresses() if served.hislip else {}
        vxi11 = served.vxi11.addresses() if served.vxi11 else {}
        instruments = [
            self.describe_instrument(
                s, inst, i, ports.get(name), hislip.get(name), vxi11.get(name), values_only
            )
            for i, (name, inst) in enumerate(s.instruments.items())
        ]
        clients: list[M.ClientInfo] = []
        for server in served.servers.values():
            clients += [
                M.ClientInfo(client=c.client, instrument=c.instrument, locked=c.locked)
                for c in server.connections()
            ]
        for info in instruments:
            info.remote = served.remote_clients(info.name)
            info.local = info.name in served.local
        parts = [self._component_info(s, name) for name in b.parts]
        wires = []
        for i, w in enumerate(b.wires):
            accs = [self._component_info(s, f"wire{i}.{a.type}{k}") for k, a in enumerate(w.accessories)]
            wires.append(
                M.WireInfo(
                    index=i,
                    **{"from": w.from_},
                    to=w.to,
                    label=w.label,
                    accessories=accs,
                    net=s.port_net.get(w.from_),
                )
            )
        return M.SessionInfo(
            id=served.id,
            build=served.build,
            bench_name=b.name,
            bench_path=served.bench_path.relative_to(self.manager.files.workspace).as_posix()
            if served.bench_path
            else None,
            description=b.description,
            clock_mode=s.clock.mode,  # type: ignore[arg-type]
            time_scale=s.clock.time_scale,
            idle_mode=s.clock.idle_mode,  # ty: ignore[invalid-argument-type] — checked in Session.__init__
            now_ns=s.now_ns,
            seed=s.seed,
            reveal_true_values=s.reveal_true_values,
            bench_sha256=served.bench_sha256,
            bench_fingerprint=served.bench_fingerprint,
            stale_definitions=self.manager.stale_definitions(served) if not served.preview else [],
            record_path=str(s.recorder.path) if s.recorder and s.recorder.path.exists() else None,
            port_notices=list(served.port_notices),
            link_address=served.link.address() if served.link else None,
            clients=clients,
            instruments=instruments,
            parts=parts,
            duts=b.duts(),
            wires=wires,
            nets=[M.NetInfo(id=n.id, members=n.members, notes=_net_notes(s, n.id)) for n in s.nets.values()],
            layout=b.layout,
        )

    def _component_info(self, s: Session, name: str) -> M.ComponentInfo:
        comp = s.components[name]
        part = s.bench.parts.get(name)
        label = part.label if part else None
        ports = [
            M.PortInfo(
                name=p,
                direction=spec.direction,
                medium=spec.medium,
                net=s.port_net.get(f"{name}.{p}"),
                drives=drives_net(type(comp), spec),
            )
            for p, spec in comp.ports.items()
        ]
        params = comp.describe()
        deviations = []
        for param, rng in (part.deviation if part else {}).items():
            key = f"{name}.{param}"
            drawn = key in s.part_deviations
            if drawn and not s.reveal_true_values:
                params[param] = next(v for k, v, _ in comp.nominal() if k == param)  # the nominal value
            deviations.append(_deviation_info(param, rng, comp.param(param), drawn, s.reveal_true_values))
        return M.ComponentInfo(
            name=name,
            type=comp.type_name,
            label=label,
            dut=bool(part and part.dut),
            params=jsonable(params),
            ports=ports,
            deviations=deviations,
        )

    def describe_instrument(
        self,
        s: Session,
        inst: Instrument,
        index: int,
        scpi_port: int | None,
        hislip: str | None = None,
        vxi11: str | None = None,
        values_only: bool = False,
    ) -> M.InstrumentInfo:
        d = inst.definition
        ports = []
        for pname, (cname, cport) in inst.ports.items():
            comp = inst.components[cname]
            spec = comp.port_spec(cport)  # a relay terminal comes from the card's port_pattern
            ports.append(
                M.PortInfo(
                    name=pname,
                    direction=spec.direction,
                    medium=d.ports.media.get(pname, spec.medium),
                    net=s.port_net.get(f"{inst.name}.{pname}"),
                    channel=comp.channel,
                    drives=drives_net(type(comp), spec),
                )
            )
        dangerous_paths = {c.state for c in d.commands if c.dangerous and c.state}
        on_read = _memory_depth_paths(inst)
        state = []
        for path, spec in [] if values_only else inst.specs.items():
            generic = f"ch.{path.split('.', 1)[1]}" if path.startswith("ch") and path[2:3].isdigit() else path
            ch = int(path[2 : path.index(".")]) if path.startswith("ch") and path[2:3].isdigit() else None
            state.append(
                M.StateVarInfo(
                    **jsonable(spec.model_dump()),
                    path=path,
                    dangerous=generic in dangerous_paths,
                    value=jsonable(inst.state.get(path)),
                    channel=ch,
                    fidelity="computed_on_read" if path in on_read else None,
                )
            )
        return M.InstrumentInfo(
            name=inst.name,
            label=inst.instance.label,
            model=d.model,
            manufacturer=d.manufacturer,
            definition_id=d.id,
            instrument_class=categories.as_api_class(d.instrument_class),
            description=d.description,
            idn=inst.idn,
            channels=inst.channel_count,
            # The socket address is a fact of the listener, not a guess: none when this session serves no SCPI.
            address_virtual=scpi_socket_address("127.0.0.1", scpi_port) if scpi_port else None,
            address_real=inst.instance.address,
            address_hislip=hislip,
            address_vxi11=vxi11,
            scpi_port=scpi_port,
            display=_display_info(d),
            ports=ports,
            state=state,
            values={path: jsonable(inst.state.get(path)) for path in inst.specs} if values_only else None,
            spec_rev=inst.settings.spec_rev,
            errors=[M.ErrorItem(code=e.code, message=e.message) for e in inst.errors.peek_all()],
            warnings=[M.WarningItem(code=c, message=m) for c, m in inst.warnings.items()],
            deviation=inst.instance.deviation,
            dut=inst.instance.dut,
            deviations=[
                _deviation_info(
                    key,
                    spec,
                    inst.deviations.get(key),
                    key in inst.deviations,
                    s.reveal_true_values,
                )
                for spec in d.deviations
                if spec.option is None or spec.option in inst.options  # an option's, on the units with it
                for key in _deviation_keys(inst, spec.component, spec.parameter, spec.when)
            ],
            options=option_infos(d, inst.options),
        )


def option_infos(d: InstrumentDefinition, installed: Iterable[str]) -> list[M.OptionInfo]:
    """The model's options (D68): what each changes, and whether this unit has it."""
    have = set(installed)
    return [
        M.OptionInfo(
            id=o.id,
            label=o.label,
            params=[f"{comp}.{param}" for comp, params in o.params.items() for param in params],
            commands=[c.header for c in d.commands if c.option == o.id],
            installed=o.id in have,
        )
        for o in d.options
    ]


def served_addresses(served: ServedSession) -> dict[str, list[str]]:
    """Every address the service's own servers answer at, by instrument: what a script that
    connects through the bench link must be able to open (the ports are the service's choice)."""
    out: dict[str, list[str]] = {name: [] for name in served.session.instruments}
    for server in served.servers.values():  # the registry, in start order (#1475)
        for name, address in server.addresses().items():
            out.setdefault(name, []).append(address)
    return out


def internals(served: ServedSession, instrument: str) -> M.InstrumentInternals:
    """The instrument's components, internal wiring and panel ports (issue 20), read-only."""
    from .services.internal_probe import tuning_of

    s = served.session
    inst = s.instrument(instrument)
    wires = inst.wiring.wires()
    # Ports a component takes per channel (an acquisition's in1..in4) exist only in the wiring.
    wired: dict[str, dict[str, str]] = {}
    for a, b in wires:
        for end, direction in ((a, "out"), (b, "in")):
            cname, port = split(end)
            wired.setdefault(cname, {})[port] = direction
    # Ports a component exposes without declaring them (a multiplexer's channel terminals,
    # created by the class's port_pattern).
    exposed: dict[str, set[str]] = {}
    for _pname, (cname, cport) in inst.ports.items():
        exposed.setdefault(cname, set()).add(cport)
    commands = inst.definition.commands
    specs = {c.name: c for c in inst.definition.components} | {
        f"ch{n}.{c.name}": c
        for n in range(1, inst.channel_count + 1)
        for c in inst.definition.channel_components
    }
    edges = influence_edges(inst, wires)
    with s.view():
        components = [
            M.InternalComponent(
                name=name,
                type=comp.type_name,
                channel=comp.channel,
                type_label=comp.label or None,
                type_label_en=comp.label_en or None,
                about=comp.about or None,
                about_en=comp.about_en or None,
                doc=_first_paragraph(type(comp).__doc__),
                label=getattr(specs.get(name), "label", None),
                description=getattr(specs.get(name), "description", None),
                params=[
                    M.InternalParam(name=k, value=jsonable(v), binding=b, unit=comp.units.get(k))
                    for k, v, b in comp.nominal()
                ],
                ports=[
                    M.PortInfo(name=p, direction=spec.direction, medium=spec.medium, channel=comp.channel)
                    for p, spec in comp.ports.items()
                ]
                + [
                    M.PortInfo(
                        name=p,
                        direction=d,
                        medium=spec.medium if (spec := comp.spec_of(p)) else "signal",
                        channel=comp.channel,
                    )
                    for p, d in wired.get(name, {}).items()
                    if p not in comp.ports
                ]
                + [
                    M.PortInfo(name=p, direction="inout", medium="electrical", channel=comp.channel)
                    for p in sorted(exposed.get(name, ()))
                    if p not in comp.ports and p not in wired.get(name, {})
                ],
                settings=(bound := list(dict.fromkeys(comp.bound_paths().values()))),
                commands=drivers(commands, name, comp.channel, bound),
                readings=readings_of(commands, inst, edges, name),
            )
            for name, comp in inst.components.items()
        ]
        tuning = tuning_of(inst)
    ports = []
    for pname, (cname, cport) in inst.ports.items():
        comp = inst.components[cname]
        spec = comp.ports.get(cport)  # a pattern port (a mux channel) is not declared
        ports.append(
            M.InternalPort(
                name=pname,
                target=f"{cname}.{cport}",
                direction=spec.direction if spec else "inout",
                medium=inst.definition.ports.media.get(pname, spec.medium if spec else "electrical"),
                channel=comp.channel,
            )
        )
    return M.InstrumentInternals(
        instrument=inst.name,
        definition_id=inst.definition.id,
        reference=inst.definition.reference,
        channels=inst.channel_count,
        components=components,
        wires=_internal_wires(s, inst, wires),
        refs=[
            M.InternalRef(**{"from": used}, to=name, param=key)
            for name, comp in inst.components.items()
            for key, used in comp.ref_links()
        ],
        selects=selections(inst),
        ports=ports,
        tuning=tuning,
    )


def _internal_wires(s: Session, inst: Instrument, wires: list[tuple[str, str]]) -> list[M.InternalWire]:
    """The instrument's wires with what flows on each: a sample stream's wire carries its rate and
    format with the current settings (none while the block has no rate to give)."""
    from .services.internal_probe import out_ports, stream_format, stream_request

    streams = {
        ref: (comp, port) for ref, (comp, port, medium) in out_ports(inst).items() if medium == "samples"
    }
    out: list[M.InternalWire] = []
    with s.view():
        for a, b in wires:
            if a not in streams:
                out.append(M.InternalWire(**{"from": a}, to=b))
                continue
            comp, port = streams[a]
            rate, fmt = None, None
            try:  # one sample is enough to learn the format
                frame = s.cached_frames(comp, port, stream_request(s, comp, port, count=1))
                rate, fmt = frame.rate_hz, stream_format(frame)
            except (ScpiError, NotImplementedError, ValueError):
                pass
            out.append(M.InternalWire(**{"from": a}, to=b, medium="samples", sample_rate_hz=rate, format=fmt))
    return out


# ---- actions ------------------------------------------------------------------------------------


def _memory_depth_paths(inst: Instrument) -> set[str]:
    """The settings a scope's record length follows (bound to a ``scope_timebase``'s ``points``):
    the memory depth, whose deep records are computed where they are read (#1012)."""
    paths = set()
    for comp in inst.components.values():
        if comp.type_name == "scope_timebase":
            bound = comp.bound_path("points")
            if bound is not None:
                paths.add(bound)
    return paths


def _screen_traces(
    s: Session, inst: Instrument, records: Mapping[int, Record], bins: int | None, strict: bool
) -> list[M.Trace]:
    """The screen's traces of ``records``: every sample, or per-bin min and max (``bins``; a deep
    record always, #1012: its envelope stands for it). A trace whose screen is beyond the model is
    refused (``strict``) or left out."""
    out = []
    for n, r in sorted(records.items()):
        minmax: list[float] | None = None
        want = bins if bins is not None or r.deep is None else DEEP_SCREEN_BINS
        try:
            if want is not None and r.points:
                minmax = r.envelope(want, ("screen", n)) or None
            volts = np.asarray(r.volts, dtype=float) if minmax is None else np.zeros(0)
        except ModelLimit:
            if strict:
                raise
            continue
        out.append(
            M.Trace(
                channel=n,
                t0_s=r.origin,
                dt_s=r.dt_s,
                points=int(r.points),
                volts=[] if minmax is not None else [float(x) for x in volts],
                scale=r.scale,
                offset=r.offset,
                triggered=r.triggered,
                silence=_channel_silence(s, inst, n),
                minmax=minmax,
            )
        )
    return out


def _screen_functions(
    inst: Instrument, records: Mapping[int, Record], bins: int | None
) -> list[M.FunctionTrace]:
    """The math functions the screen draws, over the channel records it shows (#1219's demo: an
    FFT turned on with ``:FUNCtion1:DISPlay ON`` appears under the time traces)."""
    # the scope_math block (its class is registered by type name, so it is looked up by that)
    math_block: Any = next((c for c in inst.components.values() if c.type_name == "scope_math"), None)
    if math_block is None or not records:
        return []
    out = []
    for f, operator, rec in math_block.screen_functions(records):
        minmax = rec.envelope(bins) if bins is not None and rec.points else None
        out.append(
            M.FunctionTrace(
                function=f,
                operator=operator,
                spectrum=rec.x_hz,
                x0=rec.origin,
                dx=rec.dt_s,
                points=int(rec.points),
                unit=math_block.function_units(f)[1],
                values=[] if minmax is not None else [float(v) for v in rec.volts],
                scale=rec.scale,
                offset=rec.offset,
                minmax=minmax or None,
            )
        )
    return out


def scope_screen(served: ServedSession, instrument: str, points: int | None = None) -> M.ScopeScreen:
    """The scope's screen. ``points`` (tui-screens.md 6.1): downsample each trace to that many
    bins of per-bin min and max (2 values per bin) instead of returning every sample — the
    pixel extraction of architecture.md 8.3 for a renderer with a fixed dot width.

    The screen's state is the acquisition block's read (:class:`ScreenSource`); this assembles the
    DTO and downsamples it, and holds the frame counter (a projection state, ServedSession.frames)."""
    s = served.session
    inst = s.instrument(instrument)
    acq = inst.components.get("acq")
    if acq is None or not isinstance(acq, ScreenSource):
        raise Unknown(f"{instrument} is not an oscilloscope")
    bins = max(2, int(points)) if points else None
    with s.view(settle=(instrument,)):  # nothing else the instrument keeps changes here
        # The screen's redraw counter advances with every read; the block draws frame `frame` —
        # with noise and untriggered phase of its own (D47) — only when the screen follows the signal.
        frame = served.frames[instrument] = served.frames.get(instrument, 0) + 1
        state = acq.screen_state(frame)
        try:
            traces = _screen_traces(s, inst, state.records, bins, strict=state.shown)
        except ModelLimit:
            # The frame's own projection is beyond the model (#986): the screen holds what it has,
            # as a read of such an acquisition is refused.
            state = state.holding()
            traces = _screen_traces(s, inst, state.records, bins, strict=False)
        meas = next((c for c in inst.components.values() if isinstance(c, ScreenMeasurer)), None)
        measurements = (
            [M.ScopeMeasurement(**row) for row in meas.screen_values(state.records)] if meas else []
        )
        return M.ScopeScreen(
            instrument=instrument,
            source="live" if state.live else "record",
            running=state.live,
            waiting=state.waiting,
            timebase_scale=float(inst.get_state("timebase.scale")),
            screen_left_s=state.screen_left_s,
            trigger_level=state.trigger_level,
            trigger_source=state.trigger_source,
            traces=traces,
            functions=_screen_functions(inst, state.records, bins),
            acquisitions=state.acquisitions,
            measurements=measurements,
        )


def readings(served: ServedSession, instrument: str) -> M.InstrumentReadings:
    s = served.session
    inst = s.instrument(instrument)
    values: dict[str, Any] = {}
    traces: list[M.ReadingTrace] = []
    # The panel shows what has come due, as any command's arrival would.
    with s.view(settle=(instrument,)):
        for name, comp in inst.components.items():
            r = comp.readings()
            if not r:
                continue
            for k, v in r.get("values", {}).items():
                values[f"{name}.{k}"] = jsonable(v)
            for t in r.get("traces", []):
                traces.append(M.ReadingTrace(**{**t, "name": f"{name}.{t['name']}"}))
        return M.InstrumentReadings(instrument=instrument, now_ns=s.now_ns, values=values, traces=traces)


def probe(served: ServedSession, net: str, max_lines: int = 16) -> M.NetProbe:
    """Read a net without touching it, and break its signal down by independent source."""
    s = served.session
    if net not in s.nets:
        raise Unknown(net)
    with s.view():
        sol, emf = s.solution()
        sig = s.net_signal(net).merged()
        lines = sig.spectral_lines(max_lines=max_lines)
        window = sig.window_s()  # two periods of the strongest line, or 2 ms for DC
        points = 400
        volts = sig.sample_rel(s.now_ns, np.arange(points) * (window / points))
        wave = M.NetWaveform(
            net=net,
            t0_ns=s.now_ns,
            dt_s=window / points,
            volts=[float(v) for v in volts],
            exact=sig.is_exact,
            notes=[n.detail for n in sig.notes],
        )
        sources = sol.source_contributions(net, emf, max_lines=max_lines)
        return M.NetProbe(
            net=net,
            members=list(s.nets[net].members),
            t_ns=s.now_ns,
            dc=sig.mean(),
            rms=sig.rms(),
            peak=sig.peak(),
            lines=lines,
            line_count=int((sig.freq > 0).sum()),
            waveform=wave,
            sources=sources,
            notes=[n.detail for n in sig.notes],
            silence=list(why_silent(s, net)),
        )


def net_levels(served: ServedSession, points: int = 48) -> M.NetLevels:
    """Every net's level and a thumbnail, in one read, for the canvas (D44)."""
    s = served.session
    with s.view():
        out: list[M.NetLevel] = []
        for net in s.nets:
            sig = s.net_signal(net).merged()
            window = sig.window_s()  # two periods of the strongest line, or 2 ms for DC
            volts = sig.sample_rel(s.now_ns, np.arange(points) * (window / points))
            peak = float(sig.peak())
            dc = float(sig.mean())
            out.append(
                M.NetLevel(
                    net=net,
                    dc=dc,
                    rms=float(sig.rms()),
                    peak=peak,
                    active=peak > 0 or dc != 0,
                    window_s=window,
                    wave=[float(v) for v in volts],
                )
            )
        return M.NetLevels(t_ns=s.now_ns, nets=out)


def net_waveform(served: ServedSession, net: str, window_s: float, points: int) -> M.NetWaveform:
    s = served.session
    if net not in s.nets:
        raise Unknown(net)
    with s.view():
        sig = s.net_signal(net)
        dt = window_s / points
        v = sig.sample_rel(s.now_ns, np.arange(points) * dt)
        return M.NetWaveform(
            net=net,
            t0_ns=s.now_ns,
            dt_s=dt,
            volts=[float(x) for x in v],
            exact=sig.is_exact,
            notes=[n.detail for n in sig.notes],
        )


__all__ = ["ScpiError", "ServedSession", "SessionManager"]


def _first_paragraph(doc: str | None) -> str | None:
    if not doc:
        return None
    return " ".join(doc.strip().split("\n\n")[0].split())


def _net_notes(s: Session, net: str) -> list[str]:
    """Fidelity notes of the signal on a net (empty when exact); a model failure is a note too."""
    try:
        with s.view():
            return list(dict.fromkeys(n.detail for n in s.net_signal(net).notes))
    except Exception as exc:  # describing a session must not fail because one net cannot be solved
        log.exception("net %s of %s", net, s.bench.name)
        return [f"model error: {type(exc).__name__}"]


def fault_info(f: FaultState) -> M.FaultInfo:
    """The one fault shape every consumer assembles: the web API and MCP tools return the same
    fields (the MCP tools used to carry their own, with seconds instead of ns and no remaining)."""
    return M.FaultInfo(
        id=f.id,
        status=f.status,
        description=f.describe(),
        source=f.source,
        started_ns=f.started_ns,
        ends_ns=f.ends_ns,
        remaining=f.remaining,
        spec=f.spec,
    )


def _channel_silence(s: Session, inst: Instrument, channel: int) -> list[M.SilenceReason]:
    """Why a scope channel's input reads 0 V: its input port, traced upstream (issue 22)."""
    for pname, (comp, port) in inst.ports.items():
        if comp.startswith(f"ch{channel}.") and inst.components[comp].ports[port].direction == "in":
            ref = f"{inst.name}.{pname}"
            return list(why_silent(s, s.node_of(inst.components[comp], port), port_ref=ref))
    return []


def _deviation_info(param: str, rng: Any, value: Any, drawn: bool, reveal: bool) -> M.DeviationInfo:
    """A deviation range for the inspector; a drawn value only when the bench reveals true values."""
    return M.DeviationInfo(
        param=param,
        limit=jsonable(rng.limit),
        unit=rng.unit,
        distribution=rng.distribution,
        by=jsonable(rng.by),
        bands=[list(b) for b in rng.bands] if rng.bands else None,
        value=jsonable(value) if drawn and reveal else None,
        drawn=drawn,
    )


def _display_info(d: InstrumentDefinition) -> M.DisplayInfo:
    panel, source = effective_panel(d)
    return M.DisplayInfo(**d.display.model_dump(exclude={"panel"}), panel=panel, panel_source=source)
