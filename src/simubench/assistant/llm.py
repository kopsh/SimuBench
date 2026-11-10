"""The LLM behind the assistant: one PydanticAI model per protocol (#872, D66).

``openai`` speaks the Chat Completions API (``<base_url>/chat/completions``), ``anthropic`` the
Messages API (``<base_url>/v1/messages``); both stream and call tools. The configured context
length becomes the model profile's ``context_window``, which the compaction reads
(:mod:`simubench.assistant.compaction`).
"""

from __future__ import annotations

import os
import time
from typing import Any

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")  # a server, not a terminal session

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings

from ..models import api as M
from .config import ConfigError, LlmSettings

#: Some private deployments need no key, but both SDKs refuse to start without one.
NO_KEY = "not-needed"


def build_model(settings: LlmSettings, http_client: Any = None) -> Model:
    """The PydanticAI model the settings describe; ``http_client`` (an ``httpx2.AsyncClient``)
    replaces the network in tests."""
    if not settings.configured:
        raise ConfigError("还没有配置 LLM：在设置页填写地址和模型")
    profile = {"context_window": settings.context_tokens}
    key = settings.api_key or NO_KEY
    if settings.protocol == "anthropic":
        from anthropic import AsyncAnthropic
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        # Anthropic-compatible gateways read the key from x-api-key or from Authorization: both.
        client = AsyncAnthropic(
            api_key=key,
            base_url=settings.base_url,
            http_client=http_client,
            default_headers={"x-api-key": key},
            timeout=settings.timeout_s,
        )
        return AnthropicModel(
            settings.model,  # type: ignore[arg-type]
            provider=AnthropicProvider(anthropic_client=client),
            profile=profile,  # type: ignore[arg-type]
        )
    if settings.protocol != "openai":
        raise ConfigError(f"不支持的协议 {settings.protocol!r}：openai 或 anthropic")
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    oai = AsyncOpenAI(
        api_key=key, base_url=settings.base_url, http_client=http_client, timeout=settings.timeout_s
    )
    return OpenAIChatModel(
        settings.model,  # type: ignore[arg-type]
        provider=OpenAIProvider(openai_client=oai),
        profile=profile,  # type: ignore[arg-type]
    )


def model_settings(settings: LlmSettings) -> ModelSettings:
    return ModelSettings(timeout=settings.timeout_s)


def describe_error(exc: BaseException, settings: LlmSettings) -> str:
    """An error from the LLM service, in words, without the key (it may sit in a URL or a header
    echoed back): what happened → where → what to do."""
    text = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    if settings.api_key:
        text = text.replace(settings.api_key, "***")
    name = type(exc).__name__
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if status in (401, 403):
        return f"LLM 服务拒绝了请求（{status}）：检查 API key — {text}"
    if status == 404:
        return f"LLM 服务找不到这个地址或模型（404）：检查地址与模型名 — {text}"
    if "Timeout" in name or "timed out" in text.lower():
        return f"LLM 服务在 {settings.timeout_s:g} 秒内没有回应：检查地址，或调大超时 — {text}"
    if "Connect" in name or "connection" in text.lower():
        return f"连不上 LLM 服务 {settings.base_url}：检查地址与网络 — {text}"
    return f"LLM 服务出错：{text}"


async def check_connection(settings: LlmSettings, http_client: Any = None) -> M.AssistantTestResult:
    """One short streamed request with a tool, as a turn makes them: a model or a service that cannot
    call tools or stream is noticed here."""
    try:
        model = build_model(settings, http_client)
    except ConfigError as exc:
        return M.AssistantTestResult(ok=False, message=str(exc))
    agent: Agent[None, str] = Agent(model, instructions="Call the ping tool once, then answer OK.")
    called: list[bool] = []

    @agent.tool_plain
    def ping() -> str:
        """Answers pong."""
        called.append(True)
        return "pong"

    t0 = time.perf_counter()
    try:
        async with agent.run_stream_events("ping", model_settings=model_settings(settings)) as stream:
            async for _ in stream:
                pass
    except Exception as exc:
        return M.AssistantTestResult(ok=False, message=describe_error(exc, settings))
    ms = (time.perf_counter() - t0) * 1000
    if not called:
        return M.AssistantTestResult(
            ok=False,
            message="连上了，但模型没有调用工具：助手需要支持 tool calling 的模型",
            latency_ms=ms,
        )
    return M.AssistantTestResult(ok=True, message=f"连接成功：{settings.model}，可以调用工具", latency_ms=ms)
