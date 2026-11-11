"""Starting, resetting and stopping a session, as both seams ask for them (#936).

The clients guard lives in the manager: starting a bench stops the running one, resetting drops its
state, stopping closes the sockets, and each refuses while scripts are connected unless forced — for
whichever seam asked (#121, issue 624). What a service adds is the contract the two seams share:
the errors it raises are domain errors only —

* ``ClientsConnected`` — scripts are connected and ``force`` is off;
* ``Forbidden`` / ``NotFound`` (``PermissionError`` / ``FileNotFoundError``) — a bench path outside the
  workspace, a file that is not there;
* ``BenchInvalid`` — the bench does not validate (its diagnostics say why);
* ``ValueError`` / ``KeyError`` — a bad option, an unknown session.

— and each seam maps them with its own table (``ApiContext.error_row``, ``McpContext.fail``).
"""

from __future__ import annotations

from ..manager import ServedSession, SessionManager


async def start(
    manager: SessionManager,
    bench_path: str,
    *,
    clock: str | None = None,
    seed: int | None = None,
    force: bool = False,
    time_scale: float | None = None,
) -> ServedSession:
    return await manager.start(bench_path, clock=clock, seed=seed, force=force, time_scale=time_scale)  # type: ignore[arg-type]


async def reset(
    manager: SessionManager,
    session_id: str,
    *,
    clock: str | None = None,
    seed: int | None = None,
    force: bool = False,
) -> ServedSession:
    return await manager.reset(session_id, clock=clock, seed=seed, force=force)  # type: ignore[arg-type]


async def stop(manager: SessionManager, session_id: str, *, force: bool = False) -> None:
    """Stop a session. An unknown id raises ``KeyError``: the manager itself returns silently for
    one, and a seam must answer "no such session", not success."""
    manager.get(session_id)
    await manager.stop(session_id, force=force)
