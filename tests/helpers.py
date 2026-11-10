"""Shared test helpers (#698): the skeletons every new per-model test file used to copy.

- ``load_script`` imports an ``examples/scripts`` file as a fresh module;
- ``run_manual_script`` runs such a script on a copy of its example bench and returns its stdout
  lines;
- ``session_query`` is the write-read-strip one-liner most per-model files define as ``q``;
- ``send_timed`` / ``record_rows`` / ``require_gold`` are the golden-table machinery of the
  ``*_migration.py`` tests; ``run_gold_step`` is the per-step runner of the JSON gold recordings;
- ``warm_up`` bills a definition's lazy loading to the wall clock before a recorded sequence
  (D47).

Model-specific assertions (hand calculations, expectation tables) stay in the per-model files;
only the shared skeletons live here — don't copy them into new files.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import sys
from pathlib import Path

import pytest
import pyvisa


class StableScreen:
    """Decides from successive renders whether a screen has settled (#896): the last two renders
    are identical and non-empty, show the ``needle`` and match every ``ready`` pattern — a
    needle alone once captured a screen whose readings had not arrived yet. A pattern is a plain
    substring, or a regular expression with a ``re:`` prefix. Pure text logic; the unit test is
    ``tests/test_tui_settle.py``, the snapshot tests feed it their renders."""

    def __init__(self, needle: str, ready: tuple[str, ...] = (), hold: int = 2) -> None:
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
        return all(
            re.search(pattern[3:], text) if pattern.startswith("re:") else pattern in text
            for pattern in self.ready
        )


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


def record_rows(s, steps) -> list[tuple[str, str, str | None, int]]:
    """(instrument, command, reply, ns consumed) for a (instrument, command) sequence — the row
    shape of the golden tables."""
    return [(instrument, command, *send_timed(s, instrument, command)) for instrument, command in steps]


def require_gold(got, expected, label: str) -> None:
    """The recording loop's guard: an empty expectation table prints the actual rows for the
    recorder to paste, then fails (the record-then-refactor method of #186-#190)."""
    if not expected:
        for row in got:
            print(repr(row))
        pytest.fail(f"{label} is empty: paste the output above")
    assert got == expected


def run_gold_step(s, i: int, step: dict) -> tuple[bool, str | None]:
    """One step of a JSON gold recording (lockin/rfgen/specan): a clock advance or a command with
    its expected reply and consumed time. Returns (handled, reply text); the caller adds the step
    kinds specific to its model (signal lines, traces, float tolerances)."""
    if "advance_s" in step:
        s.advance(step["advance_s"])
        return True, None
    if "cmd" not in step:
        return False, None
    text, ns = send_timed(s, step["instrument"], step["cmd"])
    assert text == step.get("reply"), f"step {i}: {step['cmd']} replied {text!r}"
    assert ns == step["ns"], f"step {i}: {step['cmd']} took time"
    return True, text


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


# ---- the AI assistant (#871): a scripted LLM ------------------------------------------------------


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
