"""The event sockets: the session's stream (the backlog, then every new event as it happens) and
the workspace's (``file_changed`` and ``clients_changed``, issue #1484)."""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from typing import Any, cast

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ...engine.events import project
from ..workspace_events import WorkspaceEvent, put_bounded
from .context import ApiContext, host_ok


def build_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()

    @router.websocket("/api/sessions/{sid}/events")
    async def events(ws: WebSocket, sid: str, since: int = 0) -> None:
        """The session's stream. ``seq`` is the event's stable cursor in the session's history
        (the engine's ``eid`` — the same event carries the same seq on every connect), so a
        reconnecting client passes its last one as ``since`` and is sent only the events after
        it (#1632); the default 0 replays the whole history, as before."""
        if not host_ok(ws.headers.get("host")) or not host_ok(ws.headers.get("origin")):
            await ws.close(code=4403)
            return
        supplied = ws.query_params.get("token") or ""
        if ctx.token is not None and not secrets.compare_digest(supplied, ctx.token):
            await ws.close(code=4401)
            return
        try:
            s = ctx.manager.get(sid)
        except KeyError:
            await ws.close(code=4404)
            return
        await ws.accept()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=10000)
        fallback = {"seq": 0}

        def cursor(ev: dict[str, Any]) -> int:
            """The event's stream seq: the engine's eid, which every engine event carries; the
            per-connection count only stands in for an event that has none."""
            if "eid" in ev:
                return int(ev["eid"])
            fallback["seq"] += 1
            return fallback["seq"]

        def push(ev: dict[str, Any]) -> None:
            cur = cursor(ev)
            if cur <= since:
                return  # the client already holds this one and asked for the tail only
            msg = project(ev, cur)
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(put_bounded, queue, msg)

        with s.session.view():  # backlog and subscription without a gap or a duplicate
            backlog = list(s.session.events.history)
            unsubscribe = s.session.events.subscribe(push)
        for ev in backlog:
            cur = cursor(ev)
            if cur <= since:
                continue
            await ws.send_json(project(ev, cur))

        async def wait_disconnect() -> None:
            """Return only when the client goes away; non-disconnect frames are ignored."""
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    raise WebSocketDisconnect(cast(int, message.get("code")))

        listener = asyncio.create_task(wait_disconnect())
        try:
            while True:
                get = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait({get, listener}, return_when=asyncio.FIRST_COMPLETED)
                if listener in done:
                    get.cancel()
                    listener.result()  # re-raise the disconnect; a non-disconnect frame continues below
                    listener = asyncio.create_task(wait_disconnect())
                    continue
                await ws.send_json(get.result())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            listener.cancel()
            unsubscribe()

    @router.websocket("/api/events")
    async def workspace_events(ws: WebSocket) -> None:
        """The workspace's stream (issue #1484): file versions and access-port clients, what no
        session owns. No backlog: the Web re-reads, on its first open, what it read before the socket
        listened, and everything on a reconnect (#1526)."""
        if not host_ok(ws.headers.get("host")) or not host_ok(ws.headers.get("origin")):
            await ws.close(code=4403)
            return
        supplied = ws.query_params.get("token") or ""
        if ctx.token is not None and not secrets.compare_digest(supplied, ctx.token):
            await ws.close(code=4401)
            return
        # subscribe before the handshake completes: once the client sees the socket open, every
        # later write reaches it (#1526); the Web re-reads what it read before that.
        queue = ctx.manager.events.subscribe(asyncio.get_running_loop())
        try:
            await ws.accept()
        except BaseException:
            ctx.manager.events.unsubscribe(queue)
            raise
        counter = {"seq": 0}

        async def wait_disconnect() -> None:
            """Return only when the client goes away; non-disconnect frames are ignored."""
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    raise WebSocketDisconnect(cast(int, message.get("code")))

        listener = asyncio.create_task(wait_disconnect())
        try:
            while True:
                get = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait({get, listener}, return_when=asyncio.FIRST_COMPLETED)
                if listener in done:
                    get.cancel()
                    listener.result()  # re-raise the disconnect; a non-disconnect frame continues below
                    listener = asyncio.create_task(wait_disconnect())
                    continue
                counter["seq"] += 1
                await ws.send_json(
                    WorkspaceEvent.model_validate({"seq": counter["seq"], **get.result()}).model_dump(
                        exclude_none=True
                    )
                )
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            listener.cancel()
            ctx.manager.events.unsubscribe(queue)

    return router
