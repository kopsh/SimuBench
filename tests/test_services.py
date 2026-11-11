"""The application services (#894) called directly: no HTTP app, no MCP server."""

from __future__ import annotations

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


def test_the_lifecycle_error_table_maps_each_domain_error_once(tmp_path):
    from simubench.models import api as M
    from simubench.server.manager import BenchInvalid, ClientsConnected

    ctx = _ctx(tmp_path)
    status = lambda exc, **kw: getattr(ctx.lifecycle_error(exc, **kw), "status_code", None)  # noqa: E731
    assert status(ClientsConnected(["a"], "stop")) == 409
    assert status(PermissionError("x")) == 403
    assert status(FileNotFoundError("x")) == 404
    diag = M.Diagnostic(severity="error", code="E-X", pointer="/p", message="m")
    assert status(BenchInvalid([diag])) == 422
    assert status(ValueError("a\nb")) == 422 and status(KeyError("k")) == 422
    assert ctx.lifecycle_error(RuntimeError("boom")) is None


def test_a_reset_does_not_read_a_file_so_it_maps_no_file_errors(tmp_path):
    ctx = _ctx(tmp_path)
    assert ctx.lifecycle_error(PermissionError("x"), files=False) is None
    assert ctx.lifecycle_error(FileNotFoundError("x"), files=False) is None
