"""Operations: structured logs, the version manifest, health checks and the diagnostics bundle (M4).

Nothing here leaves the machine: the bundle is a zip the user hands over themselves (D17). It never
contains the access token, and bench files are included only for sessions that are running.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import importlib.metadata
import io
import json
import logging
import platform
import socket
import sys
import tempfile
import zipfile
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import __version__

if TYPE_CHECKING:
    from .server.manager import SessionManager

RING_SIZE = 2000
DEPENDENCIES = ("numpy", "scipy", "pydantic", "pyyaml", "pyvisa", "fastapi", "uvicorn", "mcp")


# ---- structured logs ---------------------------------------------------------------------------------


class JsonFormatter(logging.Formatter):
    """One JSON object per line: time (UTC), level, logger, message, and any ``extra`` fields."""

    _std = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": dt.datetime.fromtimestamp(record.created, dt.UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k not in self._std and not k.startswith("_"):
                out[k] = v if isinstance(v, (str, int, float, bool, type(None))) else repr(v)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False)


class RingHandler(logging.Handler):
    """Keeps the latest log records in memory for the diagnostics bundle."""

    def __init__(self, size: int = RING_SIZE) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: deque[str] = deque(maxlen=size)
        self.setFormatter(JsonFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append(self.format(record))
        except Exception:  # a log record must never break the service
            self.handleError(record)


RING = RingHandler()


def setup_logging(level: str = "warning", fmt: str = "text", file: str | None = None) -> None:
    """Configure the root logger: text or JSON on stderr (and a file), plus the in-memory ring."""
    root = logging.getLogger()
    root.setLevel(min(logging.INFO, logging.getLevelName(level.upper())))
    for h in list(root.handlers):
        if not isinstance(h, RingHandler):
            root.removeHandler(h)
    formatter: logging.Formatter = (
        JsonFormatter()
        if fmt == "json"
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if file:
        handlers.append(logging.FileHandler(file, encoding="utf-8"))
    for h in handlers:
        h.setLevel(logging.getLevelName(level.upper()))
        h.setFormatter(formatter)
        root.addHandler(h)
    if RING not in root.handlers:
        RING.setLevel(logging.INFO)  # the ring keeps info and above whatever is printed
        root.addHandler(RING)


# ---- version manifest ----------------------------------------------------------------------------------


def _dist_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def manifest(workspace: Path | None = None, record_dir: Path | None = None) -> dict[str, Any]:
    """Everything that decides how this installation behaves, for bug reports and run comparisons."""
    from .engine.component import component_types
    from .engine.record import definition_hash
    from .library.loader import DefinitionLibrary
    from .server.app import WEB_DIST

    lib = DefinitionLibrary([workspace / "instruments"] if workspace else [])
    definitions = []
    for def_id in lib.ids():
        try:
            d = lib.get(def_id)
            definitions.append({"id": d.id, "version": d.version, "sha256": definition_hash(d)})
        except Exception as exc:
            definitions.append({"id": def_id, "error": str(exc).splitlines()[0]})
    index = WEB_DIST / "index.html"
    return {
        "simubench": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "dependencies": {name: _dist_version(name) for name in DEPENDENCIES},
        "web_ui": {
            "present": index.exists(),
            "sha256": hashlib.sha256(index.read_bytes()).hexdigest() if index.exists() else None,
        },
        "definitions": definitions,
        "component_types": sorted(component_types()),
        "workspace": str(workspace) if workspace else None,
        "record_dir": str(record_dir) if record_dir else None,
    }


# ---- health checks -------------------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail
    detail: str = ""


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def doctor(
    workspace: Path | None = None, port: int | None = None, scpi_base_port: int | None = None
) -> list[Check]:
    """Checks that SimuBench can run here; each says what is wrong and how to fix it."""
    from .server.app import WEB_DIST

    checks: list[Check] = []
    v = sys.version_info
    checks.append(
        Check(
            "python", "ok" if v >= (3, 11) else "fail", f"{platform.python_version()} (3.11 or later needed)"
        )
    )
    missing = [n for n in DEPENDENCIES if n != "mcp" and _dist_version(n) is None]
    checks.append(Check("dependencies", "fail" if missing else "ok", ", ".join(missing) or "all installed"))
    checks.append(
        Check(
            "mcp",
            "ok" if _dist_version("mcp") else "warn",
            "installed" if _dist_version("mcp") else "not installed: `uv sync --extra mcp` for AI agents",
        )
    )
    try:
        from pyvisa import highlevel

        highlevel.get_wrapper_class("simubench")
        checks.append(Check("pyvisa backend", "ok", "@simubench registered"))
    except Exception as exc:
        checks.append(Check("pyvisa backend", "fail", f"{type(exc).__name__}: {exc}"))

    from .library.loader import DefinitionLibrary

    lib = DefinitionLibrary([workspace / "instruments"] if workspace else [])
    broken = []
    for def_id in lib.ids():
        try:
            lib.get(def_id)
        except Exception as exc:
            broken.append(f"{def_id}: {str(exc).splitlines()[0]}")
    checks.append(
        Check(
            "instrument definitions",
            "fail" if broken else "ok",
            "; ".join(broken) or f"{len(lib.ids())} load",
        )
    )
    checks.append(
        Check(
            "web ui",
            "ok" if (WEB_DIST / "index.html").exists() else "warn",
            str(WEB_DIST)
            if (WEB_DIST / "index.html").exists()
            else "not built: `pnpm -C web build` (API still works)",
        )
    )
    if workspace is not None:
        checks.extend(_workspace_checks(workspace))
    for label, p in (("web port", port), ("scpi port", scpi_base_port)):
        if p:
            free = _port_free(p)
            checks.append(
                Check(
                    label,
                    "ok" if free else "warn",
                    f"{p} {'free' if free else 'in use: the next free one is used'}",
                )
            )
    try:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "x").write_text("x")
        checks.append(Check("temp folder", "ok", tempfile.gettempdir()))
    except OSError as exc:
        checks.append(Check("temp folder", "fail", str(exc)))
    return checks


def _workspace_checks(workspace: Path) -> list[Check]:
    from .engine.validate import validate_data
    from .library.loader import DefinitionLibrary, load_yaml

    if not workspace.is_dir():
        return [Check("workspace", "fail", f"{workspace} does not exist")]
    out = [Check("workspace", "ok", str(workspace))]
    lib = DefinitionLibrary([workspace / "instruments"])
    bad = []
    count = 0
    for path in sorted(workspace.rglob("*.bench.yaml")):
        if ".simubench" in path.parts:
            continue
        count += 1
        try:
            _, report = validate_data(load_yaml(path.read_text(encoding="utf-8")), lib)
            if not report.ok:
                bad.append(f"{path.relative_to(workspace).as_posix()}: {report.diagnostics[0].code}")
        except Exception as exc:
            bad.append(f"{path.relative_to(workspace).as_posix()}: {str(exc).splitlines()[0]}")
    out.append(Check("bench files", "warn" if bad else "ok", "; ".join(bad) or f"{count} valid"))
    return out


# ---- diagnostics bundle --------------------------------------------------------------------------------


def bundle(
    manager: SessionManager | None,
    workspace: Path | None,
    *,
    record_dir: Path | None = None,
    runs: int = 3,
    max_run_bytes: int = 5_000_000,
) -> bytes:
    """A zip for a bug report: manifest, health checks, recent logs, running sessions with their
    bench files and recent errors, and the latest run records (each up to ``max_run_bytes``)."""
    buf = io.BytesIO()
    created = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:

        def put(name: str, data: Any) -> None:
            text = (
                data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2, default=str)
            )
            z.writestr(name, text)

        put(
            "README.txt",
            f"SimuBench diagnostics bundle, created {created} (UTC).\nNo access token is included.\n",
        )
        put("manifest.json", manifest(workspace, record_dir))
        put("doctor.json", [asdict(c) for c in doctor(workspace)])
        put("logs.jsonl", "\n".join(RING.records) + ("\n" if RING.records else ""))
        if manager is not None:
            sessions = []
            for sid, served in list(manager.sessions.items()):
                s = served.session
                errors = [e for e in list(s.history) if e.get("kind") in ("error", "fault")][-200:]
                sessions.append(
                    {
                        "id": sid,
                        "bench": s.bench.name,
                        "bench_path": str(served.bench_path) if served.bench_path else None,
                        "clock": s.clock_mode,
                        "now_ns": s.now_ns,
                        "seed": s.seed,
                        "record": str(s.recorder.path) if s.recorder else None,
                        "recent_errors_and_faults": errors,
                    }
                )
                if served.bench_path:
                    with contextlib.suppress(OSError):  # the file may have been moved since
                        z.writestr(f"benches/{sid}-{served.bench_path.name}", served.bench_path.read_bytes())
            put("sessions.json", sessions)
        if record_dir and record_dir.is_dir():
            recent = sorted(record_dir.glob("run-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[
                :runs
            ]
            for p in recent:
                data = p.read_bytes()
                if len(data) > max_run_bytes:
                    data = data[:max_run_bytes].rsplit(b"\n", 1)[0] + b"\n"
                z.writestr(f"runs/{p.name}", data)
    return buf.getvalue()
