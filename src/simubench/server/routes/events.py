"""The event sockets: the session's stream (the backlog, then every new event as it happens) and
the workspace's (``file_changed`` and ``clients_changed``, issue #1484)."""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from typing import Any, cast

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ...engine.events import project
from ..workspace_events import WorkspaceEvent
from .context import ApiContext, host_ok


def build_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()

    @router.websocket("/api/sessions/{sid}/events")
    async def events(ws: WebSocket, sid: str) -> None:
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
        counter = {"seq": 0}

        def push(ev: dict[str, Any]) -> None:
            counter["seq"] += 1
            msg = project(ev, counter["seq"])
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(_put, queue, msg)

        with s.session.view():  # backlog and subscription without a gap or a duplicate
            backlog = list(s.session.history)
            unsubscribe = s.session.subscribe(push)
        for ev in backlog:
            counter["seq"] += 1
            await ws.send_json(project(ev, counter["seq"]))

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
        session owns. No backlog: the Web re-reads its queries on subscribe and on reconnect."""
        if not host_ok(ws.headers.get("host")) or not host_ok(ws.headers.get("origin")):
            await ws.close(code=4403)
            return
        supplied = ws.query_params.get("token") or ""
        if ctx.token is not None and not secrets.compare_digest(supplied, ctx.token):
            await ws.close(code=4401)
            return
        await ws.accept()
        loop = asyncio.get_running_loop()
        queue = ctx.manager.events.subscribe(loop)
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


def _put(queue: asyncio.Queue[dict[str, Any]], msg: dict[str, Any]) -> None:
    if queue.full():
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()  # drop the oldest rather than block the bench
    queue.put_nowait(msg)
