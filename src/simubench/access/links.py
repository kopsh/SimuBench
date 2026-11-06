"""What one VISA session can do on the bench's buses: the links (D63).

A VISA session is one script's handle on one resource. The PyVISA backend keeps what VISA keeps
per session (buffers, attributes, timeouts); everything the *bus* does for it is a :class:`Link`:
a message goes out, a response comes in, the device is cleared, triggered or polled, REN and the
locks are set. Four kinds exist:

* :class:`NetLink` — TCPIP (HiSLIP, VXI-11, raw socket) and instrument names;
* :class:`GpibLink` — a ``GPIB<n>::<pad>::INSTR`` resource on the virtual bus of gpib_bus.py;
* :class:`UsbLink` — a ``USB…::INSTR`` resource, a USBTMC/USB488 interface of usbtmc.py;
* :class:`GpibIntfcLink` — ``GPIB<n>::INTFC``, the board itself.

The methods are the whole surface, and their arguments and results are plain data, so a link can
be driven from another process: the connected mode (bench_link.py) forwards the same calls to the
served bench. Errors are :class:`~simubench.access.bus.BusError` with the VISA status in ``code``.
"""

from __future__ import annotations

import time
from typing import Any

from pyvisa import constants

from ..engine.session import Session
from .bus import BusError, LockTable, bus_of, observe
from .gpib_bus import GET, GpibBus, gpib_board
from .usbtmc import UsbError, UsbStall, UsbtmcInterface, usb_interface

#: What an infinite VISA timeout (0xFFFFFFFF ms) is worth: a wait that never ends would hang a
#: script on a bench whose request line can never be raised.
INFINITE_WAIT_S = 60.0

# VI_ATTR_GPIB_* values for what a virtual bus has no handshake to show.
_LINE = constants.LineState


def wait_seconds(timeout_ms: Any, default_ms: float = 2000.0) -> float:
    try:
        ms = float(timeout_ms)
    except (TypeError, ValueError):
        ms = default_ms
    return INFINITE_WAIT_S if ms >= 0xFFFFFFFF else max(ms, 0.0) / 1000


