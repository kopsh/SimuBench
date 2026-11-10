"""The assistant's LLM settings (#872, docs/ai-assistant.md 6).

Defaults come from the server: the environment (``SIMUBENCH_LLM_*``) over the ``assistant`` block
of the workspace's ``.simubench/settings.json``. The Web settings page saves overrides in
``.simubench/assistant.json`` (the folder is git-ignored; the file is made readable by its owner
only — on Windows as far as the platform allows). The API key is write-only: no response, log line
or diagnostics bundle carries it; :class:`AssistantConfig` reports ``api_key_set`` instead.

The address must be ``http`` or ``https`` and may be a private network address (a private
deployment lives there), but it may not point back at this SimuBench service's own ports (Web,
SCPI, HiSLIP, VXI-11, the bench link): the request is the server's, and it would talk to itself.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import os
import stat
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from ..models import api as M

if TYPE_CHECKING:
    from ..server.manager import SessionManager

OVERRIDES = Path(".simubench") / "assistant.json"
SETTINGS = Path(".simubench") / "settings.json"
ENV = {
    "protocol": "SIMUBENCH_LLM_PROTOCOL",
    "base_url": "SIMUBENCH_LLM_BASE_URL",
    "model": "SIMUBENCH_LLM_MODEL",
    "api_key": "SIMUBENCH_LLM_API_KEY",
    "timeout_s": "SIMUBENCH_LLM_TIMEOUT",
    "context_tokens": "SIMUBENCH_LLM_CONTEXT_TOKENS",
}
#: How many SCPI ports past the base one a bench may take (one per instrument).
SCPI_SPAN = 64


class ConfigError(ValueError):
    """A setting that cannot be used, with the reason in words."""


@dataclass(frozen=True)
class LlmSettings:
    protocol: str = "openai"
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = field(default=None, repr=False)
    timeout_s: float = 60.0
    context_tokens: int = 32000

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)

    def __repr__(self) -> str:  # never the key, in a log line or a traceback
        return (
            f"LlmSettings(protocol={self.protocol!r}, base_url={self.base_url!r}, model={self.model!r}, "
            f"api_key_set={bool(self.api_key)}, timeout_s={self.timeout_s}, context_tokens={self.context_tokens})"
        )


_FIELDS = {f.name for f in fields(LlmSettings)}


def _coerce(values: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in values.items():
        if key not in _FIELDS or value in (None, ""):
            continue
        if key == "timeout_s":
            value = float(value)
        elif key == "context_tokens":
            value = int(value)
        else:
            value = str(value)
        out[key] = value
    return out


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class AssistantSettings:
    """Reads the defaults and the overrides of one workspace, and saves the overrides."""

    def __init__(self, workspace: Path, env: dict[str, str] | None = None) -> None:
        self.workspace = workspace
        self.env = env if env is not None else os.environ

    def defaults(self) -> LlmSettings:
        block = _read_json(self.workspace / SETTINGS).get("assistant")
        values = _coerce(block if isinstance(block, dict) else {})
        values.update(_coerce({k: self.env.get(v) for k, v in ENV.items()}))
        return LlmSettings(**values)

    def overrides(self) -> dict[str, Any]:
        return _coerce(_read_json(self.workspace / OVERRIDES))

    def current(self) -> LlmSettings:
        return replace(self.defaults(), **self.overrides())

    def view(self) -> M.AssistantConfig:
        s = self.current()
        return M.AssistantConfig(
            protocol=s.protocol if s.protocol in ("openai", "anthropic") else "openai",  # type: ignore[arg-type]
            base_url=s.base_url,
            model=s.model,
            timeout_s=s.timeout_s,
            context_tokens=s.context_tokens,
            api_key_set=bool(s.api_key),
            configured=s.configured,
            overridden=sorted(self.overrides()),
        )

    def update(
        self, req: M.AssistantConfigUpdate, manager: SessionManager | None = None
    ) -> M.AssistantConfig:
        current = {} if req.reset else self.overrides()
        for key in ("protocol", "base_url", "model", "timeout_s", "context_tokens"):
            value = getattr(req, key)
            if value is not None:
                current[key] = value.strip() if isinstance(value, str) else value
                if current[key] == "":
                    current.pop(key)
        if req.api_key is not None:
            if req.api_key == "":
                current.pop("api_key", None)
            else:
                current["api_key"] = req.api_key.strip()
        merged = replace(self.defaults(), **_coerce(current))
        if merged.base_url:
            check_base_url(merged.base_url, manager)
        self._write(current)
        return self.view()

    def _write(self, data: dict[str, Any]) -> None:
        path = self.workspace / OVERRIDES
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        # Created owner-only before the content goes in, so the key is never readable by others.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        with contextlib.suppress(OSError):  # Windows: as far as the platform allows
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, path)


def own_ports(manager: SessionManager | None) -> set[int]:
    """The ports this SimuBench service listens on, or will when a bench starts."""
    if manager is None:
        return set()
    ports = {
        p for p in (manager.web_port, manager.hislip_port, manager.vxi11_port, manager.portmapper_port) if p
    }
    if manager.link_port:
        ports.add(manager.link_port)
    if manager.scpi_base_port:
        ports |= set(range(manager.scpi_base_port, manager.scpi_base_port + SCPI_SPAN))
    for served in manager.sessions.values():
        for lst in served.scpi.listeners if served.scpi else []:
            if lst.port:
                ports.add(lst.port)
    return ports


def _int_part(text: str) -> int | None:
    """One dotted part in the BSD shorthand grammar: decimal, ``0x`` hex, or leading-0 octal."""
    if text[:2] in ("0x", "0X"):
        try:
            return int(text, 16)
        except ValueError:
            return None
    if len(text) > 1 and text[0] == "0":
        try:
            return int(text, 8)
        except ValueError:
            return None
    # isdigit alone is not enough: it is True for superscripts (²) that int() rejects (#914)
    return int(text) if text.isascii() and text.isdigit() else None


def _ipv4_shorthand(host: str) -> str | None:
    """The full IPv4 address a BSD-style shorthand resolves to: ``127.1`` and ``0x7f.1`` are
    127.0.0.1, so is ``2130706433`` — the last part fills the remaining bytes. ``None`` when
    ``host`` is not such an address."""
    parts = host.split(".")
    if not 1 <= len(parts) <= 4:
        return None
    values: list[int] = []
    for part in parts:
        value = _int_part(part)
        if value is None:
            return None
        values.append(value)
    if any(not 0 <= v <= 255 for v in values[:-1]):
        return None
    tail = 4 - (len(values) - 1)
    if values[-1] >= 256**tail:
        return None
    return str(ipaddress.IPv4Address(bytes(values[:-1]) + values[-1].to_bytes(tail, "big")))


def _is_this_computer(host: str) -> bool:
    host = host.rstrip(".").lower().split("%", 1)[0]  # trailing dot, IPv6 zone
    if host in ("localhost", "0.0.0.0", "::", "") or host.endswith(".localhost"):
        return True
    with contextlib.suppress(ValueError):
        addr = ipaddress.ip_address(host)
        if (mapped := getattr(addr, "ipv4_mapped", None)) is not None:
            # an IPv4-mapped IPv6 address means its IPv4 self (the loopback verdict for the mapped
            # form is the mapped address's, whatever the running Python's own shortcuts do)
            addr = mapped
        return addr.is_loopback
    shorthand = _ipv4_shorthand(host)
    return shorthand is not None and ipaddress.ip_address(shorthand).is_loopback


def check_base_url(url: str, manager: SessionManager | None) -> None:
    """Refuse an address the server should not send the conversation to (docs/security.md)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError("LLM 地址只能是 http:// 或 https:// 开头的完整地址")
    if parts.username or parts.password:
        raise ConfigError("LLM 地址里不能写用户名或密码；API key 填在 key 一栏")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise ConfigError("LLM 地址的端口无效") from None
    if _is_this_computer(parts.hostname.strip("[]")) and port in own_ports(manager):
        raise ConfigError(f"LLM 地址指向 SimuBench 自己的端口 {port}；填写 LLM 服务的地址")


def check_settings(settings: LlmSettings, manager: SessionManager | None) -> None:
    """The address rules at use time as well as save time (#901): a default that came from the
    environment or the workspace's ``settings.json`` is checked here too, before anything is sent
    to it."""
    if settings.base_url:
        check_base_url(settings.base_url, manager)
