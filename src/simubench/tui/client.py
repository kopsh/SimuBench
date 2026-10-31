"""The TUI's HTTP/WebSocket client: the same ``/api`` endpoints the Web Bench uses (D18).

Every request carries ``X-SimuBench-Client: tui`` so the server records the source as 终端
(tui-screens.md 1.6); the access token goes as the ``x-simubench-token`` header on HTTP and
``?token=`` on the WebSocket. Types come from ``simubench.models.api``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

CLIENT_HEADER = {"X-SimuBench-Client": "tui"}


class ApiError(Exception):
    """A request failed: ``status`` is the HTTP status (401 token, 404 session, …)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class SessionGone(ApiError):
    """The session no longer exists (404): back to the picker with a reason."""


def ws_url(base_url: str, sid: str, token: str | None = None) -> str:
    """The events WebSocket of a session, with the token when one is set."""
    url = base_url.rstrip("/")
    if url.startswith("https://"):
        url = "wss://" + url[len("https://") :]
    elif url.startswith("http://"):
        url = "ws://" + url[len("http://") :]
    out = f"{url}/api/sessions/{sid}/events"
    return f"{out}?token={token}" if token else out


class ApiClient:
    """Async client for one service. ``open`` must be called before use, ``close`` after."""

    def __init__(self, base_url: str, token: str | None = None, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    async def open(self) -> None:
        headers = dict(CLIENT_HEADER)
        if self.token:
            headers["x-simubench-token"] = self.token
        self._http = httpx.AsyncClient(
            base_url=self.base_url, headers=headers, timeout=self._timeout, follow_redirects=False
        )

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    @property
    def http(self) -> httpx.AsyncClient:
        assert self._http is not None, "ApiClient.open() first"
        return self._http

    async def _request(self, method: str, path: str, ok: tuple[int, ...] = (200,), **kwargs: Any) -> Any:
        try:
            r = await self.http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ApiError(0, str(exc)) from exc
        if r.status_code not in ok:
            message = r.text
            try:
                body = r.json()
                message = body.get("message") if isinstance(body, dict) else body
                if isinstance(message, dict) and "message" in message:
                    message = message["message"]
                if not isinstance(message, str):
                    message = r.text
            except ValueError:
                pass
            if r.status_code == 404:
                raise SessionGone(404, message)
            raise ApiError(r.status_code, message)
        if r.status_code == 204:
            return None
        return r.json()

    # ---- sessions -----------------------------------------------------------------------------------

    async def sessions(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/sessions")  # type: ignore[no-any-return]

    async def session(self, sid: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/sessions/{sid}")  # type: ignore[no-any-return]

    async def start_session(
        self, bench_path: str, clock: str | None = None, seed: int | None = None, force: bool = False
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"bench_path": bench_path, "force": force}
        if clock is not None:
            body["clock"] = clock
        if seed is not None:
            body["seed"] = seed
        return await self._request("POST", "/api/sessions", json=body, ok=(200,))

    async def stop_session(self, sid: str, force: bool = False) -> None:
        await self._request(
            "DELETE", f"/api/sessions/{sid}", params={"force": str(force).lower()}, ok=(200, 204)
        )

    async def reset_session(
        self, sid: str, clock: str | None = None, seed: int | None = None, force: bool = False
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"force": force}
        if clock is not None:
            body["clock"] = clock
        if seed is not None:
            body["seed"] = seed
        return await self._request("POST", f"/api/sessions/{sid}/reset", json=body, ok=(200,))

    # ---- reading ------------------------------------------------------------------------------------

    async def instrument(self, sid: str, name: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/sessions/{sid}/instruments/{name}")  # type: ignore[no-any-return]

    async def readings(self, sid: str, name: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/sessions/{sid}/instruments/{name}/readings")  # type: ignore[no-any-return]

    async def levels(self, sid: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/sessions/{sid}/levels")  # type: ignore[no-any-return]

    async def clock(self, sid: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/sessions/{sid}/clock")  # type: ignore[no-any-return]

    async def faults(self, sid: str) -> list[dict[str, Any]]:
        return await self._request("GET", f"/api/sessions/{sid}/faults")  # type: ignore[no-any-return]

    # ---- writing (all carry X-SimuBench-Client: tui) --------------------------------------------------

    async def scpi(self, sid: str, name: str, command: str) -> dict[str, Any]:
        return await self._request(
            "POST", f"/api/sessions/{sid}/instruments/{name}/scpi", json={"command": command}, ok=(200,)
        )

    async def configure(self, sid: str, name: str, path: str, value: Any) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/api/sessions/{sid}/instruments/{name}/configure",
            json={"path": path, "value": value},
            ok=(200,),
        )

    async def panel(self, sid: str, name: str, action: str) -> dict[str, Any]:
        return await self._request(
            "POST", f"/api/sessions/{sid}/instruments/{name}/panel", json={"action": action}, ok=(200,)
        )


async def stream_events(
    url: str,
    on_event: Any,  # callable[[dict], None]
    on_open: Any,  # callable[[], None]
    on_close: Any,  # callable[[int | str], None]
    stop: asyncio.Event,
) -> None:
    """One connection to the events WebSocket, pumped until ``stop`` is set or the socket ends.

    The server replays the full history on every connect (dedupe happens in the store).
    ``on_close`` receives the WebSocket close code when there is one (4401 token invalid,
    4404 session gone), else the error text; the app's reconnect loop decides what to do.
    """
    import websockets

    if stop.is_set():
        return
    reason: int | str = "closed"
    try:
        async with websockets.connect(url, open_timeout=5, close_timeout=2) as ws:
            on_open()
            pump = asyncio.create_task(_pump(ws, on_event))
            stop_wait = asyncio.create_task(stop.wait())
            done, _ = await asyncio.wait({pump, stop_wait}, return_when=asyncio.FIRST_COMPLETED)
            pump.cancel()
            stop_wait.cancel()
            if pump in done and not pump.cancelled() and pump.exception() is not None:
                exc = pump.exception()
                code = getattr(exc, "code", None)  # websockets ConnectionClosed
                reason = code if isinstance(code, int) else (str(exc) or "closed")
            elif stop_wait in done:
                return  # a clean stop: no on_close
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        code = getattr(exc, "code", None)
        reason = code if isinstance(code, int) else (str(exc) or "closed")
    on_close(reason)


async def _pump(ws: Any, on_event: Any) -> None:
    async for raw in ws:
        on_event(json.loads(raw))