class Link:
    """The common part: locks, time, and waiting for a service request."""

    kind = "base"

    def __init__(self, session: Session, name: str | None, client: str = "pyvisa") -> None:
        self.session, self.name, self.client = session, name, client
        self.lock_depth = 0

    # ---- locks (VISA viLock, shared with every other interface of the instrument) --------------------

    def _locks(self) -> LockTable:
        assert self.name is not None
        return bus_of(self.session, self.name).locks

    def guard(self) -> None:
        """I/O on a resource another session has locked fails (VPP-4.3 viLock)."""
        if not self._locks().accessible(self):
            raise BusError(constants.VI_ERROR_RSRC_LOCKED, "the resource is locked by another session")

    def lock(self, exclusive: bool, key: str, timeout_ms: Any) -> None:
        locks = self._locks()
        granted = locks.grant(self, exclusive, key.encode())
        if not granted and self.lock_depth and locks.held_by(self):
            # Nested viLock: the same kind of lock (and key) the session already holds counts up.
            granted = (locks.exclusive is self) == exclusive and (
                exclusive or locks.shared_key == key.encode()
            )
        if not granted:
            self.spend_timeout(timeout_ms)  # the resource stayed locked for the whole wait
            raise BusError(constants.VI_ERROR_TMO, "the resource stayed locked")
        self.lock_depth += 1

    def unlock(self) -> None:
        if not self.lock_depth:
            raise BusError(constants.VI_ERROR_SESN_NLOCKED, "this session holds no lock")
        self.lock_depth -= 1
        if not self.lock_depth:
            self._locks().release(self)

    def close(self) -> None:
        if self.lock_depth and self.name is not None:
            self._locks().release(self)  # a closed session holds no lock
            self.lock_depth = 0

    # ---- time -----------------------------------------------------------------------------------------------

    def spend_timeout(self, timeout_ms: Any) -> None:
        """A wait that ended in a timeout took that long: virtual time on a command clock."""
        seconds = wait_seconds(timeout_ms)
        if seconds <= 0 or seconds >= INFINITE_WAIT_S:
            return
        self.pass_time(seconds)

    def pass_time(self, seconds: float) -> None:
        if self.session.clock_mode == "wall":
            time.sleep(seconds)
        else:
            self.session.advance(seconds)

    def wait_srq(self, timeout_ms: Any) -> bool:
        """Let time pass until a service request is there or the timeout is spent.

        The SRQ of ``*OPC`` after a long operation appears only as the clock advances, so it is
        looked for between steps, not once; on a command clock the wait costs what the instrument
        needed, no more."""
        remaining = wait_seconds(timeout_ms)
        step = max(remaining / 200, 1e-3)
        while True:
            if self.srq_ready():
                return True
            if remaining <= 0:
                return False
            dt = min(step, remaining)
            self.pass_time(dt)
            remaining -= dt

    # ---- the operations every kind answers ------------------------------------------------------------------

    def consume_srq(self) -> None:
        """A wait for SERVICE_REQUEST took the request it saw (only USB488 queues them)."""

    def write(self, data: bytes) -> int:
        raise NotImplementedError

    def read_message(self) -> bytes | None:
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError

    def trigger(self) -> None:
        raise NotImplementedError

    def read_stb(self) -> int:
        raise NotImplementedError

    def control_ren(self, mode: int) -> None:
        raise NotImplementedError

    def srq_ready(self) -> bool:
        raise NotImplementedError

    def attribute(self, attribute: int) -> Any:
        """The current value of a VISA attribute that the bus decides, or ``None``."""
        return None

    # operations only some kinds have: the others refuse them as VISA does
    def gpib_command(self, data: bytes) -> int:
        raise BusError(constants.VI_ERROR_NSUP_OPER, "not a GPIB interface")

    def gpib_send_ifc(self) -> None:
        raise BusError(constants.VI_ERROR_NSUP_OPER, "not a GPIB interface")

    def gpib_control_atn(self, mode: int) -> None:
        raise BusError(constants.VI_ERROR_NSUP_OPER, "not a GPIB interface")

    def gpib_pass_control(self, primary: int, secondary: int) -> None:
        raise BusError(constants.VI_ERROR_NSUP_OPER, "not a GPIB interface")

    def usb_control(
        self, request_type: int, request: int, value: int, index: int, length: int, data: bytes
    ) -> bytes:
        raise BusError(constants.VI_ERROR_NSUP_OPER, "not a USB interface")


# ---- network resources and instrument names -------------------------------------------------------------------


class NetLink(Link):
    """TCPIP INSTR (HiSLIP, VXI-11), TCPIP SOCKET and a bare instrument name."""

    kind = "net"

    def write(self, data: bytes) -> int:
        assert self.name is not None
        self.guard()
        self.session.write(self.name, bytes(data), client=self.client)
        return len(data)

    def read_message(self) -> bytes | None:
        assert self.name is not None
        self.guard()
        return self.session.read(self.name, client=self.client)

    def clear(self) -> None:
        assert self.name is not None
        self.guard()
        self.session.clear_output(self.name, client=self.client)

    def trigger(self) -> None:
        assert self.name is not None
        self.guard()
        self.session.write(self.name, b"*TRG", client=self.client)

    def read_stb(self) -> int:
        assert self.name is not None
        self.guard()
        return self.session.instrument(self.name).stb()

    def control_ren(self, mode: int) -> None:
        assert self.name is not None
        self.guard()
        try:
            action = bus_of(self.session, self.name).remote_local.control(int(mode))
        except (KeyError, ValueError):
            raise BusError(constants.VI_ERROR_INV_MODE, f"unknown REN mode {mode}") from None
        self.session.emit(
            {"kind": "bus_control", "instrument": self.name, "client": self.client, "action": action}
        )

    def srq_ready(self) -> bool:
        assert self.name is not None
        return bool(self.session.instrument(self.name).stb() & 0x40)


