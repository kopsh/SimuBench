"""The application services (#894) called directly: no HTTP app, no MCP server."""

from __future__ import annotations

import asyncio
import threading

import pytest

from simubench.models.bench import Fault
from simubench.server.manager import ServedSession
from simubench.server.services import clock, faults
from test_passive_view import open_view


def _held(s, monkeypatch):
    """Record, per call of the DTO conversion, whether the session lock is held (#480, #489)."""
    real = threading.RLock()
    depth = {"n": 0}
    held: list[bool] = []

    class Tracked:
        def __enter__(self):
            real.acquire()
            depth["n"] += 1
            return self

        def __exit__(self, *exc):
            depth["n"] -= 1
            real.release()
            return False

    real_info = faults.fault_info

    def info(f):
        held.append(depth["n"] > 0)
        return real_info(f)

    monkeypatch.setattr(s, "_lock", Tracked())
    monkeypatch.setattr(faults, "fault_info", info)
    return held


def test_every_fault_report_is_converted_inside_the_view(tmp_path, monkeypatch):
    s = open_view(tmp_path)
    sv = ServedSession("t", s, None)
    held = _held(s, monkeypatch)
    spec = Fault(kind="latency", instrument="lia", latency_s=0.1, at_s=0.05)
    injected = faults.inject(sv, spec, source="t")
    assert [f.id for f in faults.list_faults(sv)] == [injected.id]
    cleared = faults.clear(sv, injected.id)
    assert cleared.id == injected.id
    assert held == [True, True, True]


def test_a_missing_fault_raises_the_domain_error(tmp_path):
    sv = ServedSession("t", open_view(tmp_path), None)
    with pytest.raises(KeyError):
        faults.clear(sv, "nope")


def test_the_clock_service_reports_the_clock_after_the_move(tmp_path):
    sv = ServedSession("t", open_view(tmp_path), None)
    before = clock.info(sv).now_ns
    after = clock.advance(sv, 1.0)
    assert after.now_ns - before == 1_000_000_000
    assert after.mode == clock.info(sv).mode


def _ctx(tmp_path):
    from simubench.server.manager import SessionManager
    from simubench.server.routes.context import ApiContext

    return ApiContext.of(SessionManager(tmp_path, scpi_base_port=None, hislip_port=None), None)


def _status(ctx, exc, **options):
    err = ctx.error_row(exc, **options)
    return None if err is None else err.status_code


def test_the_http_error_table_maps_each_domain_error_once(tmp_path):
    from simubench.models import api as M
    from simubench.server.errors import BenchInvalid, ClientsConnected, Conflict, Forbidden, NotFound

    ctx = _ctx(tmp_path)
    diag = M.Diagnostic(severity="error", code="E-X", pointer="/p", message="m")
    assert _status(ctx, ClientsConnected(["a"], "stop")) == 409
    assert _status(ctx, Forbidden("x")) == 403 and _status(ctx, PermissionError("x")) == 403
    assert _status(ctx, NotFound("x")) == 404 and _status(ctx, FileNotFoundError("x")) == 404
    assert _status(ctx, Conflict("changed", "abc")) == 409
    assert _status(ctx, BenchInvalid([diag])) == 422
    assert _status(ctx, ValueError("a\nb")) == 422
    assert _status(ctx, RuntimeError("boom")) is None


def test_a_missing_key_is_a_404_or_a_422_by_where_it_comes_from(tmp_path):
    ctx = _ctx(tmp_path)
    assert _status(ctx, KeyError("k")) == 404  # an unknown session or file asked for
    assert _status(ctx, KeyError("k"), key="unprocessable") == 422  # a bad option of a start / reset


def test_the_error_row_keeps_the_text_each_route_gave(tmp_path):
    ctx = _ctx(tmp_path)
    assert (
        ctx.error_row(FileNotFoundError("run-1"), missing="no such run record").detail == "no such run record"
    )
    assert ctx.error_row(FileNotFoundError("run-1")).detail == "run-1"
    assert ctx.error_row(ValueError("first\nsecond")).detail == "first"
    assert ctx.error_row(ValueError("first\nsecond"), full_detail=True).detail == "first\nsecond"
    assert ctx.error_row(ValueError("")).detail == ""  # no first line to take: no IndexError


