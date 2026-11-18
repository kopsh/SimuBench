"""What every MCP tool module shares: the manager, the file stores, the rate limiter and the
refusals (a tool error the agent can act on)."""

from __future__ import annotations

import functools
import inspect
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel

from ..engine.record import jsonable
from ..server.benchfiles import BenchFiles
from ..server.definitionfiles import DefinitionFiles
from ..server.errors import DOMAIN_ERRORS, tool_text
from ..server.manager import ClientsConnected, ServedSession, SessionManager
from ..server.projections import scpi_payload  # re-exported for the tool modules

__all__ = ["McpContext", "RateLimiter", "scpi_payload", "tool_input"]


class RateLimiter:
    """Token bucket shared by all tools."""

    def __init__(self, rate: float = 20.0, burst: int = 60) -> None:
        self.rate, self.burst = rate, burst
        self.tokens = float(burst)
        self.t = time.monotonic()
        self._lock = threading.Lock()

    def take(self) -> None:
        with self._lock:
            now = time.monotonic()
            self.tokens = min(self.burst, self.tokens + (now - self.t) * self.rate)
            self.t = now
            if self.tokens < 1:
                wait = (1 - self.tokens) / self.rate
                raise ToolError(f"调用过于频繁，请 {wait:.2f} 秒后重试 (rate limited)")
            self.tokens -= 1


def tool_input(model_cls: type[BaseModel]) -> Any:
    """A tool body that takes a shared input model (:mod:`..models.tools`, #1382) from flat
    arguments: the SDK mirrors the model's fields into the signature (so the published schema stays
    flat and comes from the model — a bare model parameter would nest under its name and break
    flat callers), and the body receives the constructed model."""

    def mirror_of(fn: Callable[[Any], Any]) -> inspect.Signature:
        params = []
        for name, f in model_cls.model_fields.items():
            params.append(
                inspect.Parameter(
                    name,
                    kind=inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    default=inspect.Parameter.empty
                    if f.is_required()
                    else f.get_default(call_default_factory=True),
                    # the field's own info (description, limits) travels with it into the schema
                    annotation=f.rebuild_annotation(),
                )
            )
        try:
            # the return annotation decides the SDK's structured output: it must survive the mirror
            # as a real type (the string form does not evaluate against the wrapper's globals)
            returns = inspect.signature(fn, eval_str=True).return_annotation
        except NameError:  # a TYPE_CHECKING-only annotation: the SDK wraps the result instead
            returns = inspect.signature(fn).return_annotation
        return inspect.Signature(params, return_annotation=returns)

    def deco(fn: Callable[[Any], Any]) -> Any:
        mirror = mirror_of(fn)
        wrapper: Any
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def awrapper(**kwargs: Any) -> Any:
                return await fn(model_cls(**kwargs))

            wrapper = cast(Any, awrapper)
        else:

            @functools.wraps(fn)
            def swrapper(**kwargs: Any) -> Any:
                return fn(model_cls(**kwargs))

            wrapper = cast(Any, swrapper)

        wrapper.__signature__ = mirror  # the SDK reads the signature, not the source
        return wrapper

    return deco


@dataclass
class McpContext:
    manager: SessionManager
    allow_control: bool
    ui_url: str | None
    limiter: RateLimiter

    @property
    def files(self) -> BenchFiles:
        return self.manager.files

    @property
    def definitions(self) -> DefinitionFiles:
        return DefinitionFiles(self.manager.files)

    def guard(self) -> None:
        self.limiter.take()

    def fail(self, exc: Exception) -> ToolError:
        """The tool error of a domain error: the tool column of the one table
        (:mod:`..server.errors`, #936, #1382), the same rows the HTTP routes and the assistant read.

        The calling ``except`` is :data:`DOMAIN_ERRORS`: a bug handed here is refused — it must
        surface as itself (the SDK reports it, like the HTTP 500 it is), never dressed as a tool
        text an agent would read as a refusal (#1478)."""
        if not isinstance(exc, DOMAIN_ERRORS):
            raise AssertionError(
                f"{type(exc).__name__} 不是领域错误；只捕 DOMAIN_ERRORS，bug 原样上抛 (#1478)"
            ) from exc
        return ToolError(tool_text(exc, "mcp"))

    def force_needed(self, exc: ClientsConnected) -> ToolError:
        """A whole-bench operation the connected scripts stand in the way of (#121, issue 624):
        the same refusal the HTTP API answers 409 with, as a tool error the agent can retry from."""
        return ToolError(tool_text(exc, "mcp"))

    def served(self, session_id: str | None) -> ServedSession:
        manager = self.manager
        if session_id:
            try:
                return manager.get(session_id)
            except KeyError:
                raise ToolError(f"没有会话 {session_id!r}；用 list_sessions 查看") from None
        if len(manager.sessions) == 1:
            return next(iter(manager.sessions.values()))
        if not manager.sessions:
            raise ToolError("没有运行中的会话；先用 start_session 部署测试台")
        raise ToolError("有多个会话，请指定 session_id")

    def control(self) -> None:
        if not self.allow_control:
            raise ToolError("仿真控制默认关闭；工程师需以 `simubench mcp --allow-control` 启动服务")

    def definition_out(self, doc: Any) -> dict[str, Any]:
        doc.data = jsonable(doc.data)  # JSON has no infinity: "INF", as bench files spell it
        doc.sessions_to_restart = self.manager.sessions_using_stale_definition(doc.id)
        return doc.model_dump(mode="json")

    def session_summary(self, s: ServedSession, info: Any = None) -> dict[str, Any]:
        # `info` when the caller already described the session: describe re-projects every
        # instrument's state, deviations and nets — not something to do twice (issue 641).
        info = info or self.manager.views.describe(s)
        rel = info.bench_path
        out: dict[str, Any] = {
            "session_id": info.id,
            "bench": info.bench_name,
            "bench_path": rel,
            "clock": info.clock_mode,
            "now_ns": info.now_ns,
            "instruments": [
                {
                    "name": i.name,
                    "model": i.model,
                    "idn": i.idn,
                    "resource_virtual": i.address_virtual,
                    "resource_real": i.address_real,
                    "resource_hislip": i.address_hislip,
                    "resource_vxi11": i.address_vxi11,
                    "scpi_socket": f"TCPIP0::127.0.0.1::{i.scpi_port}::SOCKET" if i.scpi_port else None,
                    # the hardware options this unit has (D68), for a model that declares any
                    **({"options": [o.id for o in i.options if o.installed]} if i.options else {}),
                }
                for i in info.instruments
            ],
            "switch_script": (
                "In-process (CI): rm = pyvisa.ResourceManager('<bench file>@simubench'); the script's "
                "real addresses and the instance names both open the virtual instruments. "
                "Or keep the default backend and use the scpi_socket addresses of this session."
            ),
        }
        if self.ui_url:
            out["web_bench"] = self.ui_url
        return out
