"""Read-only projections of a served session: the DTOs the HTTP API, the MCP tools and the TUI show
(session, instruments, internals, scope screen, readings, nets, faults). Nothing here changes a
session; actions are in :mod:`simubench.server.drive`, lifecycle in :mod:`simubench.server.manager`."""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import numpy as np

from ..engine.deviation import deviation_keys as _deviation_keys
from ..engine.faults import FaultState
from ..engine.instrument import Instrument
from ..engine.portref import split
from ..engine.record import jsonable
from ..engine.session import Session
from ..engine.silence import why_silent
from ..engine.stream import ModelLimit
from ..library.components.scope import channel_index
from ..library.panels import effective_panel
from ..models import api as M
from ..models.definition import InstrumentDefinition
from ..scpi.errors import ScpiError
from .internals_links import drivers, influence_edges, selections
from .internals_links import readings as readings_of

if TYPE_CHECKING:
    from .manager import ServedSession, SessionManager

log = logging.getLogger(__name__)


class SessionViews:
    """The projections that need the manager (the workspace and the stale-definition check)."""

    def __init__(self, manager: SessionManager) -> None:
        self.manager = manager

    def describe(self, served: ServedSession) -> M.SessionInfo:
        s = served.session
        b = s.bench
        ports = {lst.instrument: lst.port for lst in (served.scpi.listeners if served.scpi else [])}
        hislip = served.hislip.addresses() if served.hislip else {}
        vxi11 = served.vxi11.addresses() if served.vxi11 else {}
        instruments = [
            self.describe_instrument(s, inst, i, ports.get(name), hislip.get(name), vxi11.get(name))
            for i, (name, inst) in enumerate(s.instruments.items())
        ]
        clients: list[M.ClientInfo] = [
            M.ClientInfo(client=c, instrument=lst.instrument)
            for lst in (served.scpi.listeners if served.scpi else [])
            for c in sorted(lst.labels)
        ]
        if served.hislip:
            for hs in served.hislip.sessions():
                clients.append(
                    M.ClientInfo(
                        client=hs.client,
                        instrument=hs.instrument,
                        locked=served.hislip.locks(hs.instrument).held_by(hs),
                    )
                )
        if served.vxi11:
            for link in served.vxi11._links.values():
                clients.append(
                    M.ClientInfo(
                        client=link.client,
                        instrument=link.instrument,
                        locked=served.vxi11.locks(link.instrument).held_by(link),
                    )
                )
        if served.link:
            for label in served.link.clients():
                clients.append(
                    M.ClientInfo(
                        client=label,
                        instrument=served.link._clients[label][0],
                        locked=served.link.holds_lock(label),
                    )
                )
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
            bench_name=b.name,
            bench_path=served.bench_path.relative_to(self.manager.files.workspace).as_posix()
            if served.bench_path
            else None,
            description=b.description,
            clock_mode=s.clock_mode,  # type: ignore[arg-type]
            time_scale=s.time_scale,
            now_ns=s.now_ns,
            seed=s.seed,
            reveal_true_values=s.reveal_true_values,
            bench_sha256=served.bench_sha256,
            bench_fingerprint=served.bench_fingerprint,
            stale_definitions=self.manager.stale_definitions(served) if served.id != "preview" else [],
            record_path=str(s.recorder.path) if s.recorder and s.recorder.path.exists() else None,
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
                name=p, direction=spec.direction, medium=spec.medium, net=s.port_net.get(f"{name}.{p}")
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
                )
            )
        dangerous_paths = {c.state for c in d.commands if c.dangerous and c.state}
        state = []
        for path, spec in inst.specs.items():
            generic = f"ch.{path.split('.', 1)[1]}" if path.startswith("ch") and path[2:3].isdigit() else path
            ch = int(path[2 : path.index(".")]) if path.startswith("ch") and path[2:3].isdigit() else None
            state.append(
                M.StateVarInfo(
                    **jsonable(spec.model_dump()),
                    path=path,
                    dangerous=generic in dangerous_paths,
                    value=jsonable(inst.state.get(path)),
                    channel=ch,
                )
            )
        return M.InstrumentInfo(
            name=inst.name,
            label=inst.instance.label,
            model=d.model,
            manufacturer=d.manufacturer,
            definition_id=d.id,
            instrument_class=d.instrument_class,
            description=d.description,
            idn=inst.idn,
            channels=inst.channel_count,
            address_virtual=f"TCPIP0::127.0.0.1::{scpi_port or 5025 + index}::SOCKET",
            address_real=inst.instance.address,
            address_hislip=hislip,
            address_vxi11=vxi11,
            scpi_port=scpi_port,
            display=_display_info(d),
            ports=ports,
            state=state,
            errors=[M.ErrorItem(code=e.code, message=e.message) for e in inst.errors.peek_all()],
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
    for lst in served.scpi.listeners if served.scpi else []:
        out[lst.instrument].append(lst.address)
    for servers in (served.hislip, served.vxi11):
        for name, address in (servers.addresses() if servers else {}).items():
            out[name].append(address)
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
    edges = influence_edges(inst, wires)
    with s.view():
        components = [
            M.InternalComponent(
                name=name,
                type=comp.type_name,
                channel=comp.channel,
                doc=_first_paragraph(type(comp).__doc__),
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


def scope_screen(served: ServedSession, instrument: str, points: int | None = None) -> M.ScopeScreen:
    """The scope's screen. ``points`` (tui-screens.md 6.1): downsample each trace to that many
    bins of per-bin min and max (2 values per bin) instead of returning every sample — the
    pixel extraction of architecture.md 8.3 for a renderer with a fixed dot width."""
    s = served.session
    inst = s.instrument(instrument)
    acq = inst.components.get("acq")
    if acq is None or not hasattr(acq, "compute"):
        raise KeyError(f"{instrument} is not an oscilloscope")
    bins = max(2, int(points)) if points else None
    with s.view(settle=(instrument,)):  # nothing else the instrument keeps changes here
        live = bool(acq.shows_live())  # type: ignore[attr-defined]
        records = dict(acq.records)  # type: ignore[attr-defined]
        if live:
            # A running scope redraws the signal now; a frame is not an acquisition (no record,
            # no virtual time), and its noise is its own. Untriggered in a normal mode, it holds.
            frame = served.frames[instrument] = served.frames.get(instrument, 0) + 1
            try:
                shown, _, triggered = acq.compute(acq.displayed(), frame=frame)  # type: ignore[attr-defined]
            except ModelLimit:
                # Settings whose acquisition is beyond the model (an average of too many samples,
                # #986): the screen holds what it has, as the acquisition itself is refused.
                live = False
            else:
                if triggered or acq._is("auto") or not records:  # type: ignore[attr-defined]
                    records = shown
        traces = []
        for n, r in sorted(records.items()):
            volts = np.asarray(r.volts, dtype=float)
            minmax: list[float] | None = None
            if bins is not None and volts.size:
                # Per-bin min and max: split the samples into ``bins`` near-equal parts.
                edges = np.linspace(0, volts.size, bins + 1, dtype=int)
                chunks = (volts[edges[i] : edges[i + 1]] for i in range(bins))
                pairs = [(float(c.min()), float(c.max())) for c in chunks if c.size]
                flat = [v for pair in pairs for v in pair]
                minmax = flat if flat else None
            traces.append(
                M.Trace(
                    channel=n,
                    t0_s=r.origin,
                    dt_s=r.dt_s,
                    volts=[] if minmax is not None else [float(x) for x in volts],
                    scale=r.scale,
                    offset=r.offset,
                    triggered=r.triggered,
                    silence=_channel_silence(s, inst, n),
                    minmax=minmax,
                )
            )
        meas = next((c for c in inst.components.values() if c.type_name == "scope_measurements"), None)
        measurements = []
        if meas is not None:
            names = {
                "vpp": "VPP",
                "vmax": "VMAX",
                "vmin": "VMIN",
                "mean": "VAVerage",
                "rms": "VRMS",
                "frequency": "FREQuency",
                "period": "PERiod",
            }
            for n, r in sorted(records.items()):
                row: dict[str, Any] = {"channel": n}
                for key, what in names.items():
                    value = float(meas.record_value(what, r))  # type: ignore[attr-defined]
                    row[key] = value if math.isfinite(value) else None
                measurements.append(M.ScopeMeasurement(**row))
        return M.ScopeScreen(
            instrument=instrument,
            source="live" if live else "record",
            running=live,
            waiting=bool(acq.armed and acq._is("single")),  # type: ignore[attr-defined]
            timebase_scale=float(inst.get_state("timebase.scale")),
            trigger_level=acq.trigger_level(),  # type: ignore[attr-defined]
            trigger_source=channel_index(inst.get_state("trigger.source")),
            traces=traces,
            acquisitions=int(acq.acq_count),  # type: ignore[attr-defined]
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
        raise KeyError(net)
    with s.view():
        sol, emf = s.solution()
        sig = s.net_signal(net).merged()
        lines = sig.spectral_lines(max_lines=max_lines)
        # Two periods of the strongest line, or 2 ms for DC.
        f0 = lines[0].freq_hz if lines else 0.0
        window = 2.0 / f0 if f0 > 0 else 2e-3
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
            pos = sig.freq > 0
            amps = np.abs(sig.amp[pos])
            f0 = float(sig.freq[pos][int(np.argmax(amps))]) if amps.size else 0.0
            window = 2.0 / f0 if f0 > 0 else 2e-3
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
        raise KeyError(net)
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
