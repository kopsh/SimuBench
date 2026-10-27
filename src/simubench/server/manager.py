"""Sessions served by ``simubench serve``: start, look up, describe."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from ..access.scpi_server import ScpiServer
from ..api import open_bench
from ..engine.instrument import Instrument
from ..engine.session import Session
from ..library.loader import load_bench
from ..models import api as M
from ..scpi.errors import ScpiError


class ServedSession:
    def __init__(self, sid: str, session: Session, bench_path: Path | None) -> None:
        self.id = sid
        self.session = session
        self.bench_path = bench_path
        self.scpi: ScpiServer | None = None


class SessionManager:
    def __init__(
        self,
        workspace: Path,
        *,
        scpi_host: str = "127.0.0.1",
        scpi_base_port: int | None = 5025,
        record_dir: Path | None = None,
    ) -> None:
        self.workspace = workspace
        self.scpi_host = scpi_host
        self.scpi_base_port = scpi_base_port
        self.record_dir = record_dir
        self.sessions: dict[str, ServedSession] = {}

    # ---- bench files --------------------------------------------------------------------------------

    def bench_files(self) -> list[M.BenchFile]:
        out: list[M.BenchFile] = []
        for p in sorted(self.workspace.rglob("*.bench.yaml")) + sorted(self.workspace.rglob("*.bench.json")):
            if any(part in ("node_modules", ".venv", ".git") for part in p.parts):
                continue
            rel = str(p.relative_to(self.workspace))
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
        p = (self.workspace / bench_path).resolve()
        if self.workspace.resolve() not in p.parents and p != self.workspace.resolve():
            raise PermissionError("bench path outside the workspace")
        return p

    # ---- sessions -----------------------------------------------------------------------------------

    async def start(
        self, bench_path: str, clock: str | None = None, seed: int | None = None
    ) -> ServedSession:
        path = self.resolve(bench_path)
        session = open_bench(path, clock=clock, seed=seed, record_dir=self.record_dir)
        served = ServedSession(uuid.uuid4().hex[:12], session, path)
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
            bench_path=str(served.bench_path.relative_to(self.workspace)) if served.bench_path else None,
            description=b.description,
            clock_mode=s.clock_mode,  # type: ignore[arg-type]
            now_ns=s.now_ns,
            seed=s.seed,
            reveal_true_values=s.reveal_true_values,
            record_path=str(s.recorder.path) if s.recorder else None,
            instruments=instruments,
            duts=duts,
            wires=wires,
            nets=[M.NetInfo(id=n.id, members=n.members) for n in s.nets.values()],
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
                    medium=spec.medium,
                    net=s.port_net.get(f"{inst.name}.{pname}"),
                    channel=comp.channel,
                )
            )
        state = []
        for path, spec in inst.specs.items():
            ch = int(path[2 : path.index(".")]) if path.startswith("ch") and path[2:3].isdigit() else None
            state.append(
                M.StateVarInfo(
                    path=path,
                    type=spec.type,
                    unit=spec.unit,
                    min=spec.min,
                    max=spec.max,
                    options=spec.options,
                    label=spec.label,
                    label_en=spec.label_en,
                    readonly=spec.readonly,
                    electrical=spec.electrical,
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
        s.write(instrument, command.encode(), client="ui")
        out = s.read(instrument, client="ui")
        new_errors = inst.errors.peek_all()[before:]
        return M.ScpiResponse(
            response=out.decode(errors="replace").rstrip("\n") if out is not None else None,
            errors=[M.ErrorItem(code=e.code, message=e.message) for e in new_errors],
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
    def net_waveform(served: ServedSession, net: str, window_s: float, points: int) -> M.NetWaveform:
        s = served.session
        if net not in s.nets:
            raise KeyError(net)
        with s._lock:
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
