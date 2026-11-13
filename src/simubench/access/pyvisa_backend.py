"""PyVISA backend: ``pyvisa.ResourceManager("bench.yaml@simubench")`` (D5, architecture.md 7.3).

The bench opens in this process. Every instrument answers at:

* its virtual address ``TCPIP0::127.0.0.1::<5025+i>::SOCKET`` (i = position in the bench file),
* the HiSLIP form ``...::hislip<i>,4880::INSTR`` and the VXI-11 form ``...::inst<i>::INSTR`` (D41, D63),
* the real-bench address configured in the bench file (``address:``), so scripts keep their strings
  (``GPIB0::4::INSTR`` and ``USB0::0x…::INSTR`` behave as those buses do, ``GPIB0::INTFC`` is the board),
* its instance name (``rm.open_resource("scope")``).

With ``PYVISA_LIBRARY=<host>:<port>@simubench`` (or ``SIMUBENCH_LINK``) the bench is not opened here
but is the one a service is running: the same addresses and the same behaviour, in the service
(bench_link.py), so the Web Bench or the TUI can watch the script work.

What a session does on its bus is a :mod:`~simubench.access.links` link; this module keeps what VISA
keeps per session: the read buffer, the attributes, the timeouts.

Reads follow VISA rules: a read ends at the termination character when it is enabled (so a binary
block containing 0x0A is cut short, exactly as on hardware), at the end of a response message, or
when ``count`` bytes have been read. A read with nothing queued is a timeout.
"""

from __future__ import annotations

import os
import random
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any

from pyvisa import attributes, constants, errors, highlevel, rname
from pyvisa.util import LibraryPath

from ..api import bench_files_dir, open_bench
from ..engine.faults import LinkDown
from ..engine.session import Session
from .bench_link import DEFAULT_PORT as LINK_PORT
from .bench_link import RemoteBench
from .bus import BusError
from .gpib_bus import boards_of
from .links import Link, make_link

BASE_PORT = 5025
HISLIP_PORT = 4880
INTFC = "<intfc>"  # what the address table names for a board: it is no instrument


def _kind_of(parsed: rname.ResourceName) -> str:
    if isinstance(parsed, rname.GPIBIntfc):
        return "gpib_intfc"
    if isinstance(parsed, rname.GPIBInstr):
        return "gpib"
    if isinstance(parsed, rname.USBInstr):
        return "usb"
    return "net"


#: The bench configuration one resource string (or the environment, as the fallback) may carry:
#: query parameter -> the environment variable it overrides (issue 640).
OPTIONS = {
    "clock": "SIMUBENCH_CLOCK",
    "seed": "SIMUBENCH_SEED",
    "idle": "SIMUBENCH_IDLE",
    "record_dir": "SIMUBENCH_RECORD_DIR",
}


def _parse_spec(spec: str) -> tuple[str, dict[str, str]]:
    """Split a library path into the bench file and its query parameters: ``bench.yaml?seed=7``
    (the ``@simubench`` backend part PyVISA has already taken off). A parameter this table does
    not name is an error, not a silent default — a typo'd ``cloc=wall`` would otherwise run the
    bench under defaults and look like a modeling bug."""
    path, sep, query = spec.partition("?")
    if not sep:
        return spec, {}
    from urllib.parse import parse_qsl

    params = dict(parse_qsl(query, keep_blank_values=True))
    unknown = sorted(set(params) - set(OPTIONS))
    if unknown:
        message = (
            f"unknown SimuBench option(s) in the resource string: {', '.join(unknown)}"
            f" — known ones: {', '.join(sorted(OPTIONS))}"
        )
        # A real option name never carries a path separator or a '?': what did is almost surely
        # the bench path itself, whose literal '?' collided with the query separator (issue 675).
        if any(ch in name for name in unknown for ch in "/\\?"):
            message += (
                "\n(the path contains a literal '?', which starts the query — rename the file or folder)"
            )
        raise ValueError(message)
    return path, params


