"""The K-series marker measurements: a field of a marker's reading and the delta between two markers. Part of ``ScopeMeasurements`` (signal-chain.md 3.4, #1540)."""

from __future__ import annotations

import math
from typing import Any

from ...engine.component import (
    OpResult,
)
from ...scpi import values as V
from ...scpi.errors import ScpiError
from ...units import parse_number
from .scope import short_source
from .scope_measurements_host import MeasurementsHost


class KMarkers:
    def __init__(self, host: MeasurementsHost) -> None:
        self.host = host
        self.k_markers: dict[int, dict[str, Any]] = {}  # the numbered markers' state, #1074

    def marker_measurements(
        self, measurement: Any = None, state: Any = None, query: bool = False, **_: Any
    ) -> OpResult:
        if isinstance(measurement, list):  # a raw parameter takes all the remaining ones
            measurement = measurement[0] if measurement else None
        if measurement is None:
            if not query:
                raise ScpiError(-109)
            tracked = sorted(m["measurement"] for m in self.k_markers.values() if m.get("tracking"))
            return OpResult((",".join(f"MEAS{n}" for n in tracked)).encode()) if tracked else OpResult()
        word = V.parse_string(measurement if isinstance(measurement, bytes) else str(measurement).encode())
        digits = word.upper().removeprefix("MEAS").removeprefix("UREMENT").removeprefix("UREMENT")
        # MEASurement<N> rides its own short spellings (MEAS1, MEASurement1); a bare number too
        if not (digits.startswith("1") or digits.isdigit()):
            digits = digits.lstrip("0")
        if not digits.isdigit():
            raise ScpiError(-224, f"{word}: not a measurement number")
        n = int(digits)
        slot = next((k for k, m in self.k_markers.items() if m.get("measurement") == n), None)
        if state:  # an omitted parameter arrives as an empty list, not None
            on = V.flag(state)
            if slot is None and on:
                slot = max(self.k_markers, default=0) + 1
            if slot is not None:
                if on:
                    self.k_markers[slot] = {"tracking": True, "measurement": n}
                else:
                    self.k_markers.pop(slot, None)
            return OpResult()
        self.k_markers = {k: m for k, m in self.k_markers.items() if not m.get("tracking")}
        self.k_markers[max(self.k_markers, default=0) + 1] = {"tracking": True, "measurement": n}
        meas_path = self.host.bound_path("marker_measurement")
        if meas_path is not None:
            self.host.set_state(meas_path, n, source="scpi", internal=True)
        return OpResult()

    def _k(self, suffixes: dict[str, int] | None) -> int:
        """The ``<K>`` a numbered-marker command carried, checked against the model's count."""
        most = int(self.host.param("markers") or 0)
        k = int(next(iter(suffixes.values()))) if suffixes else 1
        if most <= 0 or not 1 <= k <= most:
            raise ScpiError(-114, f"marker {k} is not 1..{most}")
        return k

    def _k_marker(self, k: int) -> dict[str, Any]:
        """Marker k's state, its defaults in: XMANual type, channel 1, off."""
        return self.k_markers.setdefault(
            k, {"type": "XMANual", "source": "CHANnel1", "enable": False, "x": 0.0, "y": 0.0}
        )

    def k_field(
        self,
        field: str = "type",
        value: Any = None,
        query: bool = False,
        suffixes: dict[str, int] | None = None,
        **_: Any,
    ) -> OpResult:
        k = self._k(suffixes)
        marker = self._k_marker(k)
        key = {
            "type": "type",
            "source": "source",
            "enable": "enable",
            "name": "name",
            "color": "color",
            "cmode": "cmode",
            "x": "x",
            "y": "y",
        }[field]
        if isinstance(value, list):  # the raw parameter arrives as the unit's parameter list
            value = value[0] if value else None
        if query:
            if key == "x" and marker["type"] == "YMANual":
                return OpResult(str(self.host.param("invalid_value")).encode())
            if key == "y" and marker["type"] == "XMANual":
                return OpResult(str(self.host.param("invalid_value")).encode())
            if key == "y" and marker["type"] in ("TRACk", "RF"):
                # a tracking marker marks its source's value at its x (the guide's TRACk / RF)
                rec, used = self.host.cursors.source_record(str(marker.get("source") or ""))
                y = self.host.cursors.value_at(rec, float(marker.get("x") or 0.0))
                if not math.isfinite(y):
                    return OpResult(str(self.host.param("invalid_value")).encode(), used)
                return OpResult(self.host.format_number(y), used)
            current = marker.get(key)
            if key == "enable":
                return OpResult(V.format_int(int(bool(current))))
            if isinstance(current, (int, float)) and key in ("x", "y"):
                return OpResult(self.host.format_number(float(current)))
            if current in (None, ""):
                return OpResult(b'""')
            if key == "type":
                return OpResult(
                    {"XMANual": "XMAN", "YMANual": "YMAN", "TRACk": "TRAC", "RF": "RF"}[current].encode()
                )
            if key == "source":
                return OpResult(short_source(current).encode())
            if key == "cmode":
                return OpResult(b"CUST" if current == "CUSTom" else b"SOUR")
            if key in ("name", "color"):  # a quoted string, as the manual's Returned Format has it
                return OpResult(f'"{current}"'.encode())
            return OpResult(str(current).encode())
        if value is None:
            raise ScpiError(-109)
        raw = value if isinstance(value, bytes) else str(value).encode()
        if key == "type":
            word = V.parse_string(raw).upper()
            if word not in ("XMANUAL", "YMANUAL", "TRACK", "RF"):
                raise ScpiError(-224, f"{value}: XMANual, YMANual, TRACk or RF")
            if marker["enable"]:
                raise ScpiError(-221, f"marker {k} is on: turn it off before changing its type")
            marker["type"] = {"XMANUAL": "XMANual", "YMANUAL": "YMANual", "TRACK": "TRACk", "RF": "RF"}[word]
            return OpResult()
        if key == "enable":
            marker["enable"] = V.flag(raw)
            return OpResult()
        if key == "cmode":
            word = V.parse_string(raw).upper()
            if word not in ("CUSTOM", "SOURCE"):
                raise ScpiError(-224, f"{value}: CUSTom or SOURce")
            marker["cmode"] = "CUSTom" if word == "CUSTOM" else "SOURce"
            return OpResult()
        if key in ("source", "name", "color"):
            marker[key] = V.parse_string(raw)
            return OpResult()
        if key == "x" and marker["type"] == "YMANual":
            raise ScpiError(-221, "a Y-only marker has no x position")
        if key == "y" and marker["type"] in ("XMANual", "TRACk", "RF"):
            raise ScpiError(-221, "an X-only or tracking marker has no y position")
        try:
            number = parse_number(raw.decode("ascii", errors="replace"))
        except ValueError:
            raise ScpiError(-104, f"{value!r}: not a number") from None
        marker[key] = number
        return OpResult()

    def marker_delta(
        self,
        other: Any = None,
        state: Any = None,
        query: bool = True,
        suffixes: dict[str, int] | None = None,
        **_: Any,
    ) -> OpResult:
        if isinstance(other, list):  # a raw parameter takes all the remaining ones: the ON/OFF
            extra = other[1:]  # state of a :MARKer<K>:DELTa MARKer<L>,{ON | OFF} rides here
            other = other[0] if other else None
            if not state and extra:  # an omitted parameter arrives as an empty list
                state = extra[0]
        if isinstance(state, list):
            state = state[0] if state else None
        raw = other if isinstance(other, bytes) else (str(other).encode() if other is not None else b"")
        word = V.parse_string(raw).upper() if raw else ""
        if word.startswith("MARKER") and word.removeprefix("MARKER").isdigit():
            k = self._k(suffixes)
            other_k = int(word.removeprefix("MARKER"))
            if not 1 <= other_k <= int(self.host.param("markers") or 0) or other_k == k:
                raise ScpiError(-224, f"{other!r}: not another marker of this model")
            first, second = self._k_marker(k), self._k_marker(other_k)
            if not query:
                word = (
                    ""
                    if state is None
                    else V.parse_string(state if isinstance(state, bytes) else str(state).encode())
                )
                first["delta_to"] = other_k if state is None or V.flag(word) else None
                return OpResult()
            on = first.get("delta_to") == other_k
            dx = float(second["x"]) - float(first["x"])
            invalid = str(self.host.param("invalid_value")).encode()
            parts = [V.format_int(int(on)), self.host.format_number(dx)]
            parts.append(self.host.format_number(1.0 / dx) if dx else invalid)
            if first["type"] == second["type"] == "YMANual":
                dy = float(second["y"]) - float(first["y"])
                parts.append(self.host.format_number(dy))
                parts.append(self.host.format_number(dy / dx) if dx else invalid)
            else:
                parts += [invalid, invalid]
            return OpResult(b",".join(parts))
        if not query:  # :MARKer:DELTa {ON | OFF}: the screen's delta-markers check box
            path = self.host.bound_path("marker_delta")
            if path is None:
                raise ScpiError(-113, "this model has no delta-marker display setting")
            self.host.set_state(path, V.flag(raw), source="scpi", internal=True)
            return OpResult()
        path = self.host.bound_path("marker_delta")
        if path is None:
            raise ScpiError(-113, "this model has no delta-marker display setting")
        return OpResult(V.format_int(int(V.flag(self.host.get_state(path)))))
