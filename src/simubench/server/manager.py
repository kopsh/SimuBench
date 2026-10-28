"""Sessions served by ``simubench serve``: start, look up, describe."""

from __future__ import annotations

import asyncio
import copy
import logging
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from ..access.scpi_server import ScpiServer
from ..api import open_bench
from ..engine.faults import LinkDown
from ..engine.instrument import Instrument
from ..engine.session import Session
from ..engine.silence import why_silent
from ..library.loader import load_bench
from ..models import api as M
from ..models.bench import BenchDefinition
from ..scpi.errors import ScpiError
from .benchfiles import BenchFiles, scan
from .runs import Runs

log = logging.getLogger(__name__)


class BenchInvalid(ValueError):
    """The bench cannot start; ``diagnostics`` say why and where."""

    def __init__(self, diagnostics: list[M.Diagnostic]) -> None:
        first = next(d for d in diagnostics if d.severity == "error")
        super().__init__(f"{first.pointer}: {first.message}")
        self.diagnostics = diagnostics


class ServedSession:
    def __init__(self, sid: str, session: Session, bench_path: Path | None) -> None:
        self.id = sid
        self.session = session
        self.bench_path = bench_path
        self.bench_sha256: str | None = None
        self.bench_fingerprint: str | None = None
        self.scpi: ScpiServer | None = None


