"""SCPI error codes (SCPI-99 21.8) and the per-instrument error queue."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

STANDARD_MESSAGES: dict[int, str] = {
    0: "No error",
    -100: "Command error",
    -101: "Invalid character",
    -102: "Syntax error",
    -103: "Invalid separator",
    -104: "Data type error",
    -108: "Parameter not allowed",
    -109: "Missing parameter",
    -113: "Undefined header",
    -114: "Header suffix out of range",
    -120: "Numeric data error",
    -131: "Invalid suffix",
    -138: "Suffix not allowed",
    -141: "Invalid character data",
    -150: "String data error",
    -160: "Block data error",
    -200: "Execution error",
    -220: "Parameter error",
    -221: "Settings conflict",
    -222: "Data out of range",
    -224: "Illegal parameter value",
    -230: "Data corrupt or stale",
    -240: "Hardware error",
    -300: "Device-specific error",
    -310: "System error",
    -311: "Memory error",
    -313: "Calibration memory lost",
    -314: "Save/recall memory lost",
    -315: "Configuration memory lost",
    -330: "Self-test failed",
    -350: "Queue overflow",
    -360: "Communication error",
    -363: "Input buffer overrun",
    -400: "Query error",
    -410: "Query INTERRUPTED",
    -420: "Query UNTERMINATED",
}

#: Standard event status register bit for each error class (IEEE 488.2 11.5.1.1).
ESR_BITS = {"command": 5, "execution": 4, "device": 3, "query": 2}


@dataclass(frozen=True)
class ScpiError(Exception):
    """An error raised while executing a command; queued and flagged in the ESR."""

    code: int
    detail: str = ""

    @property
    def message(self) -> str:
        base = STANDARD_MESSAGES.get(self.code)
        if base is None and self.code > 0 and self.detail:
            return self.detail  # device-specific codes carry their own message, e.g. 325,"Input overload"
        if base is None:
            # The family of -313 is -310 (truncate toward zero; // would give -320).
            family = -(abs(self.code) // 10 * 10) if self.code < 0 else self.code // 10 * 10
            base = STANDARD_MESSAGES.get(family, "Error")
        return f"{base}; {self.detail}" if self.detail else base

    @property
    def esr_class(self) -> str:
        if -199 <= self.code <= -100:
            return "command"
        if -299 <= self.code <= -200:
            return "execution"
        if -499 <= self.code <= -400:
            return "query"
        return "device"

    def __str__(self) -> str:
        return f'{self.code},"{self.message}"'


class ErrorQueue:
    """FIFO error queue; the last slot reports -350 on overflow (SCPI-99 21.8.2)."""

    def __init__(self, capacity: int = 20) -> None:
        self.capacity = capacity
        self._q: deque[ScpiError] = deque()

    def push(self, err: ScpiError) -> None:
        if len(self._q) >= self.capacity:
            return
        if len(self._q) == self.capacity - 1:
            self._q.append(ScpiError(-350))
            return
        self._q.append(err)

    def pop(self) -> ScpiError:
        return self._q.popleft() if self._q else ScpiError(0)

    def clear(self) -> None:
        self._q.clear()

    def __len__(self) -> int:
        return len(self._q)

    def peek_all(self) -> list[ScpiError]:
        return list(self._q)
