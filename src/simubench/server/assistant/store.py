"""Conversations on disk (#873): one per bench, in the workspace, kept until the user clears it.

``.simubench/assistant/<bench path>.json`` holds what the panel shows (the turns, complete), the
messages sent to the LLM (compacted when the conversation grew long), and per turn which files it
wrote and their versions before and after (for "restore to before the last turn", #876). The
folder is git-ignored with the rest of ``.simubench/``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from ...contracts import api as M
from ..errors import Forbidden, Invalid


@dataclass
class TurnFiles:
    """The files a turn wrote: their sha256 before it (None: the turn created the file) and after."""

    before: dict[str, str | None] = field(default_factory=dict)
    after: dict[str, str] = field(default_factory=dict)
    kinds: dict[str, str] = field(default_factory=dict)
    #: The text before the turn, for a version the history no longer has (merged node moves).
    content: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"before": self.before, "after": self.after, "kinds": self.kinds, "content": self.content}

    @classmethod
    def of(cls, data: dict[str, Any]) -> TurnFiles:
        return cls(
            dict(data.get("before") or {}),
            dict(data.get("after") or {}),
            dict(data.get("kinds") or {}),
            dict(data.get("content") or {}),
        )


@dataclass
class Conversation:
    bench: str
    turns: list[M.AssistantTurn] = field(default_factory=list)
    messages: list[ModelMessage] = field(default_factory=list)
    files: dict[str, TurnFiles] = field(default_factory=dict)
    compacted: bool = False
    #: The index of the turn in which the assistant asked to stop the running bench; consent for
    #: the next turn only (:meth:`stop_consent_alive`).
    stop_requested_in: int | None = None
    #: Said to the LLM with the next message (the user restored the files meanwhile).
    note: str | None = None

    def user_texts(self) -> list[str]:
        return [t.user for t in self.turns]

    def stop_consent_alive(self, turn: int) -> bool:
        """Is the agreement to stop the running bench alive for ``turn``? A request asked in turn
        n is consent for turn n+1 only (#902): asked in the turn right before, and nothing else —
        not in the turn it was asked (the user has not answered yet), not a turn later (lapsed),
        and without a request nothing is alive. The one judgment both the sending side
        (:meth:`stop_consent_pending`) and the ``stop_session`` gate ask."""
        return self.stop_requested_in is not None and self.stop_requested_in == turn - 1

    def stop_consent_pending(self) -> bool:
        """Whether the message about to be sent answers a stop request: the one on record was
        asked in the last recorded turn. A stale request is forgotten here, so a lapsed one
        cannot answer a later message. The tool side asks only :meth:`stop_consent_alive`, which
        forgets nothing: a request the user has not answered yet must survive a premature stop
        attempt in the turn that asked it, or the panel's button would have nothing to agree to."""
        alive = self.stop_consent_alive(len(self.turns))
        if not alive:
            self.stop_requested_in = None
        return alive


class ConversationStore:
    def __init__(self, workspace: Path) -> None:
        self.root = workspace / ".simubench" / "assistant"
        self.workspace = workspace

    def _path(self, bench: str) -> Path:
        p = (self.root / f"{bench}.json").resolve()
        if self.root.resolve() not in p.parents:
            raise Forbidden("bench path outside the workspace")
        return p

    def load(self, bench: str) -> Conversation:
        path = self._path(bench)
        if not path.is_file():
            return Conversation(bench)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise Invalid(f"{path.name}: expected a mapping at the top level")
            turns = [M.AssistantTurn.model_validate(t) for t in data.get("turns", [])]
            messages = ModelMessagesTypeAdapter.validate_python(data.get("messages", []))
            files = {k: TurnFiles.of(v) for k, v in (data.get("files") or {}).items()}
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
            # A conversation file a reload truncated or a hand edit broke is the user's to fix
            # (#1725): a domain error with the position, never a silently emptied conversation.
            raise Invalid(f"{path.name}: {exc}") from None
        return Conversation(
            bench=bench,
            turns=turns,
            messages=messages,
            files=files,
            compacted=bool(data.get("compacted")),
            stop_requested_in=data.get("stop_requested_in"),
            note=data.get("note"),
        )

    def save(self, conv: Conversation) -> None:
        path = self._path(conv.bench)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "turns": [t.model_dump(mode="json") for t in conv.turns],
            "messages": ModelMessagesTypeAdapter.dump_python(conv.messages, mode="json"),
            "files": {k: v.as_dict() for k, v in conv.files.items()},
            "compacted": conv.compacted,
            "stop_requested_in": conv.stop_requested_in,
            "note": conv.note,
        }
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def clear(self, bench: str) -> None:
        self._path(bench).unlink(missing_ok=True)
