"""The domain errors the application raises, and the one table that maps them to each seam (#936,
#1382).

A service raises these (never ``HTTPException`` or ``ToolError``). One table (:data:`SEAM_ROWS`)
holds, per kind of domain error, how each seam reports it: an HTTP route maps through
``ApiContext.error_row``, the MCP tools and the assistant's tools through :func:`tool_text`. The
classes live by layer: the engine's (``EditError``, ``PatchError``) stay in the engine, and the
server's all live here; :class:`~..errors.Unknown` and :class:`~..errors.Invalid` sit below every
layer, so the engine raises them too (#1394).

``Forbidden`` and ``NotFound`` subclass the built-in ``PermissionError`` / ``FileNotFoundError``: the
operating system raises the built-ins itself (opening a file that is gone), and both kinds must meet
the same table row. Code that raises one of its own uses the domain name, which says what it means.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import ValidationError

from ..contracts import api as M
from ..engine.edit import EditError
from ..engine.jsonpatch import PatchError
from ..errors import Invalid, Unknown
from ..library.components.files import FileNameError
from ..models.migrate import FormatError
from ..units import UnitError
from ..verdicts import VerdictError


class Forbidden(PermissionError):
    """The operation is not allowed here: a path outside the workspace, a file kind that cannot be removed."""


class NotFound(FileNotFoundError):
    """The thing asked for does not exist: a version, a run record, a file."""


#: Why a write was refused (#1393): the file to create is already there, or the file to change is
#: not the one the caller read.
ConflictReason = Literal["exists", "stale"]
#: Which kind of file: it names the tool an agent reads it again with.
ConflictTarget = Literal["bench", "definition"]


class Conflict(Exception):
    """A write that would lose or clobber something: the file to create already exists
    (``reason="exists"``), or the file changed since it was read (``"stale"``); the caller
    re-reads and retries with the new hash, or picks another name. ``target`` says which kind of
    file, ``None`` when it is not a file an agent reads (a conversation turn)."""

    def __init__(
        self,
        message: str,
        current_sha256: str | None,
        *,
        reason: ConflictReason = "stale",
        target: ConflictTarget | None = None,
    ) -> None:
        super().__init__(message)
        self.current_sha256 = current_sha256
        self.reason = reason
        self.target = target


class BenchInvalid(ValueError):
    """The bench cannot start; ``diagnostics`` say why and where."""

    def __init__(self, diagnostics: list[M.Diagnostic]) -> None:
        first = next(d for d in diagnostics if d.severity == "error")
        super().__init__(f"{first.pointer}: {first.message}")
        self.diagnostics = diagnostics


class Busy(Exception):
    """A turn of this conversation is still running: the next one waits or is refused."""


class ConfigError(ValueError):
    """A setting that cannot be used, with the reason in words. It lives beside the LLM settings
    it describes (``assistant/config.py``); the table row here is what every seam reads."""


class InstrumentRemote(RuntimeError):
    """A UI operation on an instrument a script is connected to (remote, #121)."""

    def __init__(self, instrument: str, clients: list[str]) -> None:
        super().__init__(f"{instrument} is in remote control by {', '.join(clients)}; take it to local first")
        self.instrument = instrument
        self.clients = clients


class ClientsConnected(RuntimeError):
    """An operation that drops or disturbs the scripts connected to the bench, asked without force."""

    def __init__(self, clients: list[str], what: str) -> None:
        super().__init__(f"{what} affects connected scripts ({', '.join(clients)}); confirm with force")
        self.clients = clients


#: The command-line option that moves each service's port (#1195).
PORT_OPTION = {"SCPI": "--scpi-base-port", "HiSLIP": "--hislip-port", "VXI-11": "--vxi11-port"}


class PortInUse(OSError):
    """A port the bench serves on cannot be opened, usually held by another process (#1195): which
    one, and the way out."""

    def __init__(self, service: str, exc: OSError) -> None:
        super().__init__(
            f"无法打开 {service} 端口（通常是被占用），测试台未启动：{exc}。"
            f"可能是另一个 SimuBench（例如 AI 助手自行启动的 simubench mcp）或仪器软件正在使用它；"
            f"关掉它，或以 {PORT_OPTION[service]} <其他端口> 重新启动 SimuBench（SimuBench.exe 也接受这个参数）。"
        )
        self.service = service


#: What reading or writing a workspace file can raise, as a seam catches it.
FILE_ERRORS = (PermissionError, FileNotFoundError)


# ---- the one table, two encodings per kind -------------------------------------------------------


def _first_line(exc: Exception) -> str:
    """The message's first line: empty when the message is empty (an HTTP detail may be empty)."""
    return (str(exc).splitlines() or [""])[0]


def _keyerror_text(exc: Exception) -> str:
    """The message of a ``KeyError`` without the one matched pair of quotes ``str()`` wraps around
    it: ``str(KeyError("no instrument named 'nope'"))`` is ``"no instrument named 'nope'"``, so
    peeling only the outer pair keeps the message's own closing quote (#1456)."""
    msg = str(exc)
    if len(msg) >= 2 and msg[0] == msg[-1] and msg[0] in "'\"":
        return msg[1:-1]
    return msg


def _http_in_use(exc: ClientsConnected | InstrumentRemote, **_: Any) -> HTTPException:
    """409 for an operation the connected scripts stand in the way of (#121)."""
    detail: dict[str, Any] = {"message": str(exc), "clients": exc.clients}
    if isinstance(exc, InstrumentRemote):
        detail.update(reason="remote", instrument=exc.instrument)
    else:
        detail["reason"] = "clients_connected"
    return HTTPException(409, detail)


def _http_port_in_use(exc: Exception, **_: Any) -> HTTPException:
    return HTTPException(409, str(exc))


def _http_busy(exc: Exception, **_: Any) -> HTTPException:
    """409: a turn of this conversation is still running (the assistant's own kind, #1725)."""
    return HTTPException(409, str(exc))


def _http_config(exc: Exception, **_: Any) -> HTTPException:
    """422: the LLM settings cannot be used, in the words the error was raised with."""
    return HTTPException(422, str(exc))


def _http_forbidden_file(exc: Exception, *, files: bool = True, **_: Any) -> HTTPException | None:
    """403; ``files=False`` when the operation reads no file, so a file error is not its to map."""
    return HTTPException(403, str(exc)) if files else None


def _http_missing_file(
    exc: Exception, *, files: bool = True, missing: str | None = None, **_: Any
) -> HTTPException | None:
    """404: the text the route gave (``missing``), else the error's own."""
    return HTTPException(404, missing or str(exc)) if files else None


def _http_conflict(exc: Conflict, **_: Any) -> HTTPException:
    return HTTPException(409, {"message": str(exc), "current_sha256": exc.current_sha256})


def _http_invalid(exc: BenchInvalid, **_: Any) -> HTTPException:
    return HTTPException(422, {"message": str(exc), "diagnostics": [d.model_dump() for d in exc.diagnostics]})


def _http_edit(exc: Exception, **_: Any) -> HTTPException:
    return HTTPException(422, str(exc))


def _http_key(
    exc: Exception, *, key: str = "not_found", missing: str | None = None, **_: Any
) -> HTTPException:
    """A ``KeyError`` goes where it comes from decides: a 404 for a thing asked for, or a 422 for a
    bad option."""
    if key == "not_found":
        return HTTPException(404, missing or _keyerror_text(exc))
    return HTTPException(422, _keyerror_text(exc))


def _http_value(exc: Exception, *, full_detail: bool = False, **_: Any) -> HTTPException:
    return HTTPException(422, str(exc) if full_detail else _first_line(exc))


def _tool_in_use(exc: ClientsConnected | InstrumentRemote, seam: str) -> str:
    tail = "，向工程师说明后以 force=true 重新调用" if seam == "mcp" else "，告诉用户先断开脚本"
    return f"{exc}：有脚本连着端口{tail}"


def _tool_port_in_use(exc: Exception, seam: str) -> str:
    return str(exc)


def _tool_permission(exc: Exception, seam: str) -> str:
    return _first_line(exc)


def _tool_missing_file(exc: FileNotFoundError, seam: str) -> str:
    """The file's name is what the agent needs to recover: never fold it away (#1382)."""
    name = getattr(exc, "filename", None)
    return f"文件不存在：{name or exc}"


#: The tool an agent reads each kind of file with.
_READ_TOOL: dict[str, str] = {"bench": "read_bench", "definition": "read_definition"}


def _tool_conflict(exc: Conflict, seam: str) -> str:
    """The next step follows the reason, the read tool follows the target (#1393): a name already
    taken is not someone else's edit."""
    read = _READ_TOOL.get(exc.target or "")
    sha = f"（当前 sha256 = {exc.current_sha256}）" if exc.current_sha256 else ""
    if exc.reason == "exists":
        look = f"，或先 {read} 读取现有文件再决定是否编辑它" if read else ""
        return f"{exc}；同名文件已存在：换一个名字{look}{sha}"
    again = f"先 {read} 重新读取" if read else "先重新读取"
    return f"{exc}；文件在别处被改过，{again}再编辑{sha}"


def _tool_invalid(exc: BenchInvalid, seam: str) -> str:
    lines = [f"{d.code} {d.pointer}: {d.message}" for d in exc.diagnostics if d.severity == "error"]
    return "测试台未通过校验，无法启动：\n" + "\n".join(lines)


def _tool_first_line(exc: Exception, seam: str) -> str:
    """The first line for an agent: a nameless message is nothing to act on, so the kind's name."""
    return _first_line(exc) or type(exc).__name__


def _tool_patch(exc: Exception, seam: str) -> str:
    return f"补丁没有应用：{exc}"


@dataclass(frozen=True)
class ErrorRow:
    """One kind of domain error and the two seams' encodings of it (#1382).

    ``http`` receives the options a route may pass (``missing``, ``key``, ``full_detail``,
    ``files``) and returns the HTTP error, or ``None`` when the kind is not that route's to map.
    ``tool`` receives the agent seam (``"mcp"`` or ``"assistant"``) and returns the text the agent
    reads. The first row that matches wins for both seams, so a subclass sits above its parent.

    ``caught``: the kinds a seam catches around a service call for this row, when narrower than
    ``kinds``. A bare ``KeyError`` or ``ValueError`` is how a bug looks too, so those rows are
    caught only as the domain's own subclasses (#1394). The table has no catch-all row: an
    exception outside these kinds is a bug and no seam turns it into text — HTTP re-raises it
    (a 500) and the agent seams let it surface as itself (#1478)."""

    kinds: tuple[type[Exception], ...]
    http: Callable[..., HTTPException | None]
    tool: Callable[..., str]
    caught: tuple[type[Exception], ...] | None = None


SEAM_ROWS: tuple[ErrorRow, ...] = (
    ErrorRow((ClientsConnected, InstrumentRemote), _http_in_use, _tool_in_use),
    ErrorRow((PortInUse,), _http_port_in_use, _tool_port_in_use),  # before the file errors: an OSError too
    ErrorRow((Busy,), _http_busy, _tool_first_line),
    # before the ValueError row: the assistant's settings errors are a domain kind of their own (#1725)
    ErrorRow((ConfigError,), _http_config, _tool_first_line),
    ErrorRow((PermissionError,), _http_forbidden_file, _tool_permission),
    ErrorRow((FileNotFoundError,), _http_missing_file, _tool_missing_file),
    ErrorRow((Conflict,), _http_conflict, _tool_conflict),
    ErrorRow((BenchInvalid,), _http_invalid, _tool_invalid),
    ErrorRow((EditError,), _http_edit, _tool_first_line),
    ErrorRow((PatchError,), _http_value, _tool_patch),
    ErrorRow((KeyError,), _http_key, lambda exc, seam: _keyerror_text(exc), caught=(Unknown,)),
    ErrorRow(
        (ValueError, UnicodeDecodeError),
        _http_value,
        _tool_first_line,
        caught=(
            Invalid,
            ValidationError,
            UnitError,
            FileNameError,
            FormatError,
            VerdictError,
            UnicodeDecodeError,
        ),
    ),
)

#: The kinds of domain error a seam catches around one service call: every row, each as narrow as
#: its ``caught``. Anything else is a bug and surfaces as itself: a bare ``KeyError`` or
#: ``ValueError`` too (#1394), and no tool text dresses it up (#1478).
DOMAIN_ERRORS = tuple(
    kind for row in SEAM_ROWS for kind in (row.caught if row.caught is not None else row.kinds)
)


def tool_text(exc: Exception, seam: str) -> str:
    """The text an agent reads for a domain error, from the one table: the MCP tools and the
    assistant's tool results say the same thing about the same kind (the HTTP twin is
    ``ApiContext.error_row``). The agent seams catch each row as narrow as its ``caught``
    (:data:`DOMAIN_ERRORS`), and a non-domain exception — a bare ``KeyError`` or ``ValueError``
    among them (#1394) — is refused here, not given a text a tool would read as a refusal
    (#1478)."""
    for row in SEAM_ROWS:
        if isinstance(exc, row.kinds) and (row.caught is None or isinstance(exc, row.caught)):
            return row.tool(exc, seam)
    raise AssertionError(
        f"{type(exc).__name__} 不是领域错误（DOMAIN_ERRORS）；bug 原样上抛，不得折叠成工具文案 (#1478)"
    ) from exc