class SessionManager:
    def __init__(
        self,
        workspace: Path,
        *,
        scpi_host: str = "127.0.0.1",
        scpi_base_port: int | None = 5025,
        record_dir: Path | None = None,
        record_raw: bool = False,
    ) -> None:
        self.workspace = workspace
        self.scpi_host = scpi_host
        self.scpi_base_port = scpi_base_port
        self.record_dir = record_dir
        self.record_raw = record_raw
        self.sessions: dict[str, ServedSession] = {}
        self.files = BenchFiles(workspace)
        self.runs = Runs(record_dir)

    # ---- bench files --------------------------------------------------------------------------------

    def bench_files(self) -> list[M.BenchFile]:
        out: list[M.BenchFile] = []
        for p in scan(self.workspace):
            rel = p.relative_to(self.workspace).as_posix()
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

    def resolve(self, bench_path: str) -> Path:
        return self.files.resolve(bench_path)

    # ---- sessions -----------------------------------------------------------------------------------

    async def start(
        self, bench_path: str, clock: str | None = None, seed: int | None = None
    ) -> ServedSession:
        path = self.resolve(bench_path)
        doc = self.files.read(bench_path)
        if not doc.ok:
            raise BenchInvalid(doc.diagnostics)
        session = open_bench(
            path, clock=clock, seed=seed, record_dir=self.record_dir, record_raw=self.record_raw
        )
        served = ServedSession(uuid.uuid4().hex[:12], session, path)
        served.bench_sha256 = doc.sha256
        served.bench_fingerprint = doc.fingerprint
        # One bench serves the SCPI ports at a time; a new session takes them over.
        for other in list(self.sessions.values()):
            await self.stop(other.id)
        if self.scpi_base_port is not None:
            served.scpi = ScpiServer(session, self.scpi_host, self.scpi_base_port)
            await served.scpi.start()
        self.sessions[served.id] = served
        return served

    async def stop(self, sid: str) -> None:
        served = self.sessions.pop(sid, None)
        if served is None:
            return
        if served.scpi:
            await served.scpi.stop()
        await asyncio.to_thread(served.session.close)

    def preview(self, bench_path: str) -> M.BenchPreview:
        """Build the bench without running it, leaving out what cannot be built."""
        doc = self.files.read(bench_path)
        out = M.BenchPreview(path=bench_path, sha256=doc.sha256)
        errors = [d for d in doc.diagnostics if d.severity == "error"]
        if any(d.code == "E-SCHEMA" for d in errors):
            out.error = "文件不符合测试台格式"
            return out
        data = copy.deepcopy(doc.data)
        instruments, duts = data.get("instruments") or {}, data.get("duts") or {}
        dropped_wires: set[int] = set()
        for d in errors:
            parts = [p.replace("~1", "/").replace("~0", "~") for p in d.pointer.split("/")[1:]]
            if d.code.startswith("E-STATE-") and len(parts) >= 4:
                instruments.get(parts[1], {}).get("state", {}).pop(parts[3], None)
                out.omitted.append(f"{parts[1]}.{parts[3]}")
            elif d.code == "E-MODEL-UNKNOWN" and d.subject:
                instruments.pop(d.subject, None)
                out.omitted.append(d.subject)
            elif d.code in ("E-COMPONENT-UNKNOWN", "E-NAME-DUPLICATE") and d.subject:
                duts.pop(d.subject, None)
                out.omitted.append(d.subject)
            elif d.subject and d.subject.startswith("wire:"):
                dropped_wires.add(int(d.subject[5:]))
        names = set(instruments) | set(duts)
        wires = data.get("wires") or []
        kept: list[int] = []
        for i, w in enumerate(wires):
            ends = (str(w.get("from", "")), str(w.get("to", "")))
            if i in dropped_wires or any(e.rpartition(".")[0] not in names for e in ends):
                out.omitted.append(f"wire:{i}")
            else:
                kept.append(i)
        data["wires"] = [wires[i] for i in kept]
        path = self.resolve(bench_path)
        try:
            bench = BenchDefinition.model_validate(data)
            session = Session(bench, self.files.library(path), base_dir=path.parent)
        except Exception as exc:
            out.error = str(exc).splitlines()[0]
            return out
        try:
            info = self.describe(ServedSession("preview", session, path))
        finally:
            session.close()
        for w in info.wires:
            w.index = kept[w.index]
        info.bench_sha256 = doc.sha256
        info.bench_fingerprint = doc.fingerprint
        out.session = info
        return out

    def get(self, sid: str) -> ServedSession:
        try:
            return self.sessions[sid]
        except KeyError:
            raise KeyError(sid) from None

    # ---- descriptions -------------------------------------------------------------------------------

    def describe(self, served: ServedSession) -> M.SessionInfo:
        s = served.session
        b = s.bench
        ports = {lst.instrument: lst.port for lst in (served.scpi.listeners if served.scpi else [])}
        instruments = [
            self.describe_instrument(s, inst, i, ports.get(name))
            for i, (name, inst) in enumerate(s.instruments.items())
        ]
        duts = [self._component_info(s, name) for name in b.duts]
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
            bench_path=served.bench_path.relative_to(self.files.workspace).as_posix()
            if served.bench_path
            else None,
            description=b.description,
            clock_mode=s.clock_mode,  # type: ignore[arg-type]
            now_ns=s.now_ns,
            seed=s.seed,
            reveal_true_values=s.reveal_true_values,
            bench_sha256=served.bench_sha256,
            bench_fingerprint=served.bench_fingerprint,
            record_path=str(s.recorder.path) if s.recorder else None,
            instruments=instruments,
            duts=duts,
            wires=wires,
            nets=[M.NetInfo(id=n.id, members=n.members, notes=_net_notes(s, n.id)) for n in s.nets.values()],
            layout=b.layout,
        )

    def _component_info(self, s: Session, name: str) -> M.ComponentInfo:
        comp = s.components[name]
        label = s.bench.duts[name].label if name in s.bench.duts else None
        ports = [
            M.PortInfo(
                name=p, direction=spec.direction, medium=spec.medium, net=s.port_net.get(f"{name}.{p}")
            )
            for p, spec in comp.ports.items()
        ]
        return M.ComponentInfo(
            name=name, type=comp.type_name, label=label, params=_plain(comp.describe()), ports=ports
        )

    def describe_instrument(
        self, s: Session, inst: Instrument, index: int, scpi_port: int | None
    ) -> M.InstrumentInfo:
        d = inst.definition
        ports = []
        for pname, (cname, cport) in inst.ports.items():
            comp = inst.components[cname]
            spec = comp.ports[cport]
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
                    path=path,
                    type=spec.type,
                    unit=spec.unit,
                    min=spec.min,
                    max=spec.max,
                    options=spec.options,
                    option_labels=spec.option_labels,
                    display_unit=spec.display_unit,
                    label=spec.label,
                    label_en=spec.label_en,
                    readonly=spec.readonly,
                    electrical=spec.electrical,
                    dangerous=generic in dangerous_paths,
                    value=_plain(inst.state.get(path)),
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
            scpi_port=scpi_port,
            display=M.DisplayInfo(**d.display.model_dump()),
            ports=ports,
            state=state,
            errors=[M.ErrorItem(code=e.code, message=e.message) for e in inst.errors.peek_all()],
        )

    # ---- actions ------------------------------------------------------------------------------------

    @staticmethod
    def scpi(served: ServedSession, instrument: str, command: str) -> M.ScpiResponse:
        s = served.session
        inst = s.instrument(instrument)
        before = len(inst.errors)
        try:
            s.write(instrument, command.encode(), client="ui")
            out = s.read(instrument, client="ui")
        except LinkDown as exc:
            return M.ScpiResponse(link_error=str(exc), now_ns=s.now_ns)
        new_errors = inst.errors.peek_all()[before:]
        return M.ScpiResponse(
            response=out.decode(errors="replace").rstrip("\n") if out is not None else None,
            errors=[M.ErrorItem(code=e.code, message=e.message) for e in new_errors],
            timed_out=out is None and "?" in command,
            now_ns=s.now_ns,
        )

    @staticmethod
    def configure(served: ServedSession, instrument: str, path: str, value: Any) -> M.ConfigureResponse:
        s = served.session
        err = s.try_configure(instrument, path, value, client="ui")
        current = _plain(s.instrument(instrument).state.get(path))
        if err is None:
            return M.ConfigureResponse(ok=True, value=current)
        return M.ConfigureResponse(
            ok=False, error=M.ErrorItem(code=err.code, message=err.message), value=current
        )

    @staticmethod
    def scope_screen(served: ServedSession, instrument: str, source: str) -> M.ScopeScreen:
        s = served.session
        inst = s.instrument(instrument)
        acq = inst.components.get("acq")
        if acq is None or not hasattr(acq, "compute"):
            raise KeyError(f"{instrument} is not an oscilloscope")
        with s._lock:  # read a consistent view without changing anything
            s.follow_wall()
            if source == "record":
                records = dict(acq.records)  # type: ignore[attr-defined]
            else:
                records, _, _ = acq.compute(acq.displayed())  # type: ignore[attr-defined]
            traces = [
                M.Trace(
                    channel=n,
                    t0_s=-r.trigger_index * r.dt_s,
                    dt_s=r.dt_s,
                    volts=[float(x) for x in np.asarray(r.volts, dtype=float)],
                    scale=r.scale,
                    offset=r.offset,
                    triggered=r.triggered,
                    silence=_channel_silence(s, inst, n),
                )
                for n, r in sorted(records.items())
            ]
            return M.ScopeScreen(
                instrument=instrument,
                source=source,  # type: ignore[arg-type]
                timebase_scale=float(inst.get_state("timebase.scale")),
                trigger_level=float(inst.get_state("trigger.level")),
                trigger_source=int(str(inst.get_state("trigger.source")).removeprefix("CHANnel")),
                traces=traces,
                acquisitions=int(acq.acq_count),  # type: ignore[attr-defined]
            )

    @staticmethod
    def readings(served: ServedSession, instrument: str) -> M.InstrumentReadings:
        s = served.session
        inst = s.instrument(instrument)
        values: dict[str, Any] = {}
        traces: list[M.ReadingTrace] = []
        with s._lock:
            s.follow_wall()
            for name, comp in inst.components.items():
                r = comp.readings()
                if not r:
                    continue
                for k, v in r.get("values", {}).items():
                    values[f"{name}.{k}"] = _plain(v)
                for t in r.get("traces", []):
                    traces.append(M.ReadingTrace(**{**t, "name": f"{name}.{t['name']}"}))
            return M.InstrumentReadings(instrument=instrument, now_ns=s.now_ns, values=values, traces=traces)

    @staticmethod
    def probe(served: ServedSession, net: str, max_lines: int = 16) -> M.NetProbe:
        """Read a net without touching it, and break its signal down by independent source."""
        s = served.session
        if net not in s.nets:
            raise KeyError(net)
        with s._lock:
            s.follow_wall()
            sol, emf = s._solve()
            sig = s.net_signal(net).merged()
            pos = sig.freq > 0
            order = np.argsort(-np.abs(sig.amp[pos]))
            f_pos, a_pos = sig.freq[pos], np.abs(sig.amp[pos])
            lines = [
                M.SpectralLine(freq_hz=float(f_pos[i]), amplitude=float(a_pos[i])) for i in order[:max_lines]
            ]
            # Two periods of the strongest line, or 2 ms for DC.
            f0 = float(f_pos[order[0]]) if order.size else 0.0
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
            sources: list[M.SourceContribution] = []
            for sid, src in emf.items():
                if sid not in sol.sids:
                    continue
                m = src.merged()
                h0 = complex(sol.transfer(net, sid, np.zeros(1))[0])
                src_lines: list[M.SourceLine] = []
                if m.freq.size:
                    hs = sol.transfer(net, sid, m.freq)
                    for f, a, h in zip(m.freq, m.amp, hs, strict=True):
                        c = abs(a * h)
                        if c > 0 and f > 0:
                            src_lines.append(
                                M.SourceLine(
                                    freq_hz=float(f),
                                    emf=float(abs(a)),
                                    gain=float(abs(h)),
                                    phase_deg=float(np.degrees(np.angle(h))),
                                    contribution=float(c),
                                )
                            )
                dc_c = m.dc * h0.real
                if not src_lines and dc_c == 0:
                    continue  # this source does not reach the net
                src_lines.sort(key=lambda x: -x.contribution)
                rms = float(np.sqrt(dc_c**2 + sum(x.contribution**2 for x in src_lines) / 2))
                owner, _, comp = str(sid).partition("/")
                sources.append(
                    M.SourceContribution(
                        source=str(sid),
                        instrument=owner if comp else None,
                        component=comp or owner,
                        dc_emf=float(m.dc),
                        dc_gain=float(h0.real),
                        dc_contribution=float(dc_c),
                        lines=src_lines[:max_lines],
                        rms_contribution=rms,
                    )
                )
            sources.sort(key=lambda x: -x.rms_contribution)
            return M.NetProbe(
                net=net,
                members=list(s.nets[net].members),
                t_ns=s.now_ns,
                dc=sig.mean(),
                rms=sig.rms(),
                peak=sig.peak(),
                lines=lines,
                line_count=int(pos.sum()),
                waveform=wave,
                sources=sources,
                notes=[n.detail for n in sig.notes],
                silence=[_silence(x) for x in why_silent(s, net)],
            )

    @staticmethod
    def net_waveform(served: ServedSession, net: str, window_s: float, points: int) -> M.NetWaveform:
        s = served.session
        if net not in s.nets:
            raise KeyError(net)
        with s._lock:
            s.follow_wall()
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


