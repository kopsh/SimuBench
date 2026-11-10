"""“Save as my template” (#875, D66): a bench instrument becomes a definition of its own.

Unlike “copy as my instrument” (D59, a ``my-<id>`` that ``extends`` the model and follows it),
the template is a **snapshot**: the inheritance chain and the category default are merged into one
mapping without ``extends``, so later changes to the built-in model never reach it. The instance's
settings fold into it — initial state becomes the default value (``*RST`` keeps the model's reset
value), ``idn``/``serial``/``firmware`` become the template's identity. Individual deviations stay
with the bench (the deviation *ranges* are the template's), and the address is not part of a
definition: a new instrument is given a free one when it is added (``engine/edit.py``).

Only a name is asked for: the id is made from it (and kept unique), manufacturer and model stay
the instance's. After saving, the bench's instrument points at the new template
(``replace_instrument``: name, wires and place are kept).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from ..engine.edit import ReplaceInstrument
from ..models import api as M
from .benchfiles import Conflict
from .definitionfiles import DefinitionFiles
from .history import Source

_CHANNEL_PATH = re.compile(r"ch(\d+)\.(.+)")


def template_id(name: str, fallback: str) -> str:
    """An id made from the name: ``my-`` and its ASCII letters and digits; the model's id when
    the name has none (a Chinese name)."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:40].strip("-")
    return f"my-{slug or fallback}"


def fold_state(data: dict[str, Any], state: dict[str, Any]) -> list[str]:
    """Fold an instance's initial state into the definition's defaults; return the paths that do
    not fold (a channel setting that differs between channels, a path the model lacks)."""
    count = int((data.get("channels") or {}).get("count", 1) or 1)
    per_channel: dict[str, dict[int, Any]] = {}
    not_folded: list[str] = []
    for path, value in state.items():
        m = _CHANNEL_PATH.fullmatch(path)
        if m and m.group(2) in (data.get("channel_state") or {}):
            per_channel.setdefault(m.group(2), {})[int(m.group(1))] = value
        elif path in (data.get("state") or {}):
            _set_default(data["state"][path], value)
        else:
            not_folded.append(path)
    for key, values in per_channel.items():
        distinct = {repr(v) for v in values.values()}
        if len(distinct) == 1 and (len(values) == count or count == 1):
            _set_default(data["channel_state"][key], next(iter(values.values())))
        else:
            not_folded += [f"ch{n}.{key}" for n in sorted(values)]
    return not_folded


def _set_default(spec: dict[str, Any], value: Any) -> None:
    if spec.get("rst") is None and spec.get("default") is not None:
        spec["rst"] = spec["default"]  # *RST still restores the model's reset value
    spec["default"] = value


def save_as_template(
    definitions: DefinitionFiles,
    bench_path: str,
    base_sha256: str,
    instrument: str,
    name: str,
    source: Source = "ui",
) -> M.SaveTemplateResponse:
    files = definitions.files
    bench = files.read(bench_path)
    if bench.sha256 != base_sha256:
        raise Conflict(f"{bench_path} changed since it was read", bench.sha256)
    inst = (bench.data.get("instruments") or {}).get(instrument)
    if not isinstance(inst, dict):
        raise KeyError(f"没有名为 {instrument!r} 的仪表")
    name = name.strip()
    if not name:
        raise ValueError("模板需要一个名称")
    model = str(inst.get("model"))
    data = definitions.expanded(model, bench_path)
    not_folded = fold_state(data, dict(inst.get("state") or {}))
    identity = dict(data.get("identity") or {})
    for key in ("idn", "serial", "firmware"):
        if inst.get(key) is not None:
            identity[key] = inst[key]
    if identity:
        data["identity"] = identity
    new_id = definitions.free_id(template_id(name, model))
    data["id"] = new_id
    data["description"] = name
    data.pop("template", None)  # a snapshot, not a template expansion any more
    doc = definitions.create(new_id, data, source)
    edited = files.edit(bench_path, base_sha256, [ReplaceInstrument(name=instrument, model=new_id)], source)
    return M.SaveTemplateResponse(definition=doc, bench=edited, not_folded=not_folded, based_on=model)