def test_a_reset_reads_no_file_so_it_maps_no_file_errors(tmp_path):
    ctx = _ctx(tmp_path)
    assert ctx.error_row(PermissionError("x"), files=False) is None
    assert ctx.error_row(FileNotFoundError("x"), files=False) is None


def test_http_error_lets_a_bug_surface_as_it_is(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(RuntimeError):
        try:
            raise RuntimeError("boom")
        except RuntimeError as exc:
            ctx.http_error(exc)


def test_the_mcp_table_has_a_row_for_the_same_domain_errors(tmp_path):
    pytest.importorskip("mcp")  # the optional extra (#987); the rest of the file does not need it
    from simubench.mcp_tools.context import McpContext, RateLimiter
    from simubench.server.errors import Conflict
    from simubench.server.manager import SessionManager

    ctx = McpContext(
        SessionManager(tmp_path, scpi_base_port=None, hislip_port=None), True, None, RateLimiter()
    )
    assert "sha256 = abc" in str(ctx.fail(Conflict("changed", "abc")))
    assert str(ctx.fail(FileNotFoundError("run-1"))) == "文件不存在：run-1"
    named = FileNotFoundError("no such file")  # an operating-system error carries the path itself
    named.filename = "a.yaml"
    assert str(ctx.fail(named)) == "文件不存在：a.yaml"
    assert str(ctx.fail(KeyError("no such thing"))) == "no such thing"
    assert str(ctx.fail(ValueError("first\nsecond"))) == "first"


def test_the_agent_texts_come_from_one_table():
    """#1382: one table, three seams. The kinds whose wording the two agent seams share say exactly
    the same thing; where the next step differs (scripts connected), the seam says its own."""
    from simubench.engine.edit import EditError
    from simubench.engine.jsonpatch import PatchError
    from simubench.models import api as M
    from simubench.server.errors import BenchInvalid, ClientsConnected, Conflict, tool_text

    shared = [
        Conflict("agent.bench.yaml changed since it was read", "abc"),
        BenchInvalid([M.Diagnostic(severity="error", code="E-X", pointer="/p", message="m")]),
        FileNotFoundError("run-1"),
        KeyError("no session t"),
        EditError("no such instrument ghost"),
        PatchError("the patch does not apply"),
    ]
    for exc in shared:
        assert tool_text(exc, "mcp") == tool_text(exc, "assistant"), type(exc).__name__
    assert "force=true" in tool_text(ClientsConnected(["s"], "stop"), "mcp")
    assert "先断开脚本" in tool_text(ClientsConnected(["s"], "stop"), "assistant")


def test_the_tool_text_and_the_http_row_agree_per_kind(tmp_path):
    """The table's two columns encode one policy per kind: every domain kind gets a tool text for
    both agent seams, and the text carries what the HTTP row's status is about (the missing file's
    name, the conflict's sha, the invalid bench's pointer)."""
    from simubench.models import api as M
    from simubench.server.errors import BenchInvalid, Conflict, tool_text

    ctx = _ctx(tmp_path)
    diag = M.Diagnostic(severity="error", code="E-X", pointer="/p", message="m")
    cases = [
        (Conflict("x changed since it was read", "abc"), "abc", 409),
        (FileNotFoundError("a.bench.yaml"), "a.bench.yaml", 404),
        (BenchInvalid([diag]), "/p", 422),
    ]
    for exc, needle, status in cases:
        assert _status(ctx, exc) == status
        for seam in ("mcp", "assistant"):
            assert needle in tool_text(exc, seam), (type(exc).__name__, seam)


# ---- starting, resetting and stopping, without a route or a tool --------------------------------


def test_stopping_an_unknown_session_is_a_key_error_not_a_silent_success(tmp_path):
    from simubench.server.manager import SessionManager
    from simubench.server.services import sessions as sessions_service

    manager = SessionManager(tmp_path, scpi_base_port=None, hislip_port=None)
    with pytest.raises(KeyError):
        asyncio.run(sessions_service.stop(manager, "nope"))


def test_starting_a_bench_outside_the_workspace_is_a_domain_error(tmp_path):
    from simubench.server.manager import SessionManager
    from simubench.server.services import sessions as sessions_service

    manager = SessionManager(tmp_path, scpi_base_port=None, hislip_port=None)
    with pytest.raises((PermissionError, FileNotFoundError)):
        asyncio.run(sessions_service.start(manager, "../outside.bench.yaml"))
