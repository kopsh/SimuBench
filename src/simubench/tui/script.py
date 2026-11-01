"""Scripted screenshots and demos (tui-screens.md 6, design 6): `--script steps.yaml --out dir`.

A steps file drives the app headlessly: `key` presses, `scpi` speaks as an external script over
the instrument's socket (so the log shows 脚本, like the reference video's other client),
`advance` moves the virtual clock, `wait` holds the frame, `caption` writes the bottom
subtitle, `highlight` frames the named controls, `screenshot` writes one SVG. With `idle: off`
and a fixed seed the same bench plus the same steps produce byte-identical SVGs (D47).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

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


def validate(steps: Steps) -> list[str]:
    """Every step has exactly one action and known fields; a list of problems (empty = ok)."""
    problems: list[str] = []
    actions = {"key", "scpi", "advance", "caption", "highlight", "screenshot"}
    for i, step in enumerate(steps.items):
        got = [k for k in step if k in actions]
        if len(got) != 1:
            problems.append(f"step {i}: exactly one of {sorted(actions)} is required, got {got}")
        if "key" in step and not isinstance(step["key"], str):
            problems.append(f"step {i}: key must be a key name")
        if "scpi" in step and not (
            isinstance(step["scpi"], dict) and "instrument" in step["scpi"] and "command" in step["scpi"]
        ):
            problems.append(f"step {i}: scpi must be {{instrument, command}}")
        for key in ("wait", "advance"):
            if key in step and not isinstance(step[key], (int, float)):
                problems.append(f"step {i}: {key} must be seconds")
        if "screenshot" in step and not isinstance(step["screenshot"], str):
            problems.append(f"step {i}: screenshot must be a file base name")
        if "highlight" in step and not isinstance(step["highlight"], list):
            problems.append(f"step {i}: highlight must be a list of control paths")
    return problems


def split_key(key: str) -> list[str]:
    """`enter` is one key; `4` is one key; `abc` types four characters."""
    return [key] if key in NAMED_KEYS else list(key)


async def external_scpi(app: Any, instrument: str, command: str) -> bytes:
    """One exchange as an external script would: a raw socket, so the source shows 脚本 (6)."""
    info = await app.client.instrument(app.sid, instrument)
    reader, writer = await asyncio.open_connection("127.0.0.1", info["scpi_port"])
    writer.write(command.encode() + b"\n")
    await writer.drain()
    reply = b""
    if "?" in command:
        reply = await asyncio.wait_for(reader.readline(), timeout=10)
    writer.close()
    return reply


async def run_steps(app: Any, pilot: Any, steps: Steps, out_dir: Path, record: bool = False) -> list[Path]:
    """Run the steps inside the app's `run_test` context; write SVGs; return the files written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    highlights: list[str] = []
    last_hold = 1.0
    frames: list[tuple[float, str]] = []

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
            await external_scpi(app, spec["instrument"], str(spec["command"]))
        if "advance" in step:
            await app.client.advance(app.sid, float(step["advance"]), force=False)
        hold = float(step.get("wait", 0.0)) or (
            0.5 if ("caption" in step or "highlight" in step or "scpi" in step) else 0.0
        )
        if hold:
            end = asyncio.get_running_loop().time() + hold
            while asyncio.get_running_loop().time() < end:
                await pilot.pause(0.05)
            if "wait" in step:
                last_hold = hold
        if "screenshot" in step:
            stem = str(step["screenshot"])
            path = out_dir / f"{stem}.svg"
            path.write_text(app.export_screenshot(), encoding="utf-8")
            written.append(path)
            frames.append((last_hold, stem))
        if "highlight" in step:
            _set_highlights(app, highlights, False)
    if record:
        await _write_frames(app, frames, out_dir)
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


async def _write_frames(app: Any, frames: list[tuple[float, str]], out_dir: Path) -> None:
    """--record: per-frame SVGs at ~8 fps over each hold, plus an asciinema .cast of the stems."""
    fps = 8
    cast: list[str] = []
    t = 0.0
    for hold, stem in frames:
        for _ in range(max(1, round(hold * fps))):
            path = out_dir / f"frame-{len(cast):04d}.svg"
            path.write_text(app.export_screenshot(), encoding="utf-8")
            cast.append(f'[{t:.6f}, "o", {stem!r}]')
            t += 1.0 / fps
    (out_dir / "demo.cast").write_text(
        '{"version": 2, "width": 100, "height": 32}\n' + "\n".join(cast) + "\n", encoding="utf-8"
    )
