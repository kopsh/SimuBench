"""The internal probe as a service (signal-chain.md 6; #951, the #894 pattern).

Reads the signal at one output port inside an instrument: an analog port's analytic signal, or one
frame of a sample stream. The read is passive — it runs under ``Session.view()``, starts no
acquisition and advances no counter, so two probes at the same instant give the same answer and a
script's next reply is what it would have been.

Raises ``ProbeWithheld`` (the bench hides true values and this instrument carries some: a signal
inside it shows its hidden per-unit values, so a calibration script's author could read the hidden
gain error off the ADC codes; an instrument at its nominal values hides nothing and is shown),
``KeyError`` (no such instrument or port; the message lists the ports that can be
probed) and ``ValueError`` (the port has no signal to give with the current settings). The caller
maps them.
"""

from __future__ import annotations

import re

import numpy as np

from ...engine.component import Component
from ...engine.instrument import Instrument
from ...engine.portref import split
from ...engine.session import Session
from ...engine.silence import why_silent
from ...engine.stream import FrameRequest, SampleFrame
from ...models import api as M
from ...scpi.errors import ScpiError
from ..errors import Forbidden
from ..manager import ServedSession

#: Samples in the frame a probe on a sample stream asks for.
PROBE_FRAME = 4096
#: Points of an analog port's waveform, and the lines listed.
WAVE_POINTS = 400
MAX_LINES = 16
#: Lowest level a spectrum reports, dB (an exact zero has no logarithm).
FLOOR_DB = -200.0

_MEDIA = ("signal", "samples")


class ProbeWithheld(Forbidden):
    """The bench runs without ``run.reveal_true_values`` and the instrument has hidden values: no
    signal inside it is shown."""


def withheld(s: Session, inst: Instrument) -> bool:
    """Whether the instrument's internal signals are kept back: the bench hides true values, and
    a component of the instrument carries a per-unit value (a drawn deviation, the bench's hidden
    state, a parameter fault — all live in ``Component.overrides``)."""
    return not s.reveal_true_values and any(c.overrides for c in inst.components.values())


def out_ports(inst: Instrument) -> dict[str, tuple[Component, str, str]]:
    """Every port of the instrument a probe can read — ``'<component>.<port>'`` to (component,
    port, medium): the signal and samples outputs, declared or created by a wire."""
    found: dict[str, tuple[Component, str, str]] = {}
    for name, comp in inst.components.items():
        for port, spec in comp.ports.items():
            if spec.direction == "out" and spec.medium in _MEDIA:
                found[f"{name}.{port}"] = (comp, port, spec.medium)
    for src, _dst in inst.wiring.wires():
        if src in found:
            continue
        cname, port = split(src)
        comp = inst.components[cname]
        spec = comp.spec_of(port)
        medium = spec.medium if spec else "signal"
        if medium in _MEDIA:
            found[src] = (comp, port, medium)
    return found


def stream_request(s: Session, comp: Component, port: str, count: int = PROBE_FRAME) -> FrameRequest:
    """The request a probe sends up a sample stream: ``count`` samples from now at the port's own
    rate. The noise index is the sample's place on the grid counted from time zero, so the frame
    is a function of the instant alone."""
    dt = stream_interval(comp, port)
    return FrameRequest(
        t0_ns=s.now_ns, count=count, dt_s=dt, key=("probe",), index0=int(s.now_ns * 1e-9 / dt)
    )


def stream_interval(comp: Component, port: str) -> float:
    """The interval a samples output runs at: its own, or — for a block that samples whenever it
    is asked — the one the block reading it asks for."""
    try:
        return comp.sample_interval(port)
    except ScpiError:
        if comp.owner is None:
            raise
        for src, dst in comp.owner.wiring.wires():
            if src == f"{comp.name}.{port}":
                reader, rport = split(dst)
                return comp.owner.components[reader].input_interval(rport)
        raise


def stream_format(frame: SampleFrame) -> M.ProbeFormat:
    return M.ProbeFormat(kind=frame.fmt.kind, bits=frame.fmt.bits)


def probe(sv: ServedSession, instrument: str, port: str, tune_hz: float | None = None) -> M.InternalProbe:
    s = sv.session
    inst = s.instrument(instrument)
    if withheld(s, inst):
        raise ProbeWithheld(
            f"the internal probe is not available in validation mode: {instrument} has hidden "
            "per-unit values and run.reveal_true_values is off"
        )
    ports = out_ports(inst)
    if port not in ports:
        raise KeyError(f"no output port {port!r} inside {instrument}; ports: {', '.join(sorted(ports))}")
    comp, cport, medium = ports[port]
    # tune_hz applies to instruments that declare a tuning context (#952); none does yet.
    with s.view():
        try:
            if medium == "samples":
                return _samples(s, inst, comp, cport, port)
            return _analog(s, inst, comp, cport, port)
        except (ScpiError, NotImplementedError) as exc:
            detail = exc.detail if isinstance(exc, ScpiError) else str(exc)
            raise ValueError(f"{port} has no signal to show with the current settings: {detail}") from None