class _VisaSession:
    def __init__(
        self,
        lib: SimuBenchVisaLibrary,
        instrument: str,
        resource_name: str,
        parsed: rname.ResourceName,
        link: Link,
    ):
        self.lib = lib
        self.instrument = None if instrument == INTFC else instrument
        self.parsed = parsed
        self.link = link
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
        elif isinstance(parsed, rname.TCPIPInstr):
            # A TCPIP INSTR resource is a HiSLIP address (D41); fill in its VISA attributes.
            # VI_ATTR_TCPIP_PORT is not valid on INSTR sessions in PyVISA's table: the port
            # lives inside the LAN device name.
            sub = parsed.lan_device_name.partition(",")[0]
            self.attrs[constants.ResourceAttribute.tcpip_address] = parsed.host_address
            self.attrs[constants.ResourceAttribute.tcpip_hostname] = parsed.host_address
            self.attrs[constants.ResourceAttribute.tcpip_device_name] = parsed.lan_device_name
            hislip = sub.lower().startswith("hislip")
            self.attrs[constants.ResourceAttribute.tcpip_is_hislip] = hislip
            if hislip:
                self.attrs[constants.ResourceAttribute.tcpip_hislip_version] = 0x0010_0000
        elif isinstance(parsed, rname.GPIBInstr):
            # The address is what the script wrote; nothing is invented (D63).
            self.attrs[constants.ResourceAttribute.gpib_primary_address] = int(parsed.primary_address)
            self.attrs[constants.ResourceAttribute.gpib_secondary_address] = (
                int(parsed.secondary_address)
                if parsed.secondary_address is not None
                else constants.VI_NO_SEC_ADDR
            )
        elif isinstance(parsed, rname.GPIBIntfc):
            self.attrs[constants.ResourceAttribute.gpib_secondary_address] = constants.VI_NO_SEC_ADDR
        elif isinstance(parsed, rname.USBInstr):
            self.attrs[constants.ResourceAttribute.manufacturer_id] = int(parsed.manufacturer_id, 0)
            self.attrs[constants.ResourceAttribute.model_code] = int(parsed.model_code, 0)
            self.attrs[constants.ResourceAttribute.usb_serial_number] = parsed.serial_number
            self.attrs[constants.ResourceAttribute.usb_interface_number] = int(
                parsed.usb_interface_number or 0
            )
            self.attrs[constants.ResourceAttribute.usb_class] = 0xFE  # application specific
            self.attrs[constants.ResourceAttribute.usb_subclass] = 0x03  # USBTMC
            self.attrs[constants.ResourceAttribute.usb_protocol] = 1  # USB488
        self.buffer = bytearray()  # bytes of the current response message not yet read
        self.connected = True
        self.srq_queue = False  # SERVICE_REQUEST enabled with the queue mechanism

    def get_attribute(self, attribute: Any) -> tuple[Any, constants.StatusCode]:
        try:
            attr = attributes.AttributesByID[attribute]
        except KeyError:
            return 0, constants.StatusCode.error_nonsupported_attribute
        if not attr.in_resource(self.session_type):
            return 0, constants.StatusCode.error_nonsupported_attribute
        live = self.link.attribute(attribute)  # what the bus decides: line states, pipe status ...
        if live is not None:
            return live, constants.StatusCode.success
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
    def bench(self) -> Session | RemoteBench:
        return self.lib.bench


