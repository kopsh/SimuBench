"""Shared test helpers (#698): the skeletons every new per-model test file used to copy.

- ``load_script`` imports an ``examples/scripts`` file as a fresh module;
- ``run_manual_script`` runs such a script on a copy of its example bench and returns its stdout
  lines;
- ``session_query`` is the write-read-strip one-liner most per-model files define as ``q``;
- ``send_timed`` / ``record_steps`` / ``require_gold`` are the golden-table machinery of the
  ``*_migration.py`` tests; ``run_gold_step`` / ``record_gold_step`` are the per-step runner and
  recorder of the JSON gold recordings — one step shape for every gold file (#1635), re-recorded
  by ``uv run python tests/record_gold.py``;
- ``warm_up`` bills a definition's lazy loading to the wall clock before a recorded sequence
  (D47);
- ``reading_of`` / ``Near`` / ``StableScreen`` read a value back off a rendered screen and judge it
  against an expected value ± a tolerance (#926); ``raises_with`` checks an exception's message by
  plain substrings. Tests judge values and text with these, never with regular expressions.
- ``FakeHost`` is the instrument seam's fake session (``InstrumentHost``, #1380): the shared stub
  every test that builds an Instrument without a bench constructs it on.

Model-specific assertions (hand calculations, expectation tables) stay in the per-model files;
only the shared skeletons live here — don't copy them into new files.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import importlib.util
import math
import shutil
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import pyvisa
import yaml

from simubench.scpi.errors import ScpiError
from simubench.units import ENG_PREFIX

#: The box-drawing and panel-title glyphs around a TUI value; a reading is read between them.
_FRAME = {chr(c) for c in range(0x2500, 0x2580)} | {"▌", "▐"}

#: Engineering prefix -> factor, the inverse of the TUI's ``fmt`` ladder (``µ`` stays ``µ``).
_PREFIX_SCALE = {prefix: factor for factor, prefix in ENG_PREFIX}


def _words(line: str) -> list[str]:
    return "".join(" " if c in _FRAME else c for c in line).split()


def reading_of(text: str, label: str, unit: str) -> float | None:
    """The value a rendered screen shows after ``label`` in ``unit``, in base units, or None.

    The screen writes ``<label> <number> <prefix><unit>`` (``CH1 voltage  -24.18046 µV``,
    ``V  5.00002 V``). The label matches whole words, so ``V`` is not found inside ``Voltage``; the
    first place where a number and a unit with a known prefix follow it is the reading. A dash, an
    unknown unit or no label at all is no reading.
    """
    want = label.split()
    for line in text.splitlines():
        words = _words(line)
        for i in range(len(words) - len(want) - 1):
            if words[i : i + len(want)] != want:
                continue
            number, suffix = words[i + len(want)], words[i + len(want) + 1]
            if not suffix.endswith(unit) or suffix[: -len(unit)] not in _PREFIX_SCALE:
                continue
            try:
                value = float(number)
            except ValueError:
                continue
            if math.isfinite(value):
                return value * _PREFIX_SCALE[suffix[: -len(unit)]]
    return None


def printed_quantities(line: str, label: str) -> list[tuple[float, str]]:
    """The ``(value, unit)`` pairs a script printed as ``<label>: <n> <unit>, <n> <unit>, …``
    (``channel 1: 5.000012 V, 0.0500001 A, 0.25 W``), in order. Any float spelling the script's
    format gives (``5``, ``-2.4e-05``) reads back; a line with another label fails the test."""
    head, colon, rest = line.partition(": ")
    assert colon and head == label, f"{line!r} is not a {label!r} line"
    pairs = []
    for part in rest.split(", "):
        number, _, unit = part.partition(" ")
        pairs.append((float(number), unit))
    return pairs


@dataclass(frozen=True)
class Near:
    """A screen check: the reading after ``label`` is ``center`` ± ``tol`` (base units, #926).

    The tolerance comes from the model (k·σ of its readback noise, see ``noise_rms``); ``tol=0``
    asks for the exact value. Called with a render, it answers whether the reading is there and
    within the band — the number is judged, not the way it is printed.
    """

    label: str
    unit: str
    center: float
    tol: float = 0.0

    def __call__(self, text: str) -> bool:
        value = reading_of(text, self.label, self.unit)
        return value is not None and abs(value - self.center) <= self.tol


#: A thing a settled screen must show: a plain substring, or a check called with the render.
Shows = str | Callable[[str], bool]


def shows(text: str, check: Shows) -> bool:
    """Whether ``text`` shows ``check`` (a substring, or a callable such as :class:`Near`)."""
    return check in text if isinstance(check, str) else check(text)


def noise_rms(definition: Path, component: str, quantity: str) -> float:
    """The readback noise σ a definition gives ``component`` for ``quantity``
    (``noise: {rms: {voltage: 1.0e-4}}``, #744): tolerances follow the model, not a copy of it."""

    def walk(node):
        if isinstance(node, dict):
            params = node.get("params")
            noise = params.get("noise") if isinstance(params, dict) else None
            rms = noise.get("rms") if isinstance(noise, dict) else None
            if node.get("name") == component and isinstance(rms, dict) and quantity in rms:
                yield float(rms[quantity])
            for value in node.values():
                yield from walk(value)
        elif isinstance(node, list):
            for value in node:
                yield from walk(value)

    found = list(walk(yaml.safe_load(definition.read_text(encoding="utf-8"))))
    assert found, f"{definition.name}: no {quantity} noise on {component}"
    return found[0]


@contextlib.contextmanager
def raises_with(kind: type[BaseException], *parts: str) -> Iterator[pytest.ExceptionInfo]:
    """``pytest.raises(kind)`` whose message must contain every one of ``parts`` as written —
    plain substrings, no regular expression to escape or to match by accident."""
    with pytest.raises(kind) as caught:
        yield caught
    message = str(caught.value)
    for part in parts:
        assert part in message, f"{part!r} not in {message!r}"


class StableScreen:
    """Decides from successive renders whether a screen has settled (#896): the last two renders
    are identical and non-empty, show the ``needle`` and pass every ``ready`` check — a needle
    alone once captured a screen whose readings had not arrived yet. A check is a plain substring
    or a callable on the render (a :class:`Near` reading, #926). Pure text logic; the unit test is
    ``tests/test_tui_settle.py``, the snapshot tests feed it their renders."""

    def __init__(self, needle: str, ready: tuple[Shows, ...] = (), hold: int = 2) -> None:
        self.needle = needle
        self.ready = ready
        self.hold = hold
        self._previous: str | None = None
        self._same = 0

    def feed(self, text: str) -> bool:
        """Feed one render; True when the screen has settled."""
        if text and text == self._previous:
            self._same += 1
        else:
            self._same = 1
        self._previous = text
        if not text.strip() or self._same < self.hold:
            return False
        if self.needle and self.needle not in text:
            return False
        return all(shows(text, check) for check in self.ready)


def load_script(path: Path):
    """Import a script written to run on a real instrument (``python script.py <resource>``) as a
    fresh module, registered under its file stem."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def run_manual_script(
    script, resources, bench_src, tmp_path, monkeypatch, capsys, *, rm_wrap: bool = True
) -> list[str]:
    """Run an examples/scripts module on a copy of its example bench, the way the manual's flow
    runs on a real instrument: copy the bench into tmp_path, set argv to the script's own
    convention (``script.py <resources...> --bench <copy>``), run ``main()``, return stdout
    lines. ``rm_wrap`` keeps a ResourceManager open around ``main()`` — only the files that had
    one pass it on."""
    bench = tmp_path / "bench.yaml"
    shutil.copy(bench_src, bench)
    monkeypatch.setattr(sys, "argv", [Path(script.__file__).name, *resources, "--bench", str(bench)])
    rm = pyvisa.ResourceManager(f"{bench}@simubench") if rm_wrap else None
    try:
        script.main()
    finally:
        if rm is not None:
            rm.close()
    return capsys.readouterr().out.splitlines()


def session_query(s, instrument: str, command: str, *, default: str | None = None) -> str | None:
    """One command over an open session: write the line, read the reply, strip it. A command that
    answers nothing yields ``default`` (None — or "" where the file's helper keeps a str type)."""
    s.write(instrument, (command + "\n").encode())
    out = s.read(instrument)
    return out.decode().strip() if out else default


# ---- golden tables (the *_migration.py recordings, #186-#190) --------------------------------------


def send_timed(s, instrument: str, command: str) -> tuple[str | None, int]:
    """One command with the virtual time it consumed — the golden tables' unit (D47, command
    clock). The reply keeps its trailing spaces (``rstrip("\\n")`` only): the compares are exact."""
    before = s.now_ns
    s.write(instrument, (command + "\n").encode())
    reply = s.read(instrument)
    text = reply.decode().rstrip("\n") if reply is not None else None
    return text, s.now_ns - before


def warm_up(s, *instruments: str) -> list[str | None]:
    """``*IDN?`` before a recorded sequence: a freshly loaded definition bills its lazy loading to
    the wall clock, which would make the recorded times machine-dependent (D47)."""
    return [
        send_timed(s, instrument, "*IDN?")[0] for instrument in instruments if instrument in s.instruments
    ]


def record_steps(s, steps) -> list[dict]:
    """(instrument, command) pairs -> the recorded gold steps ``{"instrument", "cmd", "ns",
    "reply"}`` — the one row shape of the gold recordings (#1635). The reply slot appears only
    when the command answered one (the guards read ``step.get("reply", "")``), and keeps its
    trailing spaces (``rstrip("\\n")`` only): the compares are exact."""
    recorded = []
    for instrument, command in steps:
        text, ns = send_timed(s, instrument, command)
        step = {"instrument": instrument, "cmd": command, "ns": ns}
        if text is not None:
            step["reply"] = text
        recorded.append(step)
    return recorded


def require_gold(got, expected, label: str) -> None:
    """The gold guard: the recorded steps must equal the loaded table, step for step. An empty
    table fails naming the recorder — a re-recording writes the JSON itself, no rows are pasted
    by hand any more (#1635; the record-then-refactor method of #186-#190)."""
    if not expected:
        pytest.fail(f"{label} is empty: record it with uv run python tests/record_gold.py")
    assert got == expected


# The actual value of each gold step kind: shared by the runner and the recorder, so what a
# re-recording writes is exactly what the test then judges.


def _gold_lines(s, signal: str) -> list[list[float]]:
    """The spectral lines the net solver finds on a port, ``[freq, Re, Im]`` per line, frequency
    ascending — the ``signal`` step kind's actual value."""
    merged = s.net_signal(s.port_net[signal]).merged()
    order = np.argsort(merged.freq, kind="stable")
    return [
        [float(f), float(a.real), float(a.imag)]
        for f, a in zip(merged.freq[order], merged.amp[order], strict=True)
    ]


def _gold_trace(s, instrument: str) -> list[float]:
    """The analyzer's trace points, read off ``:TRAC:DATA?`` — the ``trace`` step kind's actual
    value. The read is the display's own, not part of the recorded sequence."""
    s.write(instrument, b":TRAC:DATA?\n")
    return [float(x) for x in s.read(instrument).decode().rstrip("\n").split(",")]


def record_gold_step(s, i: int, step: dict) -> dict:
    """One step of a JSON gold recording, recorded: the committed step drives the walk, its
    actual values come back in the step shape — the recording twin of ``run_gold_step``. The
    flags a step carries (``floats``) and its key order are preserved, so re-recording a file
    that still matches rewrites only the values; the reply slot appears only when the command
    answered one."""
    if "advance_s" in step:
        s.advance(step["advance_s"])
        return dict(step)
    if "cmd" in step:
        text, ns = send_timed(s, step["instrument"], step["cmd"])
        recorded = {**step, "ns": ns}
        if text is not None:
            recorded["reply"] = text
        elif "reply" in step:
            del recorded["reply"]
        return recorded
    if "signal" in step:
        return {**step, "lines": _gold_lines(s, step["signal"])}
    if "trace" in step:
        return {**step, "values": _gold_trace(s, step["instrument"])}
    raise ValueError(f"step {i}: not a gold step: {sorted(step)}")


def run_gold_step(s, i: int, step: dict) -> None:
    """One step of a JSON gold recording, judged (#1635's one step shape): advance the clock; or
    send the command and compare its consumed time and reply — exactly, or numerically (rel
    1e-9, the reply's last bits differ between machines) when the step says ``floats``; or
    compare the signal lines of a port / the trace points of an analyzer (rtol 1e-9). A command
    that answered nothing carries no reply slot. The step kinds live here, not in the per-model
    files."""
    if "advance_s" in step:
        before = s.now_ns
        s.advance(step["advance_s"])
        if "ns" in step:
            assert s.now_ns - before == step["ns"], f"step {i}: the advance took time"
        return
    if "cmd" in step:
        text, ns = send_timed(s, step["instrument"], step["cmd"])
        assert text == step.get("reply"), f"step {i}: {step['cmd']} replied {text!r}"
        assert ns == step["ns"], f"step {i}: {step['cmd']} took time"
        if step.get("floats"):
            actual = [float(v) for v in (text or "").split(",")]
            expected = [float(v) for v in step["reply"].split(",")]
            assert len(actual) == len(expected), f"step {i}: {step['cmd']} count"
            for a, e in zip(actual, expected, strict=True):
                assert a == pytest.approx(e, rel=1e-9, abs=1e-15), f"step {i}: {step['cmd']}"
        return
    if "signal" in step:
        actual = np.array(_gold_lines(s, step["signal"]), dtype=float).reshape(-1, 3)
        expected = np.array(step["lines"], dtype=float).reshape(-1, 3)
        assert actual.shape == expected.shape, f"step {i} ({step['signal']}): shape"
        np.testing.assert_allclose(
            actual[:, 0],
            expected[:, 0],
            rtol=1e-9,
            atol=1e-6,
            err_msg=f"step {i} ({step['signal']}): freq",
        )
        np.testing.assert_allclose(
            actual[:, 1:],
            expected[:, 1:],
            rtol=1e-9,
            atol=1e-12,
            err_msg=f"step {i} ({step['signal']}): amp",
        )
        return
    if "trace" in step:
        actual = np.array(_gold_trace(s, step["instrument"]))
        expected = np.array(step["values"])
        assert actual.shape == expected.shape, f"step {i} ({step['trace']}): shape"
        np.testing.assert_allclose(
            actual, expected, rtol=1e-9, atol=1e-12, err_msg=f"step {i} ({step['trace']})"
        )
        return
    raise ValueError(f"step {i}: not a gold step: {sorted(step)}")


def mcp_result(r, *, error_ok: bool = False):
    """An MCP tool call's result: the unwrapped structured content, or the JSON payload. With
    ``error_ok`` an error result comes back as ``{"error": text}`` for the test to assert on;
    without it an error fails right there (#700)."""
    if r.is_error:
        if error_ok:
            return {"error": r.content[0].text}
        raise AssertionError(r.content[0].text)
    if r.structured_content is not None:
        sc = r.structured_content
        return sc.get("result", sc) if set(sc) == {"result"} else sc
    import json

    return json.loads(r.content[0].text)


async def stop_manager_sessions(manager) -> None:
    """Stop every session a test's SessionManager still holds, the way a served bench's teardown
    must: uvicorn's exit does not close them — the SCPI and HiSLIP listening sockets belong to
    the sessions, and a leaked HiSLIP port makes the next module's fixed-port bind fail
    (issue 686, two TUI modules in one process). Sync callers wrap it in ``asyncio.run``."""
    for sid in list(manager.sessions):
        await manager.stop(sid)


def wait_for_free_ports(ports, timeout: float = 60.0, host: str = "127.0.0.1") -> None:
    """Block until every port in ``ports`` can be bound, polling, instead of letting the server
    that wants it fail with ``address already in use``. The snapshot fixtures need fixed ports (the
    identity box shows the addresses), and the module before them — or another xdist worker's —
    may still be letting go of them (#857). Binding with SO_REUSEADDR (POSIX), like the servers do, so a
    socket in TIME_WAIT does not count as taken; SO_EXCLUSIVEADDRUSE on Windows. Raises after ``timeout`` s, naming the ports."""
    import socket
    import sys
    import time

    def free(port: int) -> bool:
        with socket.socket() as probe:
            if sys.platform == "win32":
                # Windows' SO_REUSEADDR lets a second socket bind a port that is in use: the
                # probe would call it free. Exclusive use is the strict test there.
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, port))
            except OSError:
                return False
            return True

    end = time.monotonic() + timeout
    while True:
        busy = [p for p in ports if not free(p)]
        if not busy:
            return
        if time.monotonic() >= end:
            raise RuntimeError(f"ports still in use after {timeout:g} s: {busy}")
        time.sleep(0.02)


# ---- the instrument seam's fake host (#1380) -------------------------------------------------------


class FakeClock:
    """The fake host's clock: stepped, so nothing ever follows the wall."""

    mode = "stepped"
    time_scale = 1.0

    def calendar(self) -> dt.datetime:
        return dt.datetime(2026, 1, 1)

    def follow_wall(self) -> None:
        pass  # a stepped clock never follows the wall


class FakeEvents(list):
    """The fake host's event stream: the events themselves, in order, as dicts with their ``kind``."""

    def instrument_event(self, inst, kind: str, data: dict) -> None:
        self.append({"kind": kind, **data})


class FakeHost:
    """The instrument seam's fake session (``InstrumentHost``, #1380): just what building an
    Instrument and running its command pipeline touch — no circuit, no time. The routing sweep
    (``test_command_equivalence``) and the CommandRunner / BuiltinCommands / ErrorReporter unit
    tests construct their instruments on it; the constructor roll-call (``_host_gaps``) holds it
    to the protocol, so every member of the seam exists here, and the ones the pipeline must not
    reach (a reading, a solve) raise.

    ``events`` records what the instrument sent to the host: ``instrument_event`` and
    ``state_changed`` calls, as dicts with their ``kind``."""

    def __init__(self) -> None:
        self.seed = 1
        self.now_ns = 0
        self.clock = FakeClock()
        self.port_net: dict[str, str] = {}
        self.events = FakeEvents()

    # ---- circuit ----

    def port_ref(self, comp, port):
        return None  # nothing is exposed on the bench

    def port_signal(self, comp, port):
        raise AssertionError("the fake host has no circuit: the pipeline must not read signals")

    def cached_output(self, comp, port):
        raise AssertionError("the fake host has no circuit: the pipeline must not read outputs")

    def cached_frames(self, comp, port, request):
        raise AssertionError("the fake host has no frames: the pipeline must not read frames")

    def net_feed(self, comp, port, medium):
        raise AssertionError("the fake host has no nets: the pipeline must not follow a fibre")

    def solve(self):
        raise AssertionError("the fake host solves nothing: the pipeline must not solve")

    def sync(self) -> None:
        pass  # a state write syncs; nothing dynamic exists on the fake

    # ---- events ----

    def state_changed(self, inst, path, old, new, *, electrical: bool, source: str) -> None:
        self.events.append(
            {
                "kind": "state",
                "path": path,
                "old": old,
                "new": new,
                "electrical": electrical,
                "source": source,
            }
        )


def seam_instrument(definition, name: str = "x", host: FakeHost | None = None, options=()):
    """An Instrument built on a :class:`FakeHost`: the instrument-layer unit tests' bench-less
    environment. The definition must be a validated ``InstrumentDefinition``; ``options`` names
    the unit's fitted 选件 (D68)."""
    from simubench.engine.instrument import Instrument
    from simubench.models.bench import InstrumentInstance

    instance = InstrumentInstance(model=definition.model, options=list(options))
    return Instrument(name, definition, instance, host or FakeHost())


def dispatch_unit(inst, message: str):
    """One unit through the command runner's dispatch, the way ``receive`` feeds it: the reply and
    the time it consumed, or the raised ScpiError — ``receive`` is what queues a refused one."""
    from simubench.scpi.parse import parse_unit, resolve_tokens

    unit = parse_unit(message.encode())
    return inst.runner._dispatch(unit, resolve_tokens(unit, ()))


class FakeScopeOwner:
    """The instrument side of the scope family's seam (#1724): the reads and writes the family
    host protocols name — settings and their declared paths, number formatting, status events,
    the pending-operation clock, warnings. The fake host below carries one and delegates the way
    the real components delegate to the Instrument. ``writes`` and ``events`` record what came
    through; ``state`` is the live setting values the block reads back."""

    def __init__(self, state=None, specs=None):
        self.state = dict(state or {})
        self.specs = dict(specs or {})  # path -> a stand-in spec (.options, .type)
        self.writes: list[tuple] = []  # (path, value, source, internal)
        self.events: list[str] = []  # latched status-event names
        self.warnings: list[tuple] = []  # (code, message)
        self.pending_log: list[str] = []  # opc_query / wait_pending / arm_opc
        self.blocked: list[tuple] = []  # (uid, wait), the pending seam's bookkeeping
        self.registered: list[int] = []
        self.cancelled = 0
        self.now = 0
        self.waited_ns = 0
        self.on_panel = False
        self.channel_count = 4

    # ---- settings ----

    def get_state(self, path):
        try:
            return self.state[path]
        except KeyError:
            raise KeyError(f"fake owner has no state {path!r}") from None

    def set_state(self, path, value, *, source="scpi", internal=False):
        self.writes.append((path, value, source, internal))
        self.state[path] = value

    def format_number(self, value):
        return f"{value:.6E}".encode()

    def format_setting(self, path):
        return str(self.state.get(path, "")).encode()

    # ---- events, time, pending operations ----

    def status_event(self, name):
        self.events.append(name)

    def warn(self, code, message):
        self.warnings.append((code, message))

    def now_ns(self):
        return self.now

    def wait_until(self, done_ns):
        waited = max(0, done_ns - self.now)
        self.now = max(self.now, done_ns)
        self.waited_ns += waited
        return waited

    def opc_query(self):
        self.pending_log.append("opc_query")
        return b"1", 0

    def wait_pending(self):
        self.pending_log.append("wait_pending")
        return 0

    def arm_opc(self):
        self.pending_log.append("arm_opc")

    # ---- the pending seam (scope_pending's Acquisition protocol reads these) ----

    def block(self, uid, wait):
        self.blocked.append((uid, wait))

    def register_pending(self, uid, due_ns, done, waited):
        self.registered.append(due_ns)

    def cancel_pending(self, uid):
        self.cancelled += 1


class FakeScopeSession:
    """The session surface the scope blocks read: the event stream (a
    :class:`FakeEvents`), invalidation, the clock's reading."""

    def __init__(self, owner):
        self.owner = owner
        self.events = FakeEvents()
        self.invalidated = 0
        self.now_ns = 0
        self.seed = 1

    def invalidate(self):
        self.invalidated += 1

    def signal_changed(self, comp):
        return False


class FakeScopeCapture:
    """The acquisition's record source, scripted: ``compute`` answers the one record set it was
    given, ``triggers_now`` the flag."""

    def __init__(self, records, used=0, triggered=True, triggers_now=False):
        self.records = records
        self.used = used
        self.triggered = triggered
        self.triggers_flag = triggers_now
        self.computed: list[list[int]] = []

    def compute(self, channels, frame=None):
        self.computed.append(list(channels))
        return self.records, self.used, self.triggered

    def triggers_now(self):
        return self.triggers_flag


class _FakeScopeSegments:
    """One-record acquisitions: ``segment_count`` 1, the latest acquisition not segmented."""

    def __init__(self):
        self.segment_records = []

    def segment_count(self):
        return 1


class FakeScopeHost:
    """The scope family's fake host (#1724): what the family's blocks read of their component —
    standing in for a ScopeAcquisition, ScopeMeasurements, ScopeMath or ScopeTrigger, no bench
    and no signals. Settings live on a :class:`FakeScopeOwner` (``owner``, which
    scope_pending's scheduling seam also reads); ``params`` answers the parameters (``bound``
    names the settings they bind to); ``capture`` is a scripted
    :class:`FakeScopeCapture`. The real blocks (Recorder, RunControl, Cursors, Values) are
    constructed on it by the tests — the family protocols are the whole test surface."""

    def __init__(self, params=None, bound=None, owner=None):
        from simubench.library.components.scope_measurements import ScopeMeasurements

        self.owner = owner or FakeScopeOwner()
        self.session = FakeScopeSession(self.owner)
        self.params = dict(params or {})
        self.bound = dict(bound or {})
        self.uid = "scope.fake"
        self.name = "fake"
        self.running = True
        self.armed = False
        self.last_triggered = False
        self.defaults = {
            "status_text": {"stopped": "STOP", "waiting": "READY", "triggered": "TRIG'D", "auto": "AUTO"}
        }
        self.timing_value = (1000, 1e-6, 200, 800)
        self.displayed_channels = [1]
        self.level = 0.0
        self.capture = FakeScopeCapture(records={})
        self.segments = _FakeScopeSegments()
        self._trigger_unit = SimpleNamespace(last_ns=0)
        self.MODES = ScopeMeasurements.MODES
        self.XUNITS = ScopeMeasurements.XUNITS
        self.YUNITS = ScopeMeasurements.YUNITS

    # ---- parameters and the run mode ----

    def param(self, key):
        return self.params.get(key)

    def mode_is(self, key, value=None):
        words = self.params.get(key) or ()
        return str(self.params.get("mode") if value is None else value).upper() in {
            str(w).upper() for w in (words if isinstance(words, (list, tuple, set)) else (words,))
        }

    def mode_path(self):
        return self.bound.get("mode")

    def set_mode(self, value):
        path = self.mode_path()
        if path is None:
            raise ScpiError(-300, f"{self.uid}: the run mode is not bound to a setting")
        self.owner.set_state(path, value)
        self.params["mode"] = value

    # ---- settings and replies ----

    def get_state(self, path):
        return self.owner.get_state(path)

    def set_state(self, path, value, *, source="scpi", internal=False):
        self.owner.set_state(path, value, source=source, internal=internal)

    def resolve_path(self, path):
        return path

    def bound_path(self, key):
        return self.bound.get(key)

    def format_number(self, value):
        return self.owner.format_number(value)

    def format_setting(self, path):
        return self.owner.format_setting(path)

    # ---- the instrument's services ----

    def status_event(self, name):
        self.owner.status_event(name)

    def warn(self, code, message):
        self.owner.warn(code, message)

    def now_ns(self):
        return self.owner.now_ns()

    def wait_until(self, done_ns):
        return self.owner.wait_until(done_ns)

    def opc_query(self):
        return self.owner.opc_query()

    def wait_pending(self):
        return self.owner.wait_pending()

    def arm_opc(self):
        self.owner.arm_opc()

    @property
    def on_panel(self):
        return self.owner.on_panel

    def input_source(self, port):
        return None

    # ---- the acquisition's figures and collaborators ----

    def channels(self):
        return self.owner.channel_count

    def timing(self):
        return self.timing_value

    def displayed(self):
        return self.displayed_channels

    def trigger_level(self):
        return self.level

    def trigger_unit(self):
        return self._trigger_unit

    def averages(self):
        return 1

    # ---- the measurements family's declared tables ----

    @property
    def specs(self):
        return self.owner.specs

    @property
    def state(self):
        return self.owner.state


class ScriptedLLM:
    """A PydanticAI FunctionModel that plays a script, one entry per model request: a string is the
    answer, a list of ``(tool, args)`` is a round of tool calls, a callable gets the messages so far
    and returns either. After the last entry it keeps answering with the last one. ``seen`` holds
    every request's messages; ``returns()`` the tool results of the latest request."""

    def __init__(self, *script):
        from pydantic_ai.models.function import FunctionModel

        self.script = list(script)
        self.seen: list[list] = []
        self.model = FunctionModel(stream_function=self._stream)

    def factory(self, _settings=None):
        return self.model

    def returns(self, n: int = -1) -> list:
        from pydantic_ai.messages import ModelRequest, ToolReturnPart

        req = self.seen[n][-1]
        assert isinstance(req, ModelRequest)
        return [p.content for p in req.parts if isinstance(p, ToolReturnPart)]

    async def _stream(self, messages, info):
        from pydantic_ai.models.function import DeltaToolCall

        self.seen.append(list(messages))
        step = self.script[min(len(self.seen), len(self.script)) - 1]
        if callable(step):
            step = step(messages)
        if isinstance(step, str):
            yield step
            return
        import json as _json

        yield {
            i: DeltaToolCall(
                name=name, json_args=_json.dumps(args), tool_call_id=f"call-{len(self.seen)}-{i}"
            )
            for i, (name, args) in enumerate(step)
        }


def last_tool_return(messages, name: str):
    """The latest return of tool ``name`` in a request's messages."""
    from pydantic_ai.messages import ModelRequest, ToolReturnPart

    for m in reversed(messages):
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, ToolReturnPart) and p.tool_name == name:
                    return p.content
    return None