def _analog(s: Session, inst: Instrument, comp: Component, cport: str, port: str) -> M.InternalProbe:
    sig = s.cached_output(comp, cport).merged()
    lines = sig.spectral_lines(max_lines=MAX_LINES)
    # Two periods of the strongest line, or 2 ms for DC: the network probe's window (D37).
    f0 = lines[0].freq_hz if lines else 0.0
    window = 2.0 / f0 if f0 > 0 else 2e-3
    volts = sig.sample_rel(s.now_ns, np.arange(WAVE_POINTS) * (window / WAVE_POINTS))
    notes = list(dict.fromkeys(n.detail for n in sig.notes))
    dc, peak = float(sig.mean()), float(sig.peak())
    return M.InternalProbe(
        port=port,
        medium="signal",
        t_ns=s.now_ns,
        dc=dc,
        rms=float(sig.rms()),
        peak=peak,
        waveform=M.NetWaveform(
            net=port,
            t0_ns=s.now_ns,
            dt_s=window / WAVE_POINTS,
            volts=[float(v) for v in volts],
            exact=sig.is_exact,
            notes=notes,
        ),
        lines=lines,
        notes=notes,
        silence=[] if peak > 0 or dc != 0 else _silence(s, inst, comp, cport),
    )


def _samples(s: Session, inst: Instrument, comp: Component, cport: str, port: str) -> M.InternalProbe:
    frame = s.cached_frames(comp, cport, stream_request(s, comp, cport))
    data = np.real(frame.data) if np.iscomplexobj(frame.data) else frame.data
    dc = float(np.mean(data))
    peak = float(np.max(np.abs(data)))
    fmt = frame.fmt
    quantized = fmt.bits > 0 and fmt.lsb > 0
    full_scale = fmt.lsb * (1 << fmt.bits) if quantized else 0.0
    notes = list(dict.fromkeys(n.detail for n in frame.notes))
    return M.InternalProbe(
        port=port,
        medium="samples",
        t_ns=s.now_ns,
        dc=dc,
        rms=float(np.sqrt(np.mean(data * data))),
        peak=peak,
        waveform=M.NetWaveform(
            net=port,
            t0_ns=s.now_ns,
            dt_s=frame.dt_s,
            volts=[float(v) for v in data],
            exact=not notes,
            notes=notes,
        ),
        spectrum=_spectrum(data, frame.dt_s, full_scale / 2 if quantized else 0.0),
        sample_rate_hz=frame.rate_hz,
        format=stream_format(frame),
        full_scale_used=float(np.max(data) - np.min(data)) / full_scale if quantized else None,
        clipped=frame.clipped,
        notes=notes,
        silence=[] if peak > 0 else _silence(s, inst, comp, cport),
    )


def _spectrum(data: np.ndarray, dt_s: float, full_scale_amp: float) -> M.ProbeSpectrum:
    """The frame's amplitude spectrum (Hann window, corrected for the window's gain): a sine reads
    its amplitude, against full scale (dBFS) where the stream is quantized, else in dBV (rms)."""
    n = data.size
    window = np.hanning(n)
    amp = np.abs(np.fft.rfft(data * window)) * 2.0 / float(np.sum(window))
    amp[0] /= 2.0  # the DC point is not split between two sides
    ref = full_scale_amp if full_scale_amp > 0 else float(np.sqrt(2.0))
    with np.errstate(divide="ignore"):
        level = np.maximum(20.0 * np.log10(amp / ref), FLOOR_DB)
    return M.ProbeSpectrum(
        freq_hz=[float(f) for f in np.fft.rfftfreq(n, dt_s)],
        level=[float(v) for v in level],
        unit="dBFS" if full_scale_amp > 0 else "dBV",
    )


_INDEXED = re.compile(r"\d+$")


def _silence(s: Session, inst: Instrument, comp: Component, cport: str) -> list[M.SilenceReason]:
    """Why an internal port reads 0: walk the instrument's wiring upstream to the panel input that
    feeds it, and ask the bench (``engine/silence.py``). A block with one input per channel
    (``in2`` for ``out2``) is followed along that channel only."""
    panel = {(cname, p): pname for pname, (cname, p) in inst.ports.items()}
    feeds: dict[str, list[tuple[str, str, str]]] = {}
    for src, dst in inst.wiring.wires():
        (a, ap), (b, bp) = split(src), split(dst)
        feeds.setdefault(b, []).append((bp, a, ap))
    seen: set[str] = set()
    queue = [(comp.name, cport)]
    while queue:
        cname, via = queue.pop(0)
        if cname in seen:
            continue
        seen.add(cname)
        block = inst.components[cname]
        for p, spec in block.ports.items():
            if spec.medium == "electrical" and spec.direction != "out" and (cname, p) in panel:
                ref = f"{inst.name}.{panel[(cname, p)]}"
                return list(why_silent(s, s.node_of(block, p), port_ref=ref))
        inputs = feeds.get(cname, [])
        index = _INDEXED.search(via)
        same = [i for i in inputs if index and (m := _INDEXED.search(i[0])) and m.group() == index.group()]
        queue.extend((a, ap) for _bp, a, ap in (same or inputs))
    return []