class SimuBenchVisaLibrary(highlevel.VisaLibraryBase):
    """VISA library backed by a SimuBench session."""

    bench: Session | RemoteBench

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
            spec = os.environ.get("SIMUBENCH_BENCH") or os.environ.get("SIMUBENCH_LINK") or ""
        if not spec:
            spec = f"127.0.0.1:{LINK_PORT}"  # a service started with `simubench serve` listens here
        self._spec, self._params = _parse_spec(spec)
        self._start()

    @staticmethod
    def _is_link(spec: str) -> bool:
        return re.fullmatch(r"[\w.\-]*:\d+", spec) is not None and not os.path.exists(spec)

    def _start(self) -> None:
        """Open the bench (a fresh session, as a power-cycled bench would be) or connect to a service's.

        The resource string's query parameters (``bench.yaml?seed=7@simubench``) win over the
        environment variables, which stay as the fallback a plain ``SIMUBENCH_BENCH`` setup uses."""

        def option(name: str) -> str | None:
            return self._params.get(name) or os.environ.get(OPTIONS[name])

        seed = option("seed")
        if self._is_link(self._spec):
            try:
                self.bench = RemoteBench(self._spec)
            except (OSError, BusError) as exc:
                raise ValueError(
                    f"no SimuBench service answers at {self._spec} ({exc}); start `simubench serve <bench>`, "
                    "or give a bench file: ResourceManager('path/to/bench.yaml@simubench')"
                ) from None
        else:
            self.bench = open_bench(
                self._spec,
                record_dir=option("record_dir"),
                clock=option("clock"),
                seed=int(seed) if seed else None,
                idle=option("idle"),
                # the instruments' files stay next to the bench between runs (#1069)
                files_dir=bench_files_dir(Path(self._spec).resolve().parent, Path(self._spec)),
            )
        self._closed = False
        # Address table: canonical virtual address, configured real address and instance name.
        # HiSLIP addresses resolve to the same in-process instruments (D41).
        self.addresses: dict[str, str] = {}  # normalized address/alias -> instrument
        self.canonical: dict[str, str] = {}  # instrument -> canonical socket address
        self.hislip: dict[str, str] = {}  # instrument -> canonical HiSLIP address
        self.vxi11: dict[str, str] = {}  # instrument -> canonical VXI-11 address
        self.vxi11_gateway: dict[str, str] = {}  # instrument -> VXI-11 gateway form of its GPIB address
        self.served: list[str] = []  # addresses a connected-to service answers at
        for i, (name, inst) in enumerate(self.bench.instruments.items()):
            canon = f"TCPIP0::127.0.0.1::{BASE_PORT + i}::SOCKET"
            self.canonical[name] = canon
            self.addresses[self._norm(canon)] = name
            for form in (
                f"TCPIP0::127.0.0.1::hislip{i},{HISLIP_PORT}::INSTR",
                f"TCPIP0::127.0.0.1::hislip{i}::INSTR",  # the default port, left unwritten
                f"TCPIP0::127.0.0.1::hislip_{name},{HISLIP_PORT}::INSTR",
                f"TCPIP0::127.0.0.1::hislip_{name}::INSTR",
            ):
                self.addresses[self._norm(form)] = name
            self.hislip[name] = f"TCPIP0::127.0.0.1::hislip{i},{HISLIP_PORT}::INSTR"
            # VXI-11 (D63): the plain address a VISA library resolves through the port mapper.
            self.vxi11[name] = f"TCPIP0::127.0.0.1::inst{i}::INSTR"
            for form in (self.vxi11[name], f"TCPIP0::127.0.0.1::inst_{name}::INSTR"):
                self.addresses[self._norm(form)] = name
            gateway = self._gateway_address(inst.instance.address)
            if gateway:  # a declared GPIB instrument is also reachable through a LAN/GPIB gateway
                self.addresses[self._norm(gateway)] = name
                self.vxi11_gateway[name] = gateway
            if inst.instance.address:
                self.addresses[self._norm(inst.instance.address)] = name
            for served in getattr(inst, "served", []):  # a service's own ports (connected mode)
                self.addresses[self._norm(served)] = name
                self.served.append(served)
            self.addresses[name.upper()] = name
        # The board of every GPIB address the bench declares can be opened as an interface (D63).
        self.intfc = [f"GPIB{board}::INTFC" for board in boards_of(self.bench)]
        for resource in self.intfc:
            self.addresses[self._norm(resource)] = INTFC

    @staticmethod
    def _gateway_address(address: str | None) -> str | None:
        """``GPIB0::4::INSTR`` -> ``TCPIP0::127.0.0.1::gpib0,4::INSTR`` (nothing for other buses)."""
        try:
            parsed = rname.parse_resource_name(address or "")
        except rname.InvalidResourceName:
            return None
        if not isinstance(parsed, rname.GPIBInstr):
            return None
        sad = f",{parsed.secondary_address}" if parsed.secondary_address is not None else ""
        return f"TCPIP0::127.0.0.1::gpib{parsed.board},{parsed.primary_address}{sad}::INSTR"

    @staticmethod
    def _norm(address: str) -> str:
        # `TCPIP0::127.0.0.1,<port>::inst0::INSTR` is what `serve` prints when no port mapper is on
        # 111; the port is random per run, so in process it is not part of the name.
        address = re.sub(r"^(TCPIP\d*::[^:,]+),\d+(::(?:inst|gpib))", r"\1\2", address, flags=re.IGNORECASE)
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
        # PyVISA keeps one library object per bench path; a resource manager opened after the
        # previous one closed gets a new session, so tests do not inherit each other's state.
        if self._closed:
            self._start()
        return self._register(self), constants.StatusCode.success

    def list_resources(self, session: Any, query: str = "?*::INSTR") -> tuple[str, ...]:
        resources = list(self.canonical.values()) + list(self.hislip.values()) + list(self.vxi11.values())
        resources += list(self.vxi11_gateway.values()) + self.intfc + self.served
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
            # PyVISA does not raise on a status returned by ``open``: reject here, as VISA does.
            raise errors.VisaIOError(constants.VI_ERROR_RSRC_NFOUND)
        name, parsed = hit
        kind, board = _kind_of(parsed), int(getattr(parsed, "board", 0) or 0)
        if isinstance(self.bench, RemoteBench):
            link: Link = self._run(self.bench.open_link, None if name == INTFC else name, kind, board)
        else:
            link = make_link(self.bench, name, kind, board)
        return self._register(
            _VisaSession(self, name, str(parsed), parsed, link)
        ), constants.StatusCode.success

    def close(self, session: Any) -> constants.StatusCode:
        obj = self.sessions.pop(session, None)
        if obj is None:
            return constants.StatusCode.error_invalid_object
        if isinstance(obj, _VisaSession):
            obj.link.close()  # a closed session holds no lock
        if obj is self:
            self.bench.close()
            self._closed = True
        return constants.StatusCode.success

    # ---- I/O ----------------------------------------------------------------------------------------

    def _session(self, session: Any) -> _VisaSession | None:
        s = self.sessions.get(session)
        return s if isinstance(s, _VisaSession) else None

    @staticmethod
    def _run(fn: Any, *args: Any) -> Any:
        """Call a link; what the bus reports becomes the VISA error a script sees."""
        try:
            return fn(*args)
        except BusError as exc:
            raise errors.VisaIOError(exc.code) from None
        except LinkDown:
            raise errors.VisaIOError(constants.VI_ERROR_CONN_LOST) from None

    def write(self, session: Any, data: bytes) -> tuple[int, constants.StatusCode]:
        s = self._session(session)
        if s is None:
            return 0, constants.StatusCode.error_invalid_object
        s.buffer.clear()
        self._run(s.link.write, bytes(data))
        return len(data), constants.StatusCode.success

    def read(self, session: Any, count: int) -> tuple[bytes, constants.StatusCode]:
        s = self._session(session)
        if s is None:
            return b"", constants.StatusCode.error_invalid_object
        if not s.buffer:
            timed_out = False
            try:
                msg = self._run(s.link.read_message)
            except errors.VisaIOError as exc:
                if exc.error_code != constants.VI_ERROR_TMO:
                    raise
                msg, timed_out = None, True
            if msg is None:
                timeout_ms, _ = s.get_attribute(constants.ResourceAttribute.timeout_value)
                s.link.spend_timeout(timeout_ms)
                raise errors.VisaIOError(constants.VI_ERROR_TMO)
            assert not timed_out
            s.buffer.extend(msg)
            s.end_pending = True  # type: ignore[attr-defined]
        termchar_enabled, _ = s.get_attribute(constants.ResourceAttribute.termchar_enabled)
        termchar, _ = s.get_attribute(constants.ResourceAttribute.termchar)
        # Up to ``count`` bytes, stopping after the termination character; sliced, not popped one by
        # one, so that megabyte waveform blocks read in linear time.
        end = min(count, len(s.buffer))
        if termchar_enabled:
            stop = s.buffer.find(bytes([termchar]), 0, end)
            if stop != -1:
                out = bytes(s.buffer[: stop + 1])
                del s.buffer[: stop + 1]
                return out, constants.StatusCode.success_termination_character_read
        out = bytes(s.buffer[:end])
        del s.buffer[:end]
        if not s.buffer:
            return out, constants.StatusCode.success  # END with the last byte
        return out, constants.StatusCode.success_max_count_read

    def clear(self, session: Any) -> constants.StatusCode:
        s = self._session(session)
        if s is None:
            return constants.StatusCode.error_invalid_object
        s.buffer.clear()
        self._run(s.link.clear)
        return constants.StatusCode.success

    def read_stb(self, session: Any) -> tuple[int, constants.StatusCode]:
        s = self._session(session)
        if s is None:
            return 0, constants.StatusCode.error_invalid_object
        return self._run(s.link.read_stb), constants.StatusCode.success

    def flush(self, session: Any, mask: Any) -> constants.StatusCode:
        return constants.StatusCode.success

    def assert_trigger(self, session: Any, protocol: Any) -> constants.StatusCode:
        s = self._session(session)
        if s is None:
            return constants.StatusCode.error_invalid_object
        self._run(s.link.trigger)
        return constants.StatusCode.success

    # ---- remote/local control (REN, GTL, LLO) -------------------------------------------------------

    def gpib_control_ren(self, session: Any, mode: Any) -> constants.StatusCode:
        """viGpibControlREN: GPIB, USB488 and TCPIP INSTR sessions share one REN/GTL/LLO state."""
        s = self._session(session)
        if s is None:
            return constants.StatusCode.error_invalid_object
        if isinstance(s.parsed, rname.TCPIPSocket):
            raise errors.VisaIOError(constants.VI_ERROR_NSUP_OPER)
        self._run(s.link.control_ren, int(mode))
        return constants.StatusCode.success

    # ---- the buses' own operations ------------------------------------------------------------------

    def gpib_command(self, session: Any, data: bytes) -> tuple[int, constants.StatusCode]:
        s = self._session(session)
        if s is None:
            return 0, constants.StatusCode.error_invalid_object
        return self._run(s.link.gpib_command, bytes(data)), constants.StatusCode.success

    def gpib_send_ifc(self, session: Any) -> constants.StatusCode:
        s = self._session(session)
        if s is None:
            return constants.StatusCode.error_invalid_object
        self._run(s.link.gpib_send_ifc)
        return constants.StatusCode.success

    def gpib_control_atn(self, session: Any, mode: Any) -> constants.StatusCode:
        s = self._session(session)
        if s is None:
            return constants.StatusCode.error_invalid_object
        self._run(s.link.gpib_control_atn, int(mode))
        return constants.StatusCode.success

    def gpib_pass_control(
        self, session: Any, primary_address: int, secondary_address: int
    ) -> constants.StatusCode:
        s = self._session(session)
        if s is None:
            return constants.StatusCode.error_invalid_object
        self._run(s.link.gpib_pass_control, int(primary_address), int(secondary_address))
        return constants.StatusCode.success

    def usb_control_in(
        self,
        session: Any,
        request_type_bitmap_field: int,
        request_id: int,
        request_value: int,
        index: int,
        length: int = 0,
    ) -> tuple[bytes, constants.StatusCode]:
        s = self._session(session)
        if s is None:
            return b"", constants.StatusCode.error_invalid_object
        data = self._run(
            s.link.usb_control, request_type_bitmap_field, request_id, request_value, index, length, b""
        )
        return data, constants.StatusCode.success

    def usb_control_out(
        self,
        session: Any,
        request_type_bitmap_field: int,
        request_id: int,
        request_value: int,
        index: int,
        data: bytes = b"",
    ) -> constants.StatusCode:
        s = self._session(session)
        if s is None:
            return constants.StatusCode.error_invalid_object
        self._run(
            s.link.usb_control, request_type_bitmap_field, request_id, request_value, index, 0, bytes(data)
        )
        return constants.StatusCode.success

    # ---- resource locks ------------------------------------------------------------------------------

    def lock(
        self, session: Any, lock_type: Any, timeout: Any, requested_key: Any = None
    ) -> tuple[str, constants.StatusCode]:
        s = self._session(session)
        if s is None:
            return "", constants.StatusCode.error_invalid_object
        exclusive = lock_type == constants.Lock.exclusive
        if exclusive:
            key = ""
        else:
            self._key_counter = getattr(self, "_key_counter", 0) + 1
            key = requested_key or f"SIMUBENCH-KEY-{self._key_counter}"
        self._run(s.link.lock, exclusive, key, timeout)
        return key, constants.StatusCode.success

    def unlock(self, session: Any) -> constants.StatusCode:
        s = self._session(session)
        if s is None:
            return constants.StatusCode.error_invalid_object
        self._run(s.link.unlock)
        return constants.StatusCode.success

    # ---- service requests ------------------------------------------------------------------------------

    def enable_event(
        self, session: Any, event_type: Any, mechanism: Any, context: Any = None
    ) -> constants.StatusCode:
        s = self._session(session)
        if s is None:
            return constants.StatusCode.error_invalid_object
        if event_type != constants.EventType.service_request or mechanism not in (
            constants.EventMechanism.queue,
            constants.EventMechanism.all,
        ):
            raise errors.VisaIOError(constants.VI_ERROR_INV_EVENT)
        s.srq_queue = True
        return constants.StatusCode.success

    def wait_on_event(
        self, session: Any, in_event_type: Any, timeout: Any
    ) -> tuple[Any, Any, constants.StatusCode]:
        """Wait for SERVICE_REQUEST: the request line of the bus, or the Interrupt-IN packet of USB488."""
        s = self._session(session)
        if s is None:
            return 0, 0, constants.StatusCode.error_invalid_object
        if in_event_type != constants.EventType.service_request:
            raise errors.VisaIOError(constants.VI_ERROR_INV_EVENT)
        if not s.srq_queue:
            raise errors.VisaIOError(constants.VI_ERROR_NENABLED)
        if not self._run(s.link.wait_srq, timeout):
            raise errors.VisaIOError(constants.VI_ERROR_TMO)
        s.link.consume_srq()
        return in_event_type, self._register(object()), constants.StatusCode.success

    # ---- attributes -------------------------------------------------------------------------------

    def get_attribute(self, session: Any, attribute: Any) -> tuple[Any, constants.StatusCode]:
        s = self._session(session)
        if s is None:
            return 0, constants.StatusCode.error_invalid_object
        value, status = s.get_attribute(attribute)
        if (
            status < 0
        ):  # PyVISA does not raise on a returned status: an attribute the resource has not is an error
            raise errors.VisaIOError(status)
        return value, status

    def set_attribute(self, session: Any, attribute: Any, attribute_state: Any) -> constants.StatusCode:
        s = self._session(session)
        if s is None:
            return constants.StatusCode.error_invalid_object
        status = s.set_attribute(attribute, attribute_state)
        if status < 0:
            raise errors.VisaIOError(status)
        return status

    def disable_event(self, session: Any, event_type: Any, mechanism: Any) -> constants.StatusCode:
        return constants.StatusCode.success

    def discard_events(self, session: Any, event_type: Any, mechanism: Any) -> constants.StatusCode:
        return constants.StatusCode.success


WRAPPER_CLASS = SimuBenchVisaLibrary