# ---- GPIB ---------------------------------------------------------------------------------------------------------


class GpibLink(Link):
    """``GPIB<n>::<pad>::INSTR``: every operation is what VISA does on the virtual bus for it."""

    kind = "gpib"

    def __init__(self, session: Session, name: str, board: int, client: str = "pyvisa") -> None:
        super().__init__(session, name, client)
        self.bus: GpibBus = gpib_board(session, board)

    def write(self, data: bytes) -> int:
        assert self.name is not None
        self.guard()
        self.bus.write(self.name, bytes(data), self.client)
        return len(data)

    def read_message(self) -> bytes | None:
        assert self.name is not None
        self.guard()
        self.bus.address_for_read(self.name, self.client)
        return self.bus.take(self.name, self.client)

    def clear(self) -> None:
        assert self.name is not None
        self.guard()
        self.bus.device_clear(self.name, self.client)

    def trigger(self) -> None:
        assert self.name is not None
        self.guard()
        self.bus.trigger(self.name, self.client)

    def read_stb(self) -> int:
        assert self.name is not None
        self.guard()
        return self.bus.read_stb(self.name, self.client)

    def control_ren(self, mode: int) -> None:
        assert self.name is not None
        self.guard()
        self.bus.control_ren(self.name, int(mode), self.client)

    def srq_ready(self) -> bool:
        assert self.name is not None
        observe(self.session, self.name)
        return bus_of(self.session, self.name).service_request.latch

    def attribute(self, attribute: int) -> Any:
        a = constants.ResourceAttribute
        assert self.name is not None
        if attribute == a.gpib_ren_state:
            return _LINE.asserted if self.bus.ren else _LINE.unasserted
        return _gpib_attribute(self.bus, self.name, attribute)


class GpibIntfcLink(Link):
    """``GPIB<n>::INTFC``: the board. It commands the bus, and writes to and reads from whoever is addressed."""

    kind = "gpib_intfc"

    def __init__(self, session: Session, board: int, client: str = "pyvisa") -> None:
        super().__init__(session, None, client)
        self.bus: GpibBus = gpib_board(session, board)

    def _locks(self) -> LockTable:
        return self.bus.locks

    def write(self, data: bytes) -> int:
        self.guard()
        self.bus.deliver(bytes(data), self.client)
        return len(data)

    def read_message(self) -> bytes | None:
        self.guard()
        return self.bus.take(None, self.client)

    def clear(self) -> None:
        self.guard()
        self.bus.send_ifc(self.client)  # viClear on an interface session asserts IFC

    def trigger(self) -> None:
        self.guard()
        self.bus.command(bytes([GET]), self.client)  # GET to whoever is addressed to listen

    def read_stb(self) -> int:
        raise BusError(constants.VI_ERROR_NSUP_OPER, "an interface has no status byte")

    def control_ren(self, mode: int) -> None:
        self.guard()
        self.bus.set_ren(int(mode), self.client)

    def srq_ready(self) -> bool:
        return self.bus.srq

    def gpib_command(self, data: bytes) -> int:
        self.guard()
        return self.bus.command(bytes(data), self.client)

    def gpib_send_ifc(self) -> None:
        self.guard()
        self.bus.send_ifc(self.client)

    def gpib_control_atn(self, mode: int) -> None:
        self.guard()
        self.bus.set_atn(int(mode))

    def gpib_pass_control(self, primary: int, secondary: int) -> None:
        # No simulated device can take control (C0): the pass can only fail.
        raise BusError(constants.VI_ERROR_NSUP_OPER, "no device on this bus can take control")

    def attribute(self, attribute: int) -> Any:
        a = constants.ResourceAttribute
        if attribute == a.gpib_ren_state:
            return _LINE.asserted if self.bus.ren else _LINE.unasserted
        if attribute == a.gpib_primary_address:
            return self.bus.controller_pad
        return _gpib_attribute(self.bus, None, attribute)


