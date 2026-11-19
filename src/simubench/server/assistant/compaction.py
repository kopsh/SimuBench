"""Keeping the conversation inside the model's context (#873, D66): a PydanticAI history processor.

There is no limit on the number of steps in a turn; instead, before each model request, when the
context is close to full (the last response's usage over the configured context length, or an
estimate when no usage is known yet), older content is compacted:

1. tool results of turns before the last :data:`KEEP_TURNS` are replaced by a short note;
2. if that is not enough, the oldest whole turns are dropped (a note says so);
3. if the current turn alone is too long, its older tool results are cleared as in 1, the last
   :data:`KEEP_RESULTS` kept.

A tool call and its return are never split: results are cleared in place, and only whole turns
(which begin with the user's message) are dropped. The instructions are not part of the history and
are sent with every request. The stored conversation the user sees is not affected — only what is
sent to the LLM.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolReturnPart,
    UserPromptPart,
)

TRIGGER = 0.75  # compact when the context is this full
TARGET = 0.5  # and bring it down to about this
KEEP_TURNS = 2
KEEP_RESULTS = 6
CLEARED = "（较早的工具结果已压缩；需要时重新读取）"
DROPPED = "（更早的对话已压缩，只保留了最近几轮）"


def estimate_tokens(messages: list[ModelMessage]) -> int:
    """About one token per three characters (Chinese and JSON mixed); only for deciding."""
    size = 0
    for m in messages:
        for p in m.parts:
            content = getattr(p, "content", None)
            args = getattr(p, "args", None)
            for value in (content, args):
                if value is None:
                    continue
                size += (
                    len(value)
                    if isinstance(value, str)
                    else len(json.dumps(value, default=str, ensure_ascii=False))
                )
    return size // 3 + 1


def _turn_starts(messages: list[ModelMessage]) -> list[int]:
    return [
        i
        for i, m in enumerate(messages)
        if isinstance(m, ModelRequest) and any(isinstance(p, UserPromptPart) for p in m.parts)
    ]


def _clear_results(messages: list[ModelMessage], keep_last: int = 0) -> tuple[list[ModelMessage], bool]:
    """Tool results replaced by :data:`CLEARED`, except the last ``keep_last`` of them."""
    positions = [
        (i, j)
        for i, m in enumerate(messages)
        if isinstance(m, ModelRequest)
        for j, p in enumerate(m.parts)
        if isinstance(p, ToolReturnPart | RetryPromptPart) and p.content != CLEARED
    ]
    targets = positions[: len(positions) - keep_last] if keep_last else positions
    if not targets:
        return messages, False
    out = list(messages)
    for i, j in targets:
        req = out[i]
        assert isinstance(req, ModelRequest)
        parts = list(req.parts)
        part = parts[j]
        if isinstance(part, (ToolReturnPart, RetryPromptPart)):
            parts[j] = replace(part, content=CLEARED)
        out[i] = replace(req, parts=parts)
    return out, True


def compact(
    messages: list[ModelMessage], budget: int, used: int | None = None
) -> tuple[list[ModelMessage], bool]:
    """``messages`` brought under ``TARGET * budget`` tokens when they are over ``TRIGGER * budget``;
    ``used``: the tokens the last response reported (else estimated)."""
    size = used if used is not None else estimate_tokens(messages)
    if size < TRIGGER * budget:
        return messages, False
    target = TARGET * budget
    starts = _turn_starts(messages)
    changed = False
    if len(starts) > KEEP_TURNS:
        cut = starts[-KEEP_TURNS]
        old, recent = _clear_results(messages[:cut])
        messages, changed = old + messages[cut:], recent
        while estimate_tokens(messages) > target:
            starts = _turn_starts(messages)
            if len(starts) <= KEEP_TURNS:
                break
            # Drop the oldest turn whole; the next one starts with the user's message again.
            messages = messages[starts[1] :]
            changed = True
        if changed and not _has_note(messages):
            first = messages[0]
            assert isinstance(first, ModelRequest)
            messages = [replace(first, parts=[UserPromptPart(content=DROPPED), *first.parts]), *messages[1:]]
    if estimate_tokens(messages) > target:
        messages, cleared = _clear_results(messages, keep_last=KEEP_RESULTS)
        changed = changed or cleared
    return messages, changed


def _has_note(messages: list[ModelMessage]) -> bool:
    first = messages[0] if messages else None
    return isinstance(first, ModelRequest) and any(
        isinstance(p, UserPromptPart) and p.content == DROPPED for p in first.parts
    )


def last_usage(messages: list[ModelMessage]) -> int | None:
    for m in reversed(messages):
        if isinstance(m, ModelResponse) and m.usage.input_tokens:
            return int(m.usage.input_tokens + m.usage.output_tokens)
    return None


def processor(budget: Callable[[], int], on_compacted: Callable[[], None]):  # type: ignore[no-untyped-def]
    """The history processor for an agent: compacts against ``budget()`` tokens and calls
    ``on_compacted`` when it did."""

    def run(messages: list[ModelMessage]) -> list[ModelMessage]:
        tokens = budget()
        # What the last response reported, plus what came after it (tool results), or the
        # estimate when that is larger: a server that reports no usage must not overflow.
        used = estimate_tokens(messages)
        reported = last_usage(messages)
        if reported is not None:
            tail = [m for m in messages[-2:] if isinstance(m, ModelRequest)]
            used = max(used, reported + estimate_tokens(tail))
        out, changed = compact(messages, tokens, used)
        if changed:
            on_compacted()
        return out

    return run
