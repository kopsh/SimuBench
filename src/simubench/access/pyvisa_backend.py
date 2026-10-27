"""PyVISA backend: ``pyvisa.ResourceManager("bench.yaml@simubench")`` (D5, architecture.md 7.3).

The bench opens in this process. Every instrument answers at:

* its virtual address ``TCPIP0::127.0.0.1::<5025+i>::SOCKET`` (i = position in the bench file),
* the real-bench address configured in the bench file (``address:``), so scripts keep their strings,
* its instance name (``rm.open_resource("scope")``).

Reads follow VISA rules: a read ends at the termination character when it is enabled (so a binary
block containing 0x0A is cut short, exactly as on hardware), at the end of a response message, or
when ``count`` bytes have been read. A read with nothing queued is a timeout.
"""

from __future__ import annotations

import os
import random
import time
from collections import OrderedDict
from typing import Any

from pyvisa import attributes, constants, errors, highlevel, rname
from pyvisa.util import LibraryPath

from ..api import open_bench
from ..engine.faults import LinkDown
from ..engine.session import Session

BASE_PORT = 5025


class _VisaSession:
    def __init__(
        self, lib: SimuBenchVisaLibrary, instrument: str, resource_name: str, parsed: rname.ResourceName
    ):
        self.lib = lib
        self.instrument = instrument
        self.parsed = parsed
        self.session_type = (parsed.interface_type_const, parsed.resource_class)
        self.attrs: dict[Any, Any] = {
            constants.ResourceAttribute.resource_name: resource_name,
            constants.ResourceAttribute.resource_class: parsed.resource_class,
            constants.ResourceAttribute.interface_type: parsed.interface_type_const,
            constants.ResourceAttribute.interface_number: int(getattr(parsed, "board", 0) or 0),
        }
        if isinstance(parsed, rname.TCPIPSocket):
            self.attrs[constants.ResourceAttribute.tcpip_address] = parsed.host_address
            self.attrs[constants.ResourceAttribute.tcpip_port] = int(parsed.port)
        self.buffer = bytearray()  # bytes of the current response message not yet read
        self.connected = True

    def get_attribute(self, attribute: Any) -> tuple[Any, constants.StatusCode]:
        try:
            attr = attributes.AttributesByID[attribute]
        except KeyError:
            return 0, constants.StatusCode.error_nonsupported_attribute
        if not attr.in_resource(self.session_type):
            return 0, constants.StatusCode.error_nonsupported_attribute
        return self.attrs.setdefault(attribute, attr.default), constants.StatusCode.success

    def set_attribute(self, attribute: Any, value: Any) -> constants.StatusCode:
        try:
            attr = attributes.AttributesByID[attribute]
        except KeyError:
            return constants.StatusCode.error_nonsupported_attribute
        if not attr.in_resource(self.session_type):
            return constants.StatusCode.error_nonsupported_attribute
        if not attr.write:
            return constants.StatusCode.error_attribute_read_only
        self.attrs[attribute] = value
        return constants.StatusCode.success

    @property
    def bench(self) -> Session:
        return self.lib.bench


