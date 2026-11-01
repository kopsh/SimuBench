"""Scripted screenshots and demos (tui-screens.md 6, design 6): `--script steps.yaml --out dir`.

A steps file drives the app headlessly: `key` presses, `scpi` speaks as an external script over
the instrument's socket (so the log shows 脚本, like the reference video's other client),
`advance` moves the virtual clock, `wait` holds the frame, `caption` writes the bottom
subtitle, `highlight` frames the named controls, `screenshot` writes one SVG. With `idle: off`
and a fixed seed the same bench plus the same steps produce byte-identical SVGs (D47).
"""

from __future__ import annotations

import asyncio
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

#: Suffixes accepted in `wait` / `advance`: seconds by default, so `1.5` and `1500ms` are equal.
_SUFFIX = {"ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1.0}

NAMED_KEYS = {
    "enter",
    "escape",
    "tab",
    "shift+tab",
    "space",
    "up",
    "down",
    "left",
    "right",
    "colon",
    "question_mark",
    "ctrl+a",
    "delete",
    "end",
    "home",
}


@dataclass
class Steps:
    """The parsed steps file: `size`, the steps in order."""

    size: tuple[int, int]
    items: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> Steps:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        size = data.get("size") or (100, 32)
        return cls(size=(int(size[0]), int(size[1])), items=list(data.get("steps") or []))


def duration_seconds(value: Any) -> float | None:
    """A `wait` / `advance` value in seconds: ``1.5``, ``"1.5s"``, ``"200ms"``; None if invalid."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value) if value >= 0 else None
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    for suffix, scale in _SUFFIX.items():
        if text.endswith(suffix):
            number = text[: -len(suffix)].strip()
            try:
                return float(number) * scale
            except ValueError:
                return None
    try:
        return float(text)
    except ValueError:
        return None


def validate(steps: Steps) -> list[str]:
    """Known fields only, every step does something; a list of problems (empty = ok).

    ``caption``, ``highlight`` and ``wait`` decorate the step that moves the demo, so several
    may share one step (the design's ``key: "1"`` + ``caption: …`` form, 6).
    """
    problems: list[str] = []
    actions = {"key", "scpi", "advance", "caption", "highlight", "screenshot", "wait"}
    for i, step in enumerate(steps.items):
        unknown = sorted(set(step) - actions)
        if unknown:
            problems.append(f"step {i}: unknown field(s) {unknown}; known: {sorted(actions)}")
        if not (set(step) & actions):
            problems.append(f"step {i}: nothing to do")
        if "key" in step and not isinstance(step["key"], str):
            problems.append(f"step {i}: key must be a key name")
        if "scpi" in step:
            spec = step["scpi"]
            if not (isinstance(spec, dict) and "instrument" in spec and "command" in spec):
                problems.append(f"step {i}: scpi must be {{instrument, command}}")
            elif "source_port" in spec and not (
                isinstance(spec["source_port"], int) and 1 <= spec["source_port"] <= 65535
            ):
                problems.append(f"step {i}: scpi.source_port must be a port number")
        for key in ("wait", "advance"):
            if key in step and duration_seconds(step[key]) is None:
                problems.append(f"step {i}: {key} must be seconds, e.g. 1.5 or 1.5s")
        if "screenshot" in step and not isinstance(step["screenshot"], str):
            problems.append(f"step {i}: screenshot must be a file base name")
        if "highlight" in step and not isinstance(step["highlight"], list):
            problems.append(f"step {i}: highlight must be a list of control paths")
    return problems


def split_key(key: str) -> list[str]:
    """`enter` is one key; `4` is one key; `abc` types four characters."""
    return [key] if key in NAMED_KEYS else list(key)


async def external_scpi(app: Any, instrument: str, command: str, source_port: int | None = None) -> bytes:
    """One exchange as an external script would: a raw socket, so the source shows 脚本 (6).

    ``source_port`` pins the client's local port: the log names it, so a reproducible demo
    needs it fixed (the OS's choice would differ on every run).
    """
    info = await app.client.instrument(app.sid, instrument)
    port = int(info["scpi_port"])
    if source_port:
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # A reset close, so the pinned local port can be bound again by the next run.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        sock.bind(("127.0.0.1", source_port))
        sock.setblocking(False)
        await asyncio.get_running_loop().sock_connect(sock, ("127.0.0.1", port))
        reader, writer = await asyncio.open_connection(sock=sock)
    else:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(command.encode() + b"\n")
    await writer.drain()
    reply = b""
    if "?" in command:
        reply = await asyncio.wait_for(reader.readline(), timeout=10)
    writer.close()
    await writer.wait_closed()
    return reply


async def run_steps(app: Any, pilot: Any, steps: Steps, out_dir: Path, record: bool = False) -> list[Path]:
    """Run the steps inside the app's `run_test` context; write SVGs; return the files written.

    With ``record`` every step's hold becomes a short run of frames: ``frame-NNNN.svg`` (the
    same SVG ``--out`` would write) and the lines of an asciinema v2 ``demo.cast``, so the
    demo can be watched and, outside the runtime, turned into a video (6).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    highlights: list[str] = []
    cast: list[str] = []
    frame_no = 0

    for step in steps.items:
        if "caption" in step:
            app.set_caption(step["caption"], right=_step_right(step))
        if "highlight" in step:
            highlights = [str(p) for p in step["highlight"]]
            _set_highlights(app, highlights, True)
        if "key" in step:
            await pilot.press(*split_key(str(step["key"])))
        if "scpi" in step:
            spec = step["scpi"]
            await external_scpi(app, spec["instrument"], str(spec["command"]), spec.get("source_port"))
        if "advance" in step:
            await app.client.advance(app.sid, duration_seconds(step["advance"]) or 0.0, force=False)
        wait = duration_seconds(step["wait"]) if "wait" in step else None
        hold = (
            wait
            if wait is not None
            else (0.5 if ("caption" in step or "highlight" in step or "scpi" in step) else 0.0)
        )
        if hold:
            end = asyncio.get_running_loop().time() + hold
            while asyncio.get_running_loop().time() < end:
                await pilot.pause(0.05)
        if "screenshot" in step:
            path = out_dir / f"{step['screenshot']}.svg"
            path.write_text(app.export_screenshot(), encoding="utf-8")
            written.append(path)
        if record and hold:
            frame_no = _record_frames(app, cast, out_dir, frame_no, hold)
        if "highlight" in step:
            _set_highlights(app, highlights, False)
    if record:
        size = app.size
        header = {"version": 2, "width": size.width, "height": size.height}
        (out_dir / "demo.cast").write_text(
            json.dumps(header) + "\n" + "\n".join(cast) + "\n", encoding="utf-8"
        )
    return written


def _step_right(step: dict[str, Any]) -> str:
    """The caption's right side names what the viewer should look at: `key ▸ enter` (6)."""
    if "key" in step:
        return f"key ▸ {step['key']}"
    if "scpi" in step:
        return f"script ▸ {step['scpi'].get('command', '')}"
    if "advance" in step:
        return "key ▸ a"
    return ""


def _set_highlights(app: Any, paths: list[str], on: bool) -> None:
    """Frame the named controls in amber; a path names a control's state path (6)."""
    from .screens.instrument import ControlButton

    for widget in app.screen.query(ControlButton):
        if (widget.control.path or "") in paths:
            widget.set_class(on, "-highlighted")


FRAME_FPS = 8


def _record_frames(app: Any, cast: list[str], out_dir: Path, first: int, hold: float) -> int:
    """Write one SVG per ~1/8 s of the step's hold and append the same frames to the cast."""
    count = max(1, round(hold * FRAME_FPS))
    for i in range(count):
        index = first + i
        (out_dir / f"frame-{index:04d}.svg").write_text(app.export_screenshot(), encoding="utf-8")
        cast.append(json.dumps([index / FRAME_FPS, "o", _screen_ansi(app)]))
    return first + count


def _sgr(style: Any) -> str:
    """A SGR sequence for a strip segment's rich style (colors included when set)."""
    if style is None:
        return "\x1b[0m"
    parts = ["0"]
    for flag, code in (("bold", "1"), ("dim", "2"), ("italic", "3"), ("underline", "4"), ("reverse", "7")):
        if getattr(style, flag, False):
            parts.append(code)
    for name, code in (("color", "38"), ("bgcolor", "48")):
        color = getattr(style, name, None)
        triple = getattr(color, "triplet", None)
        if triple:
            parts.append(f"{code};2;{triple[0]};{triple[1]};{triple[2]}")
    return "\x1b[" + ";".join(parts) + "m"


def _screen_ansi(app: Any) -> str:
    """The current screen as a terminal frame (clear, then styled text), for the .cast."""
    strips = app.screen._compositor.render_strips()
    lines = []
    for strip in strips:
        parts = []
        for segment in strip:
            parts.append(_sgr(segment.style) + segment.text)
        lines.append("".join(parts) + "\x1b[0m")
    return "\x1b[2J\x1b[H" + "\n".join(lines)
