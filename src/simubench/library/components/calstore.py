"""Calibration storage: per-key calibration coefficients and the calibration ledger (C3)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...engine.component import Component, OpResult, component, op
from ...scpi import values as V
from ...scpi.errors import ScpiError

if TYPE_CHECKING:
    from ...engine.instrument import Instrument
    from ...engine.session import Session

#: Calibration errors (positive device codes, as on real meters). The definition's ``errors`` table
#: gives the texts of a real model; these are the defaults.
CAL_SECURED = 701
CAL_INPUT = 702


@component("calibration_store")
class CalibrationStore(Component):
    """Calibration coefficients keyed by the measurement context, and the calibration ledger.

    A measuring core references the store with ``store: <this component's name>`` and reads its
    reading as ``(raw − offset[key]) × gain[key]``; a key never adjusted gives gain 1 and offset 0.
    The key comes from the ``key`` parameter, one entry per key dimension:

    * ``$path`` - the value of that instrument state (e.g. ``$sense.function``);
    * ``component.param`` - that component's parameter; for the calibrated core itself,
      ``function`` is the function of the measurement being made and ``range`` the range actually
      in use (the auto-chosen one included), e.g. ``[meter.function, meter.range]``.

    ``adjust`` (a reference of 0 adjusts the zero, anything else the gain) checks the core's raw
    input against the reference first: the deviation must stay within ``tolerance`` of the
    reference (of ``max(|reference|, 1)`` for the zero, where the reference itself is 0). A failed
    check pushes the calibration error (702 by default) and replies ``fail_reply``. Adjusted
    coefficients take effect at once and become permanent when ``store`` commits them; whether an
    un-stored adjustment survives ``*RST`` is ``volatile_until_store``. The ledger (``count``,
    ``date``, ``time``, ``string``) and the security ``code`` are never touched by ``*RST``.
    ``secured`` and ``value`` may be bound to instrument state (``$cal.secured``); set through the
    ops they write that state back, so the store follows the instrument's own variables.
    """

    defaults = {
        "secured": True,  # calibration secured: adjusting needs the security code
        "code": "SIMUBENCH",  # the factory security code
        "key": [],  # key dimensions: '$state.path' or 'component.param' entries
        "value": 0.0,  # the reference a gain adjustment assumes on the input (a setting)
        "tolerance": 0.1,  # fraction of the reference the input may be off by
        "ok_reply": "+0",  # reply of a successful adjustment
        "fail_reply": "+1",  # reply of a failed adjustment
        "volatile_until_store": False,  # *RST drops adjustments that were not stored
    }
    units = {"value": "V"}

    def __init__(
        self,
        name: str,
        params: dict[str, Any],
        owner: Instrument | None,
        session: Session,
        channel: int | None = None,
    ) -> None:
        super().__init__(name, params, owner, session, channel)
        self._stored: dict[tuple, dict[str, float]] = {}  # key -> committed coefficients
        self._pending: dict[tuple, dict[str, float]] = {}  # key -> adjustments not stored yet
        self._count = 0
        self._date = ""
        self._time = ""
        self._string = ""
        self._code: str | None = None  # changed by the code op; None: the factory code
        self._value: float | None = None  # set when unbound (bound ones live in state)
        self._secured: bool | None = None

    # ---- state ---------------------------------------------------------------------------------------

    def reset(self) -> None:
        if self.param("volatile_until_store"):
            self._pending.clear()

    def describe(self) -> dict[str, Any]:
        out = super().describe()
        out.update(
            {
                "count": self._count,
                "date": self._date,
                "time": self._time,
                "string": self._string,
                "keys": sorted(str(k) for k in set(self._stored) | set(self._pending)),
            }
        )
        return out

    def _write(self, key: str, value: Any) -> None:
        """A secured/reference change: into the bound state when there is one, else here."""
        path = self.bound_paths().get(key)
        if path and self.owner is not None:
            self.owner.set_state(path, value, source="scpi", internal=True)
        elif key == "secured":
            self._secured = bool(value)
        elif key == "value":
            self._value = float(value)

    @property
    def is_secured(self) -> bool:
        if self._secured is not None:
            return self._secured
        return bool(self.param("secured"))

    def _reference(self) -> float:
        if self._value is not None:
            return self._value
        return float(self.param("value"))

    def _code_now(self) -> str:
        return self._code if self._code is not None else str(self.param("code"))

    # ---- the calibrated core and its key -------------------------------------------------------------

    def _meter(self) -> Component:
        assert self.owner is not None
        for comp in self.owner.components.values():
            if str(comp.param("store") or "") == self.name:
                return comp
        raise ScpiError(-300, f"{self.uid}: no component references this calibration store")

    def _key(self, meter: Component, function: str) -> tuple:
        assert self.owner is not None
        out: list[Any] = []
        for entry in self.param("key") or ():
            if isinstance(entry, str) and entry.startswith("$"):
                out.append(self.owner.get_state(self.resolve_path(entry[1:])))
                continue
            comp_name, _, param = entry.rpartition(".")
            comp = self.owner.components.get(comp_name)
            if comp is None:
                raise ValueError(f"{self.uid}: key entry {entry!r} names no component")
            if comp is meter and param == "function":
                out.append(function)
            elif comp is meter and param == "range":
                out.append(comp.effective_range(function))
            else:
                out.append(comp.param(param))
        return tuple(out)

    def _coeff(self, key: tuple) -> dict[str, float]:
        return {**self._stored.get(key, {}), **self._pending.get(key, {})}

    def coefficients(self, function: str) -> tuple[float, float]:
        """The (gain, offset) correcting the core's raw reading for this function's key."""
        gain, offset = 1.0, 0.0
        if self.owner is not None:
            c = self._coeff(self._key(self._meter(), function))
            gain, offset = float(c.get("gain", 1.0)), float(c.get("offset", 0.0))
        return gain, offset

    # ---- adjustment ----------------------------------------------------------------------------------

    def _ok(self) -> OpResult:
        return OpResult(str(self.param("ok_reply")).encode())

    def _fail(self) -> OpResult:
        return OpResult(str(self.param("fail_reply")).encode())

    def _ensure_unlocked(self) -> None:
        if self.is_secured:
            raise ScpiError(CAL_SECURED, "Calibration error; calibration is secured")

    def adjust_offset(self, meter: Component) -> OpResult:
        """Zero adjustment: with 0 on the input, make the reading zero for the current key."""
        self._ensure_unlocked()
        function = meter.active_function()
        raw = meter.raw(function)
        full = max(abs(self._reference()), 1.0)
        if abs(raw) > float(self.param("tolerance")) * full:
            assert meter.owner is not None
            meter.owner.push_error(CAL_INPUT, "Calibration error; input is not zero")
            return self._fail()
        key = self._key(meter, function)
        self._pending[key] = {**self._coeff(key), "offset": raw}
        return self._ok()

    def adjust_gain(self, meter: Component) -> OpResult:
        """Gain adjustment: with the reference on the input, make the reading equal it."""
        self._ensure_unlocked()
        function = meter.active_function()
        target = self._reference()
        key = self._key(meter, function)
        corrected = meter.raw(function) - self._coeff(key).get("offset", 0.0)
        if target == 0 or corrected == 0 or abs(corrected / target - 1) > float(self.param("tolerance")):
            assert meter.owner is not None
            meter.owner.push_error(CAL_INPUT, "Calibration error; input does not match the calibration value")
            return self._fail()
        self._pending[key] = {**self._coeff(key), "gain": target / corrected}
        return self._ok()

    def commit(self) -> None:
        """Store the adjustments: they count, they are dated, they survive ``*RST``."""
        self._stored.update(self._pending)
        self._pending.clear()
        self._count += 1
        assert self.owner is not None
        now = self.owner.clock()
        self._date = now.date().isoformat()
        self._time = now.strftime("%H:%M:%S")

    # ---- operations ----------------------------------------------------------------------------------

    @op("secure")
    def op_secure(self, query: bool = False, values: list[bytes] | None = None, **_: Any) -> OpResult:
        """:CALibration:SECure:STATe {ON|OFF},<code>: unsecuring needs the security code."""
        if query:
            return OpResult(V.format_bool(self.is_secured))
        params = list(values or [])
        if not params:
            raise ScpiError(-109)
        secure = V.parse_bool(params[0])
        code = V.parse_string(params[1]) if len(params) > 1 else None
        if not secure and code != self._code_now():
            raise ScpiError(CAL_SECURED, "Calibration error; wrong security code")
        self._write("secured", secure)
        return OpResult()

    @op("code")
    def op_code(self, values: list[bytes] | None = None, **_: Any) -> OpResult:
        """A new security code; the calibration must be unsecured."""
        self._ensure_unlocked()
        params = list(values or [])
        if not params:
            raise ScpiError(-109)
        self._code = V.parse_string(params[0])
        return OpResult()

    @op("value")
    def op_value(self, query: bool = False, values: list[bytes] | None = None, **_: Any) -> OpResult:
        """The reference a gain adjustment assumes on the input."""
        if query:
            return OpResult(V.format_float(self._reference(), 6))
        params = list(values or [])
        if not params:
            raise ScpiError(-109)
        self._write("value", V.parse_float(params[0]))
        return OpResult()

    @op("adjust")
    def op_adjust(self, reference: float | None = None, **_: Any) -> OpResult:
        """Adjust for the current key: a reference of 0 zeroes, any other adjusts the gain."""
        meter = self._meter()
        ref = self._reference() if reference is None else float(reference)
        return self.adjust_offset(meter) if ref == 0 else self.adjust_gain(meter)

    @op("store")
    def op_store(self, **_: Any) -> OpResult:
        """:CALibration:STORe - keep the adjustments; counts the calibration and dates it."""
        self._ensure_unlocked()
        self.commit()
        return OpResult()

    @op("count")
    def op_count(self, query: bool = False, values: list[bytes] | None = None, **_: Any) -> OpResult:
        """How many calibrations have been stored (a query; the count is not settable)."""
        if not query:
            raise ScpiError(-221, "read-only setting")
        return OpResult(V.format_int(self._count))

    @op("date")
    def op_date(self, query: bool = False, values: list[bytes] | None = None, **_: Any) -> OpResult:
        """The date of the last stored calibration (a query)."""
        if not query:
            raise ScpiError(-221, "read-only setting")
        return OpResult(V.format_string(self._date))

    @op("time")
    def op_time(self, query: bool = False, values: list[bytes] | None = None, **_: Any) -> OpResult:
        """The time of the last stored calibration (a query)."""
        if not query:
            raise ScpiError(-221, "read-only setting")
        return OpResult(V.format_string(self._time))

    @op("string")
    def op_string(self, query: bool = False, values: list[bytes] | None = None, **_: Any) -> OpResult:
        """The calibration message; written by the user, survives *RST."""
        if query:
            return OpResult(V.format_string(self._string))
        params = list(values or [])
        if not params:
            raise ScpiError(-109)
        self._string = V.parse_string(params[0])
        return OpResult()
