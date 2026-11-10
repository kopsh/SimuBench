"""The TUI's shared wording must match the Web Bench's English i18n exactly (tui-screens.md 13).

The terminal console is English only (D23). Parses ``web/src/i18n/en.ts`` (a nested ``as const``
object) with a tolerant tokenizer, imports ``simubench.tui.i18n``, and checks that every shared
entry (the mapping below) has the same text in the TUI as in the web i18n.

Fast and offline: part of ``scripts/check.sh``.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web" / "src" / "i18n"

#: Web-only wording (key prefixes in web/src/i18n): features the TUI does not have yet. The TUI must
#: not grow keys of its own under these names — when it gets the feature, its terms move to SHARED.
WEB_ONLY: tuple[str, ...] = (
    "assistant.",  # the AI assistant: Web only in the first version (#871, D66)
    "template.",  # "save as my template" (#875)
)

#: tui key → web key, for every term the two interfaces share.
SHARED: dict[str, str] = {
    "common.on": "common.on",
    "common.off": "common.off",
    "common.none": "common.none",
    "common.cancel": "common.cancel",
    "common.confirm": "common.confirm",
    "common.close": "common.close",
    "common.loading": "common.loading",
    "activity.idle": "workbench.activity.idle",
    "activity.busy": "workbench.activity.busy",
    "activity.wall": "workbench.activity.wall",
    "clockMode.command": "workbench.clockMode.command",
    "clockMode.wall": "workbench.clockMode.wall",
    "clockMode.stepped": "workbench.clockMode.stepped",
    "source.ui": "panel.client.ui",
    "source.mcp": "panel.client.mcp",
    "source.pyvisa": "panel.client.script",
    "source.replay": "panel.client.replay",
    "remote.local": "remote.local",
    "remote.eventLocal": "remote.eventLocal",
    "remote.eventRemote": "remote.eventRemote",
    "fault.active": "fault.active",
    "fault.pending": "fault.pending",
    "fault.ended": "fault.ended",
    "cards.output": "workbench.output",
}


def _tokens(src: str):
    """``{``, ``}``, ``key`` and ``"string"`` tokens; // comments and everything else skipped."""
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == '"':
            j = i + 1
            buf = []
            while j < n and src[j] != '"':
                if src[j] == "\\":
                    buf.append(src[j + 1])
                    j += 2
                else:
                    buf.append(src[j])
                    j += 1
            yield ("str", "".join(buf))
            i = j + 1
        elif c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
        elif c == "{":
            yield ("{", None)
            i += 1
        elif c == "}":
            yield ("}", None)
            i += 1
        elif c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            word = src[i:j]
            k = j
            while k < n and src[k] in " \t":
                k += 1
            if k < n and src[k] == ":":
                yield ("key", word)
            i = j
        else:
            i += 1


def parse_ts(path: Path) -> dict:
    """The nested object of a ``export const x = { … } as const`` file, as a dict of dicts."""
    root: dict = {}
    stack: list[dict] = []
    key = None
    for kind, value in _tokens(path.read_text(encoding="utf-8")):
        if not stack:  # outside the object literal (leading exports, trailing types)
            if kind == "{" and key is None:
                stack.append(root)  # the object literal's own brace
            key = None
            continue
        if kind == "key":
            key = value
        elif kind == "str":
            if key is not None:
                stack[-1][key] = value
                key = None
        elif kind == "{":
            if key is not None:
                new: dict = {}
                stack[-1][key] = new
                key = None
                stack.append(new)
            else:
                stack.append({})  # an unkeyed brace (type syntax): scratch
        elif kind == "}":
            stack.pop()
    return root


def flatten(node: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in node.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(flatten(v, path))
        else:
            out[path] = str(v)
    return out


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    from simubench.tui.i18n import en as tui_en

    tui = flatten(tui_en.TERMS)
    web = flatten(parse_ts(WEB / "en.ts"))
    problems: list[str] = []
    for prefix in WEB_ONLY:
        if not any(k.startswith(prefix) for k in web):
            problems.append(f"web-only prefix {prefix!r} has no key in en.ts")
        problems += [f"tui key {k} is under the web-only {prefix!r}: share it instead" for k in tui if k.startswith(prefix)]
    for tui_key, web_key in SHARED.items():
        if tui_key not in tui:
            problems.append(f"tui key missing: {tui_key}")
            continue
        if web_key not in web:
            problems.append(f"web key missing: {web_key} (for tui {tui_key})")
            continue
        if tui[tui_key] != web[web_key]:
            problems.append(f"{tui_key} = {tui[tui_key]!r} differs from web {web_key} = {web[web_key]!r}")

    if problems:
        for p in problems:
            print(p, file=sys.stderr)
        print(f"tui i18n: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(f"tui i18n: {len(SHARED)} shared terms match web/src/i18n/en.ts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