def _gpib_attribute(bus: GpibBus, name: str | None, attribute: int) -> Any:
    a = constants.ResourceAttribute
    if attribute == a.gpib_address_state:
        return bus.address_state(name)
    if attribute == a.gpib_atn_state:
        return _LINE.asserted if bus.atn else _LINE.unasserted
    if attribute == a.gpib_srq_state:
        return _LINE.asserted if bus.srq else _LINE.unasserted
    if attribute == a.gpib_ndac_state:
        return _LINE.unknown  # there is no handshake to look at
    if attribute in (a.gpib_cic_state, a.gpib_system_controller):
        return constants.VI_TRUE  # the board is the controller in charge and the system controller
    if attribute == a.gpib_hs488_cable_length:
        return constants.VI_GPIB_HS488_NIMPL
    return None


# ---- USB ---------------------------------------------------------------------------------------------------------


class UsbLink(Link):
    """``USB…::INSTR``: a USBTMC interface with the USB488 subclass."""

    kind = "usb"

    def __init__(self, session: Session, name: str, client: str = "pyvisa") -> None:
        super().__init__(session, name, client)
        self.iface: UsbtmcInterface = usb_interface(session, name)

    def write(self, data: bytes) -> int:
        self.guard()
        return self.iface.write(bytes(data), self.client)

    def read_message(self) -> bytes | None:
        self.guard()
        try:
            data, _eom = self.iface.read(1 << 20, None, self.client)
        except UsbError as exc:
            if exc.code == constants.VI_ERROR_TMO:
                self.iface.in_active = False  # the host gave up waiting: nothing is left half done
                return None
            raise
        return data

    def clear(self) -> None:
        self.guard()
        self.iface.clear(self.client)

    def trigger(self) -> None:
        self.guard()
        self.iface.trigger(self.client)

    def read_stb(self) -> int:
        self.guard()
        return self.iface.read_stb(self.client)

    def control_ren(self, mode: int) -> None:
        self.guard()
        self.iface.control_ren(int(mode), self.client)

    def srq_ready(self) -> bool:
        return self.iface.srq_pending()

    def consume_srq(self) -> None:
        self.iface.take_srq()  # the interrupt packet is taken off the queue

    def usb_control(
        self, request_type: int, request: int, value: int, index: int, length: int, data: bytes
    ) -> bytes:
        self.guard()
        try:
            return self.iface.control_request(
                request_type, request, value, index, length or len(data), self.client
            )
        except UsbStall as exc:  # a STALL handshake: VISA reports an I/O error
            raise BusError(constants.VI_ERROR_IO, str(exc)) from None

    def attribute(self, attribute: int) -> Any:
        """The attributes of a USB INSTR session (the pipe ones belong to USB RAW sessions).

        The manufacturer and model names are the first two fields of the instrument's ``*IDN?``,
        which is what the USB string descriptors of a real instrument say too."""
        a = constants.ResourceAttribute
        if attribute in (a.manufacturer_name, a.model_name):
            fields = [f.strip() for f in self.session.instrument(self.name).idn.split(",")]  # type: ignore[arg-type]
            return fields[0 if attribute == a.manufacturer_name else 1] if len(fields) > 1 else None
        if attribute == a.usb_max_interrupt_size:
            return 8  # an Interrupt-IN packet is two bytes (USB488 tables 6 and 7)
        return None


def make_link(session: Session, name: str, kind: str, board: int = 0, client: str = "pyvisa") -> Link:
    """The link for a resource of ``kind``: ``net``, ``gpib``, ``gpib_intfc`` or ``usb``."""
    if kind == "gpib":
        return GpibLink(session, name, board, client)
    if kind == "gpib_intfc":
        return GpibIntfcLink(session, board, client)
    if kind == "usb":
        return UsbLink(session, name, client)
    return NetLink(session, name, client)