class SimuBenchVisaLibrary(highlevel.VisaLibraryBase):
    """VISA library backed by a SimuBench session."""

    bench: Session

    @staticmethod
    def get_library_paths() -> tuple[LibraryPath, ...]:
        return (LibraryPath("unset"),)

    @staticmethod
    def get_debug_info() -> dict[str, str]:
        from .. import __version__

        return OrderedDict(Version=__version__)

    # ---- library lifecycle ------------------------------------------------------------------------

    def _init(self) -> None:
        self.sessions: dict[int, Any] = {}
        spec = str(self.library_path)
        if spec in ("", "unset"):
            spec = os.environ.get("SIMUBENCH_BENCH", "")
        if not spec:
            raise ValueError(
                "SimuBench needs a bench file: ResourceManager('path/to/bench.yaml@simubench') "
                "or set SIMUBENCH_BENCH"
            )
        self.bench = open_bench(spec, record_dir=os.environ.get("SIMUBENCH_RECORD_DIR") or None)
        # Address table: canonical virtual address, configured real address and instance name.
        self.addresses: dict[str, str] = {}  # normalized address/alias -> instrument
        self.canonical: dict[str, str] = {}  # instrument -> canonical address
        for i, (name, inst) in enumerate(self.bench.instruments.items()):
            canon = f"TCPIP0::127.0.0.1::{BASE_PORT + i}::SOCKET"
            self.canonical[name] = canon
            self.addresses[self._norm(canon)] = name
            if inst.instance.address:
                self.addresses[self._norm(inst.instance.address)] = name
            self.addresses[name.upper()] = name

    @staticmethod
    def _norm(address: str) -> str:
        try:
            return str(rname.parse_resource_name(address)).upper()
        except rname.InvalidResourceName:
            return address.upper()

    def _lookup(self, resource_name: str) -> tuple[str, rname.ResourceName] | None:
        name = self.addresses.get(resource_name.upper()) or self.addresses.get(self._norm(resource_name))
        if name is None:
            return None
        target = resource_name
        try:
            parsed = rname.parse_resource_name(target)
        except rname.InvalidResourceName:
            target = self.canonical[name]
            parsed = rname.parse_resource_name(target)
        return name, parsed

    def _register(self, obj: Any) -> Any:
        handle = None
        while handle is None or handle in self.sessions:
            handle = random.randint(1_000_000, 9_999_999)
        self.sessions[handle] = obj
        return handle

    def open_default_resource_manager(self) -> tuple[Any, constants.StatusCode]:
        return self._register(self), constants.StatusCode.success

    def list_resources(self, session: Any, query: str = "?*::INSTR") -> tuple[str, ...]:
        resources = list(self.canonical.values())
        for inst in self.bench.instruments.values():
            if inst.instance.address:
                resources.append(inst.instance.address)
        return tuple(rname.filter(resources, query))

    def parse_resource_extended(
        self, session: Any, resource_name: str
    ) -> tuple[highlevel.ResourceInfo, constants.StatusCode]:
        hit = self._lookup(resource_name)
        if hit is None:
            return super().parse_resource_extended(session, resource_name)
        _, parsed = hit
        return (
            highlevel.ResourceInfo(
                parsed.interface_type_const,
                parsed.board,
                parsed.resource_class,
                str(parsed),
                None,
            ),
            constants.StatusCode.success,
        )

    def open(
        self, session: Any, resource_name: str, access_mode: Any = None, open_timeout: Any = None
    ) -> tuple[Any, constants.StatusCode]:
        hit = self._lookup(resource_name)
        if hit is None:
            return 0, constants.StatusCode.error_resource_not_found
        name, parsed = hit
        return self._register(_VisaSession(self, name, str(parsed), parsed)), constants.StatusCode.success

    def close(self, session: Any) -> constants.StatusCode:
        obj = self.sessions.pop(session, None)
        if obj is None:
            return constants.StatusCode.error_invalid_object
        if obj is self:
            self.bench.close()
        return constants.StatusCode.success

    # ---- I/O ----------------------------------------------------------------------------------------

    def _session(self, session: Any) -> _VisaSession | None:
        s = self.sessions.get(session)
        return s if isinstance(s, _VisaSession) else None

    def write(self, session: Any, data: bytes) -> tuple[int, constants.StatusCode]:
        s = self._session(session)
        if s is None:
            return 0, constants.StatusCode.error_invalid_object
        s.buffer.clear()
        try:
            self.bench.write(s.instrument, bytes(data), client="pyvisa")
        except LinkDown:
            raise errors.VisaIOError(constants.VI_ERROR_CONN_LOST) from None
        return len(data), constants.StatusCode.success

    def read(self, session: Any, count: int) -> tuple[bytes, constants.StatusCode]:
        s = self._session(session)
        if s is None:
            return b"", constants.StatusCode.error_invalid_object
        if not s.buffer:
            try:
                msg = self.bench.read(s.instrument, client="pyvisa")
            except LinkDown:
                raise errors.VisaIOError(constants.VI_ERROR_CONN_LOST) from None
            if msg is None:
                timeout_ms, _ = s.get_attribute(constants.ResourceAttribute.timeout_value)
                self._spend_timeout(timeout_ms)
                raise errors.VisaIOError(constants.VI_ERROR_TMO)
            s.buffer.extend(msg)
            s.end_pending = True  # type: ignore[attr-defined]
        termchar_enabled, _ = s.get_attribute(constants.ResourceAttribute.termchar_enabled)
        termchar, _ = s.get_attribute(constants.ResourceAttribute.termchar)
        out = bytearray()
        while s.buffer and len(out) < count:
            b = s.buffer.pop(0)
            out.append(b)
            if termchar_enabled and b == termchar:
                return bytes(out), constants.StatusCode.success_termination_character_read
        if not s.buffer:
            return bytes(out), constants.StatusCode.success  # END with the last byte
        return bytes(out), constants.StatusCode.success_max_count_read

    def _spend_timeout(self, timeout_ms: Any) -> None:
        try:
            ms = float(timeout_ms)
        except (TypeError, ValueError):
            ms = 2000.0
        if ms == float("inf") or ms <= 0:
            return
        if self.bench.clock_mode == "wall":
            time.sleep(ms / 1000)
        else:
            self.bench.advance(ms / 1000)

    def clear(self, session: Any) -> constants.StatusCode:
        s = self._session(session)
        if s is None:
            return constants.StatusCode.error_invalid_object
        s.buffer.clear()
        inst = self.bench.instrument(s.instrument)
        while inst.take_output() is not None:
            pass
        return constants.StatusCode.success

    def read_stb(self, session: Any) -> tuple[int, constants.StatusCode]:
        s = self._session(session)
        if s is None:
            return 0, constants.StatusCode.error_invalid_object
        return self.bench.instrument(s.instrument).stb(), constants.StatusCode.success

    def flush(self, session: Any, mask: Any) -> constants.StatusCode:
        return constants.StatusCode.success

    def assert_trigger(self, session: Any, protocol: Any) -> constants.StatusCode:
        s = self._session(session)
        if s is None:
            return constants.StatusCode.error_invalid_object
        self.bench.write(s.instrument, b"*TRG", client="pyvisa")
        return constants.StatusCode.success

    # ---- attributes -------------------------------------------------------------------------------

    def get_attribute(self, session: Any, attribute: Any) -> tuple[Any, constants.StatusCode]:
        s = self._session(session)
        if s is None:
            return 0, constants.StatusCode.error_invalid_object
        return s.get_attribute(attribute)

    def set_attribute(self, session: Any, attribute: Any, attribute_state: Any) -> constants.StatusCode:
        s = self._session(session)
        if s is None:
            return constants.StatusCode.error_invalid_object
        return s.set_attribute(attribute, attribute_state)

    def lock(
        self, session: Any, lock_type: Any, timeout: Any, requested_key: Any = None
    ) -> tuple[str, constants.StatusCode]:
        return requested_key or "", constants.StatusCode.success

    def unlock(self, session: Any) -> constants.StatusCode:
        return constants.StatusCode.success

    def disable_event(self, session: Any, event_type: Any, mechanism: Any) -> constants.StatusCode:
        return constants.StatusCode.success

    def discard_events(self, session: Any, event_type: Any, mechanism: Any) -> constants.StatusCode:
        return constants.StatusCode.success


WRAPPER_CLASS = SimuBenchVisaLibrary
