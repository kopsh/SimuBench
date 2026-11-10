"""What every MCP tool module shares: the manager, the file stores, the rate limiter and the
refusals (a tool error the agent can act on)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from ..engine.record import jsonable
from ..models import api as M
from ..server.benchfiles import BenchFiles, Conflict
from ..server.definitionfiles import DefinitionFiles
from ..server.drive import scpi_response
from ..server.manager import (
    BenchInvalid,
    ClientsConnected,
    ServedSession,
    SessionManager,
)


def scpi_payload(s: Any, instrument: str, command: str, client: str) -> dict[str, Any]:
    """The same SCPI-exchange shape the web API returns (scpi_response in server.manager): one
    DTO, two seams (the tools used to carry their own, with seconds instead of ns)."""
    return scpi_response(s, instrument, command, client).model_dump(mode="json")


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
        if isinstance(exc, Conflict):
            again = "read_definition" if "instruments/" in str(exc) else "read_bench"
            return ToolError(f"{exc}；当前 sha256 = {exc.current_sha256}，请先 {again} 再编辑")
        if isinstance(exc, BenchInvalid):
            lines = [f"{d.code} {d.pointer}: {d.message}" for d in exc.diagnostics if d.severity == "error"]
            return ToolError("测试台未通过校验，无法启动：\n" + "\n".join(lines))
        if isinstance(exc, FileNotFoundError):
            return ToolError("文件不存在")
        if isinstance(exc, KeyError):
            return ToolError(str(exc).strip("'\""))
        return ToolError(str(exc).splitlines()[0] if str(exc) else type(exc).__name__)

    def force_needed(self, exc: ClientsConnected) -> ToolError:
        """A whole-bench operation the connected scripts stand in the way of (#121, issue 624):
        the same refusal the HTTP API answers 409 with, as a tool error the agent can retry from."""
        return ToolError(f"{exc}；向工程师说明后，以 force=true 重新调用")

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
        doc.sessions_to_restart = [
            sid for sid, sv in self.manager.sessions.items() if doc.id in self.manager.stale_definitions(sv)
        ]
        return doc.model_dump(mode="json")

    def session_summary(self, s: ServedSession, info: M.SessionInfo | None = None) -> dict[str, Any]:
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
