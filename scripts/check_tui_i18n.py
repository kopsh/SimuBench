"""The TUI's shared wording must match the Web Bench's i18n exactly (tui-screens.md 13).

Parses ``web/src/i18n/{zh,en}.ts`` (nested ``as const`` objects) with a tolerant tokenizer,
imports ``simubench.tui.i18n``, and checks:

1. the TUI's zh and en key sets are identical;
2. every shared entry (the mapping below) has the same text in the TUI as in the web i18n,
   in both languages.

Fast and offline: part of ``scripts/check.sh``.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web" / "src" / "i18n"

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
    from simubench.tui.i18n import zh as tui_zh

    tui = {"zh": flatten(tui_zh.TERMS), "en": flatten(tui_en.TERMS)}
    web = {"zh": flatten(parse_ts(WEB / "zh.ts")), "en": flatten(parse_ts(WEB / "en.ts"))}
    problems: list[str] = []

    if set(tui["zh"]) != set(tui["en"]):
        only_zh = sorted(set(tui["zh"]) - set(tui["en"]))
        only_en = sorted(set(tui["en"]) - set(tui["zh"]))
        problems.append(f"TUI zh/en key sets differ: only zh {only_zh}, only en {only_en}")

    for lang in ("zh", "en"):
        for tui_key, web_key in SHARED.items():
            if tui_key not in tui[lang]:
                problems.append(f"[{lang}] tui key missing: {tui_key}")
                continue
            if web_key not in web[lang]:
                problems.append(f"[{lang}] web key missing: {web_key} (for tui {tui_key})")
                continue
            if tui[lang][tui_key] != web[lang][web_key]:
                problems.append(
                    f"[{lang}] {tui_key} = {tui[lang][tui_key]!r} differs from "
                    f"web {web_key} = {web[lang][web_key]!r}"
                )

    if problems:
        for p in problems:
            print(p, file=sys.stderr)
        print(f"tui i18n: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(f"tui i18n: {len(SHARED)} shared terms match web/src/i18n in zh and en")
    return 0


if __name__ == "__main__":
    sys.exit(main())