def _plain(value: Any) -> Any:
    """JSON-friendly values: infinities become strings, numpy scalars become Python numbers."""
    if isinstance(value, float) and not np.isfinite(value):
        return "INF" if value > 0 else "-INF" if value < 0 else "NAN"
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


__all__ = ["ScpiError", "ServedSession", "SessionManager"]


def _net_notes(s: Session, net: str) -> list[str]:
    """Fidelity notes of the signal on a net (empty when exact); a model failure is a note too."""
    try:
        with s._lock:
            return list(dict.fromkeys(n.detail for n in s.net_signal(net).notes))
    except Exception as exc:  # describing a session must not fail because one net cannot be solved
        log.exception("net %s of %s", net, s.bench.name)
        return [f"model error: {type(exc).__name__}"]


def _silence(x: Any) -> M.SilenceReason:
    return M.SilenceReason(kind=x.kind, path=x.path, source=x.source, fault_id=x.fault_id, detail=x.detail)


def _channel_silence(s: Session, inst: Instrument, channel: int) -> list[M.SilenceReason]:
    """Why a scope channel's input reads 0 V: its input port, traced upstream (issue 22)."""
    for pname, (comp, port) in inst.ports.items():
        if comp.startswith(f"ch{channel}.") and inst.components[comp].ports[port].direction == "in":
            ref = f"{inst.name}.{pname}"
            return [_silence(x) for x in why_silent(s, s.node_of(inst.components[comp], port), port_ref=ref)]
    return []
