"""The AI assistant (#871, D66): LLM settings, the conversation of a bench, its events, stop,
clear, and restore to before the last turn."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from ...assistant.config import ConfigError, check_settings
from ...assistant.llm import check_connection
from ...assistant.service import Busy
from ...models import api as M
from ..benchfiles import Conflict
from ..errors import FILE_ERRORS
from ..manager import ClientsConnected
from .context import ApiContext

_BUSY: dict[int | str, dict[str, Any]] = {409: {"description": "A turn of this conversation is running"}}


def http_error(ctx: ApiContext, exc: Exception) -> HTTPException:
    """The assistant's own errors (a turn already running, a settings problem); the rest is the
    shared table of ``ApiContext.http_error``."""
    if isinstance(exc, Busy):
        return HTTPException(409, str(exc))
    if isinstance(exc, ConfigError):
        return HTTPException(422, str(exc))
    return ctx.http_error(exc)


def bench_errors(ctx: ApiContext, fn: Any) -> Any:
    try:
        return fn()
    except (*FILE_ERRORS, Busy, ConfigError, KeyError) as exc:
        raise http_error(ctx, exc) from None


def _assistant_chat_routes(ctx: ApiContext, router: APIRouter) -> None:
    auth = ctx.auth
    service = ctx.assistant

    @router.get(
        "/api/assistant/config",
        response_model=M.AssistantConfig,
        operation_id="getAssistantConfig",
        dependencies=[Depends(auth)],
    )
    def get_config() -> M.AssistantConfig:
        """The LLM settings; the key is never returned, only whether one is set."""
        return service.settings.view()

    @router.put(
        "/api/assistant/config",
        response_model=M.AssistantConfig,
        operation_id="saveAssistantConfig",
        dependencies=[Depends(auth)],
    )
    def save_config(req: M.AssistantConfigUpdate) -> M.AssistantConfig:
        """Override the server's defaults (saved in the workspace's .simubench/, owner-only)."""
        return bench_errors(ctx, lambda: service.settings.update(req, ctx.manager))  # type: ignore[no-any-return]

    @router.post(
        "/api/assistant/config/test",
        response_model=M.AssistantTestResult,
        operation_id="testAssistantConfig",
        dependencies=[Depends(auth)],
    )
    async def test_config() -> M.AssistantTestResult:
        """One short request to the configured LLM, with a tool call: does it answer, can it call tools."""
        try:
            check_settings(service.settings.current(), ctx.manager)
        except ConfigError as exc:  # a bad default says why here, it never calls itself (#901)
            return M.AssistantTestResult(ok=False, message=str(exc))
        return await check_connection(
            service.settings.current(), http_client=getattr(service, "http_client", None)
        )

    @router.get(
        "/api/assistant/conversation",
        response_model=M.AssistantConversation,
        operation_id="getAssistantConversation",
        dependencies=[Depends(auth)],
    )
    def conversation(bench: str = Query(...)) -> M.AssistantConversation:
        return bench_errors(ctx, lambda: service.conversation(bench))  # type: ignore[no-any-return]

    @router.delete(
        "/api/assistant/conversation",
        operation_id="clearAssistantConversation",
        dependencies=[Depends(auth)],
        status_code=204,
        responses=_BUSY,
    )
    def clear(bench: str = Query(...)) -> None:
        """Clear the conversation (the files it changed stay as they are)."""
        bench_errors(ctx, lambda: service.clear(bench))

    @router.post(
        "/api/assistant/messages",
        response_model=M.AssistantTurn,
        operation_id="sendAssistantMessage",
        dependencies=[Depends(auth)],
        status_code=202,
        responses=_BUSY | {422: {"description": "The LLM is not configured"}},
    )
    async def send(req: M.AssistantMessageRequest) -> M.AssistantTurn:
        """Start a turn; follow it with GET /api/assistant/events."""
        try:
            return await service.send(req.bench, req.text, req.stop_consent)
        except (*FILE_ERRORS, Busy, ConfigError, KeyError) as exc:
            raise http_error(ctx, exc) from None


def _assistant_run_routes(ctx: ApiContext, router: APIRouter) -> None:
    auth = ctx.auth
    service = ctx.assistant

    @router.get(
        "/api/assistant/events",
        operation_id="assistantEvents",
        dependencies=[Depends(auth)],
        response_class=StreamingResponse,
        responses={200: {"model": M.AssistantEvent, "content": {"text/event-stream": {}}}},
    )
    async def events(
        bench: str = Query(...), turn: str = Query(...), after: int = Query(0, ge=0)
    ) -> StreamingResponse:
        """Server-sent events of a turn (each `data:` line one AssistantEvent), after `after`."""
        bench_errors(ctx, lambda: service.files.resolve(bench))
        stream = service.events(bench, turn, after)
        try:
            first = await anext(stream)
        except StopAsyncIteration:
            first = None
        except KeyError as exc:
            raise ctx.not_found(exc) from None

        async def body() -> AsyncIterator[bytes]:
            if first is not None:
                yield f"data: {first.model_dump_json()}\n\n".encode()
            async for ev in stream:
                yield f"data: {ev.model_dump_json()}\n\n".encode()

        return StreamingResponse(
            body(), media_type="text/event-stream", headers={"cache-control": "no-cache"}
        )

    @router.post(
        "/api/assistant/stop",
        response_model=M.AssistantConversation,
        operation_id="stopAssistantTurn",
        dependencies=[Depends(auth)],
    )
    async def stop(req: M.AssistantStopRequest) -> M.AssistantConversation:
        """Stop the running turn; what it already wrote can be restored."""
        bench_errors(ctx, lambda: service.files.resolve(req.bench))
        await service.stop(req.bench)
        return bench_errors(ctx, lambda: service.conversation(req.bench))  # type: ignore[no-any-return]

    @router.get(
        "/api/assistant/rollback",
        response_model=M.RollbackPlan,
        operation_id="assistantRollbackPlan",
        dependencies=[Depends(auth)],
    )
    def rollback_plan(bench: str = Query(...)) -> M.RollbackPlan:
        """What "restore to before the last turn" would do: files restored or deleted, changes made
        since that would be lost, sessions to stop."""
        return bench_errors(ctx, lambda: service.rollback_plan(bench))  # type: ignore[no-any-return]

    @router.post(
        "/api/assistant/rollback",
        response_model=M.RollbackResult,
        operation_id="assistantRollback",
        dependencies=[Depends(auth)],
        responses={409: {"description": "Not the last turn any more, or scripts are connected"}},
    )
    async def rollback(req: M.RollbackRequest) -> M.RollbackResult:
        """Restore the files the last turn wrote, delete the ones it created (the user's action)."""
        try:
            return await service.rollback(req)
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from None
        except ClientsConnected as exc:
            raise ctx.in_use(exc) from None
        except FILE_ERRORS as exc:
            raise ctx.http_error(exc) from None


def build_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    _assistant_chat_routes(ctx, router)
    _assistant_run_routes(ctx, router)
    return router
