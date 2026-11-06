"""A virtual USBTMC / USB488 interface for each instrument with a USB address (D63).

What a script can observe of a USB instrument is simulated, following the USBTMC 1.00 and USB488
1.00 specifications (usb.org): the control-endpoint requests with their status codes and response
layouts, the split transactions (INITIATE then CHECK) of abort and clear, endpoint halts that only
``CLEAR_FEATURE(ENDPOINT_HALT)`` lifts, bTags, the Interrupt-IN endpoint that carries service
requests and the status byte, and the REN/GTL/LLO state machine (RL1).

The endpoint addresses and the interrupt FIFO depth are the simulated device's own choice; every
simulated instrument has the same ones. GET_CAPABILITIES reports what is modelled: INDICATOR_PULSE,
TermChar, 488.2, REN/GTL/LLO, TRIGGER, SR1, RL1, DT1; not talk-only, not listen-only, and not SCPI
(bit D3, which a device may only set when it understands every mandatory SCPI command).

Not modelled: standard descriptors (GET_DESCRIPTOR) and the rest of USB enumeration, which the host
stack does before VISA sees the device; a transfer is one message, so "a transfer in progress" is a
message waiting for its EOM (Bulk-OUT) or a REQUEST_DEV_DEP_MSG_IN waiting for its Bulk-IN.
"""

from __future__ import annotations

import struct
import threading
import weakref
from collections import deque
from dataclasses import dataclass, field

from pyvisa import constants

from ..engine.session import Session
from .bus import bus_of, observe, serial_poll

# USBTMC bRequest values (USBTMC table 15) and USB488 ones (USB488 table 9).
INITIATE_ABORT_BULK_OUT, CHECK_ABORT_BULK_OUT_STATUS = 1, 2
INITIATE_ABORT_BULK_IN, CHECK_ABORT_BULK_IN_STATUS = 3, 4
INITIATE_CLEAR, CHECK_CLEAR_STATUS, GET_CAPABILITIES = 5, 6, 7
INDICATOR_PULSE = 64
READ_STATUS_BYTE, REN_CONTROL, GO_TO_LOCAL, LOCAL_LOCKOUT = 128, 160, 161, 162

# USBTMC_status (USBTMC table 16, USB488 table 10).
STATUS_SUCCESS, STATUS_PENDING = 0x01, 0x02
STATUS_INTERRUPT_IN_BUSY = 0x20
STATUS_FAILED, STATUS_TRANSFER_NOT_IN_PROGRESS = 0x80, 0x81
STATUS_SPLIT_NOT_IN_PROGRESS, STATUS_SPLIT_IN_PROGRESS = 0x82, 0x83

# bmRequestType: device-to-host, class, to the interface (0xA1) or to an endpoint (0xA2).
CLASS_INTERFACE, CLASS_ENDPOINT = 0xA1, 0xA2
STD_ENDPOINT_OUT, STD_ENDPOINT_IN = 0x02, 0x82  # standard requests to an endpoint
STD_GET_STATUS, STD_CLEAR_FEATURE, FEATURE_ENDPOINT_HALT = 0, 1, 0

# MsgID of the bulk-out and bulk-in messages (USBTMC table 2, USB488 table 1).
DEV_DEP_MSG_OUT, REQUEST_DEV_DEP_MSG_IN, TRIGGER_MSG = 1, 2, 128

#: The simulated device's endpoints and the depth of its Interrupt-IN FIFO.
BULK_OUT, BULK_IN, INTERRUPT_IN = 0x02, 0x81, 0x83
INTERRUPT_FIFO = 8
INTERFACE_NUMBER = 0

#: Response length of each class request, to pad a STATUS_SPLIT_IN_PROGRESS answer to it.
RESPONSE_SIZE = {
    INITIATE_ABORT_BULK_OUT: 2,
    CHECK_ABORT_BULK_OUT_STATUS: 8,
    INITIATE_ABORT_BULK_IN: 2,
    CHECK_ABORT_BULK_IN_STATUS: 8,
    INITIATE_CLEAR: 1,
    CHECK_CLEAR_STATUS: 2,
    GET_CAPABILITIES: 24,
    INDICATOR_PULSE: 1,
    READ_STATUS_BYTE: 3,
    REN_CONTROL: 1,
    GO_TO_LOCAL: 1,
    LOCAL_LOCKOUT: 1,
}
#: The requests addressed to an endpoint (bmRequestType 0xA2); every other one goes to the interface.
ENDPOINT_REQUESTS = {
    INITIATE_ABORT_BULK_OUT,
    CHECK_ABORT_BULK_OUT_STATUS,
    INITIATE_ABORT_BULK_IN,
    CHECK_ABORT_BULK_IN_STATUS,
}
CHECK_OF = {
    INITIATE_ABORT_BULK_OUT: CHECK_ABORT_BULK_OUT_STATUS,
    INITIATE_ABORT_BULK_IN: CHECK_ABORT_BULK_IN_STATUS,
    INITIATE_CLEAR: CHECK_CLEAR_STATUS,
}


class UsbStall(Exception):
    """The request is not defined for the device or not valid now: the device answers STALL."""


class UsbError(Exception):
    """A condition VISA reports as an error status (a halted pipe, nothing to read)."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def capabilities() -> bytes:
    """GET_CAPABILITIES response (USBTMC table 37 with the USB488 fields of USB488 table 8)."""
    data = bytearray(24)
    data[0] = STATUS_SUCCESS
    data[2:4] = struct.pack("<H", 0x0100)  # bcdUSBTMC
    data[4] = 0b100  # USBTMC interface: INDICATOR_PULSE; not talk-only, not listen-only (488.2)
    data[5] = 0b1  # USBTMC device: ends a Bulk-IN transfer at a TermChar
    data[12:14] = struct.pack("<H", 0x0100)  # bcdUSB488
    data[14] = 0b111  # USB488 interface: 488.2, REN/GTL/LLO, TRIGGER
    data[15] = 0b110 | 0b001  # USB488 device: SR1, RL1, DT1 (D3, SCPI, left clear)
    return bytes(data)


@dataclass
class UsbtmcInterface:
    """One instrument's USBTMC interface with the USB488 subclass."""

    session: Session
    name: str
    out_tag: int = 0  # bTag of the current (or most recent) Bulk-OUT transfer
    in_tag: int = 0  # bTag of the current (or most recent) Bulk-IN transfer
    out_partial: bytearray = field(default_factory=bytearray)
    out_in_progress: bool = False  # a message sent without EOM is waiting for the rest
    response: bytearray = field(default_factory=bytearray)  # what the instrument has queued to send
    in_active: bool = False  # REQUEST_DEV_DEP_MSG_IN received, its Bulk-IN not read yet
    in_request: tuple[int, int | None] = (0, None)  # TransferSize and TermChar of that request
    in_sent: int = 0
    halted: set[int] = field(default_factory=set)  # endpoint addresses that are halted
    split: int | None = None  # the INITIATE request being processed
    abort_out: int | None = None  # NBYTES_RXD once INITIATE_ABORT_BULK_OUT succeeded
    abort_in: int | None = None  # NBYTES_TXD once INITIATE_ABORT_BULK_IN succeeded
    interrupt: deque[bytes] = field(default_factory=deque)
    _tag: int = 0
    _status_tag: int = 2  # bTag of the next READ_STATUS_BYTE, 2..127 (USB488 4.3.1)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    # ---- helpers -----------------------------------------------------------------------------------------

    def _next_tag(self) -> int:
        self._tag = self._tag % 255 + 1  # 1..255; 0 is never a bTag
        return self._tag

    def _emit(self, client: str, action: str) -> None:
        self.session.emit(
            {"kind": "bus_control", "instrument": self.name, "client": client, "action": f"USB: {action}"}
        )

    @property
    def remote_local(self):
        return bus_of(self.session, self.name).remote_local

    def _check_srq(self) -> None:
        """SR1: a new reason for service sends the status byte on Interrupt-IN with bTag 1 (USB488 3.4.1)."""
        observe(self.session, self.name)
        if bus_of(self.session, self.name).service_request.latch and len(self.interrupt) < INTERRUPT_FIFO:
            stb = serial_poll(self.session, self.name)  # queued: RQS is cleared like after a serial poll
            self.interrupt.append(bytes([0x81, stb]))

    def srq_pending(self) -> bool:
        with self._lock:
            self._check_srq()
            return any(p[0] == 0x81 for p in self.interrupt)

    def take_srq(self) -> int | None:
        """The status byte of the oldest service-request packet on Interrupt-IN, if there is one."""
        with self._lock:
            self._check_srq()
            for packet in self.interrupt:
                if packet[0] == 0x81:
                    self.interrupt.remove(packet)
                    return packet[1]
            return None

    # ---- bulk transfers ----------------------------------------------------------------------------------

    def bulk_out(self, data: bytes, eom: bool = True, client: str = "pyvisa") -> int:
        """DEV_DEP_MSG_OUT: a message, or a part of one when ``eom`` is false."""
        with self._lock:
            if BULK_OUT in self.halted:
                raise UsbError(constants.VI_ERROR_IO, "the Bulk-OUT endpoint is halted")
            if not self.out_in_progress:
                self.out_tag = self._next_tag()
            self.out_partial += data
            self.out_in_progress = not eom
            if eom:
                message, self.out_partial = bytes(self.out_partial), bytearray()
                self.remote_local.listen()  # REN and a message: REMS (RL state machine, USB488 4.3.2)
                self.session.write(self.name, message, client=client)
                self._check_srq()
            return len(data)

    def trigger(self, client: str = "pyvisa") -> None:
        """MsgID = TRIGGER (USB488 3.2.1.1): the device triggers."""
        with self._lock:
            if BULK_OUT in self.halted:
                raise UsbError(constants.VI_ERROR_IO, "the Bulk-OUT endpoint is halted")
            self.out_tag = self._next_tag()
            self.remote_local.listen()
            self.session.write(self.name, b"*TRG", client=client)
            self._emit(client, "TRIGGER")
            self._check_srq()

    def request_msg_in(self, size: int, term_char: int | None = None, client: str = "pyvisa") -> int:
        """REQUEST_DEV_DEP_MSG_IN: the host asks for up to ``size`` bytes; returns the bTag."""
        with self._lock:
            if BULK_OUT in self.halted:
                raise UsbError(constants.VI_ERROR_IO, "the Bulk-OUT endpoint is halted")
            self.in_tag = self._next_tag()
            self.in_active, self.in_request, self.in_sent = True, (size, term_char), 0
            return self.in_tag

    def bulk_in(self, client: str = "pyvisa") -> tuple[bytes, bool]:
        """The Bulk-IN transfer of the request: the data and whether it ends the message (EOM)."""
        with self._lock:
            if BULK_IN in self.halted:
                raise UsbError(constants.VI_ERROR_IO, "the Bulk-IN endpoint is halted")
            if not self.in_active:
                raise UsbError(constants.VI_ERROR_IO, "no REQUEST_DEV_DEP_MSG_IN to answer")
            if not self.response:
                message = self.session.read(self.name, client=client)
                self._check_srq()
                if message is None:  # the instrument has nothing: it NAKs and the host waits
                    raise UsbError(constants.VI_ERROR_TMO, "the device has no data to send")
                self.response += message
            size, term_char = self.in_request
            end = min(size, len(self.response))
            if term_char is not None:
                stop = self.response.find(bytes([term_char]), 0, end)
                if stop != -1:
                    end = stop + 1
            data = bytes(self.response[:end])
            del self.response[:end]
            self.in_active, self.in_sent = False, len(data)
            return data, not self.response

    # ---- VISA-level conveniences (what VISA does with the requests) -------------------------------------

    def write(self, data: bytes, client: str = "pyvisa") -> int:
        return self.bulk_out(data, True, client)

    def read(self, size: int, term_char: int | None, client: str = "pyvisa") -> tuple[bytes, bool]:
        self.request_msg_in(size, term_char, client)
        return self.bulk_in(client)

    def clear(self, client: str = "pyvisa") -> None:
        """viClear: INITIATE_CLEAR, CHECK_CLEAR_STATUS until done, then CLEAR_FEATURE on the halted pipe."""
        with self._lock:
            self.control_request(CLASS_INTERFACE, INITIATE_CLEAR, 0, INTERFACE_NUMBER, 1, client)
            for _ in range(100):
                status = self.control_request(
                    CLASS_INTERFACE, CHECK_CLEAR_STATUS, 0, INTERFACE_NUMBER, 2, client
                )
                if status[0] != STATUS_PENDING:
                    break
            self.control_request(
                STD_ENDPOINT_OUT, STD_CLEAR_FEATURE, FEATURE_ENDPOINT_HALT, BULK_OUT, 0, client
            )

    def read_stb(self, client: str = "pyvisa") -> int:
        """viReadSTB: READ_STATUS_BYTE with the next bTag (2..127), the byte comes back on Interrupt-IN."""
        with self._lock:
            tag = self._status_tag
            self._status_tag = tag + 1 if tag < 127 else 2
            reply = self.control_request(CLASS_INTERFACE, READ_STATUS_BYTE, tag, INTERFACE_NUMBER, 3, client)
            if reply[0] != STATUS_SUCCESS:
                raise UsbError(constants.VI_ERROR_IO, f"READ_STATUS_BYTE failed with status {reply[0]:#x}")
            for packet in self.interrupt:
                if packet[0] == 0x80 | tag:
                    self.interrupt.remove(packet)
                    return packet[1]
            raise UsbError(constants.VI_ERROR_IO, "the status byte did not arrive on Interrupt-IN")

    def control_ren(self, mode: int, client: str = "pyvisa") -> None:
        """viGpibControlREN on a USB488 interface: the modes map onto REN_CONTROL, GO_TO_LOCAL, LOCAL_LOCKOUT."""
        ops = constants.RENLineOperation
        with self._lock:

            def send(request: int, value: int = 0) -> None:
                self.control_request(CLASS_INTERFACE, request, value, INTERFACE_NUMBER, 1, client)

            if mode == ops.deassert:
                send(REN_CONTROL, 0)
            elif mode == ops.asrt:
                send(REN_CONTROL, 1)
            elif mode == ops.deassert_gtl:
                send(GO_TO_LOCAL)
                send(REN_CONTROL, 0)
            elif mode == ops.asrt_address:
                send(REN_CONTROL, 1)
                self.remote_local.listen()  # addressed to listen: remote
            elif mode in (ops.asrt_llo, ops.asrt_address_llo):
                send(REN_CONTROL, 1)
                if mode == ops.asrt_address_llo:
                    self.remote_local.listen()
                send(LOCAL_LOCKOUT)
            elif mode == ops.address_gtl:
                send(GO_TO_LOCAL)
            else:
                raise UsbError(constants.VI_ERROR_INV_MODE, f"unknown REN mode {mode}")

    # ---- the control endpoint ------------------------------------------------------------------------------

    def control_request(
        self,
        request_type: int,
        request: int,
        value: int,
        index: int,
        length: int,
        client: str = "pyvisa",
    ) -> bytes:
        """A control-endpoint request and its response data; ``UsbStall`` when the device would STALL."""
        with self._lock:
            if request_type in (STD_ENDPOINT_OUT, STD_ENDPOINT_IN):
                return self._standard(request_type, request, value, index, length)
            if request_type not in (CLASS_INTERFACE, CLASS_ENDPOINT) or request not in RESPONSE_SIZE:
                raise UsbStall(f"request {request_type:#04x}/{request} is not defined for this device")
            on_endpoint = request in ENDPOINT_REQUESTS
            if (request_type == CLASS_ENDPOINT) != on_endpoint:
                raise UsbStall(f"request {request} is addressed to the wrong recipient")
            if on_endpoint:
                wanted = (
                    BULK_OUT if request in (INITIATE_ABORT_BULK_OUT, CHECK_ABORT_BULK_OUT_STATUS) else BULK_IN
                )
                if index & 0xFF != wanted:
                    raise UsbStall(f"endpoint {index & 0xFF:#04x} is not the one this request is for")
            elif index & 0xFF != INTERFACE_NUMBER:
                raise UsbStall(f"interface {index & 0xFF} does not exist")
            if self.split is not None and request != CHECK_OF[self.split]:
                # A new class request while an INITIATE is still being processed (USBTMC 4.2.1.1).
                return self._pad(bytes([STATUS_SPLIT_IN_PROGRESS]), request)[:length]
            data = self._class_request(request, value, client)
            self._check_srq()
            return data[:length]

    def _pad(self, data: bytes, request: int) -> bytes:
        return data + bytes(max(RESPONSE_SIZE[request] - len(data), 0))

    def _standard(self, request_type: int, request: int, value: int, index: int, length: int) -> bytes:
        endpoints = {BULK_OUT, BULK_IN, INTERRUPT_IN}
        endpoint = index & 0xFF
        if endpoint not in endpoints:
            raise UsbStall(f"endpoint {endpoint:#04x} does not exist")
        if (
            request_type == STD_ENDPOINT_OUT
            and request == STD_CLEAR_FEATURE
            and value == FEATURE_ENDPOINT_HALT
        ):
            self.halted.discard(endpoint)
            return b""
        if request_type == STD_ENDPOINT_IN and request == STD_GET_STATUS:
            return struct.pack("<H", 1 if endpoint in self.halted else 0)[:length]
        raise UsbStall(f"standard request {request} is not supported")

    def _class_request(self, request: int, value: int, client: str) -> bytes:
        if request == INITIATE_ABORT_BULK_OUT:
            tag = value & 0xFF
            if self.out_in_progress and tag == self.out_tag:
                self.halted.add(BULK_OUT)
                self.abort_out, self.split = len(self.out_partial), request
                self.out_partial, self.out_in_progress = bytearray(), False
                return bytes([STATUS_SUCCESS, self.out_tag])
            status = STATUS_TRANSFER_NOT_IN_PROGRESS if self.out_in_progress else STATUS_FAILED
            return bytes([status, self.out_tag])
        if request == CHECK_ABORT_BULK_OUT_STATUS:
            if self.abort_out is None:
                return self._pad(bytes([STATUS_SPLIT_NOT_IN_PROGRESS]), request)
            nbytes, self.abort_out, self.split = self.abort_out, None, None
            return bytes([STATUS_SUCCESS, 0, 0, 0]) + struct.pack("<I", nbytes)
        if request == INITIATE_ABORT_BULK_IN:
            tag = value & 0xFF
            if self.in_active and tag == self.in_tag:
                self.abort_in, self.split = self.in_sent, request
                self.in_active = False
                self.response.clear()
                return bytes([STATUS_SUCCESS, self.in_tag])
            status = STATUS_TRANSFER_NOT_IN_PROGRESS if self.in_active else STATUS_FAILED
            return bytes([status, self.in_tag])
        if request == CHECK_ABORT_BULK_IN_STATUS:
            if self.abort_in is None:
                return self._pad(bytes([STATUS_SPLIT_NOT_IN_PROGRESS]), request)
            nbytes, self.abort_in, self.split = self.abort_in, None, None
            return bytes([STATUS_SUCCESS, 0, 0, 0]) + struct.pack("<I", nbytes)  # bmAbortBulkIn D0 = 0
        if request == INITIATE_CLEAR:
            self.halted.add(BULK_OUT)
            self.out_partial, self.out_in_progress = bytearray(), False
            self.response.clear()
            self.in_active = False
            self.interrupt.clear()
            self.session.clear_output(self.name, client=client)
            self.split = request
            self._emit(client, "INITIATE_CLEAR")
            return bytes([STATUS_SUCCESS])
        if request == CHECK_CLEAR_STATUS:
            if self.split != INITIATE_CLEAR:
                return self._pad(bytes([STATUS_SPLIT_NOT_IN_PROGRESS]), request)
            self.split = None
            return bytes([STATUS_SUCCESS, 0])  # bmClear D0 = 0: the Bulk-IN FIFO is empty
        if request == GET_CAPABILITIES:
            return capabilities()
        if request == INDICATOR_PULSE:
            self._emit(client, "INDICATOR_PULSE")
            return bytes([STATUS_SUCCESS])
        if request == READ_STATUS_BYTE:
            tag = value & 0x7F
            if value & 0xFF80 or not 2 <= tag <= 127:  # D7 and D15..D8 must be 0 (table 11)
                return bytes([STATUS_FAILED, tag, 0])
            if len(self.interrupt) >= INTERRUPT_FIFO:
                return bytes([STATUS_INTERRUPT_IN_BUSY, tag, 0])
            stb = serial_poll(self.session, self.name)
            self.interrupt.append(bytes([0x80 | tag, stb]))  # queued first, then the control response
            return bytes([STATUS_SUCCESS, tag, 0])
        # REN_CONTROL, GO_TO_LOCAL, LOCAL_LOCKOUT (USB488 4.3.2-4.3.4)
        rl = self.remote_local
        if request == REN_CONTROL:
            if value & 0xFF == 1:
                rl.assert_ren()
            elif value & 0xFF == 0:
                rl.release_ren()
            else:
                return bytes([STATUS_FAILED])
            self._emit(client, f"REN_CONTROL {value & 0xFF}")
        elif request == GO_TO_LOCAL:
            rl.gtl()
            self._emit(client, "GO_TO_LOCAL")
        else:
            rl.llo()
            self._emit(client, "LOCAL_LOCKOUT")
        return bytes([STATUS_SUCCESS])


_REGISTRY: weakref.WeakKeyDictionary[Session, dict[str, UsbtmcInterface]] = weakref.WeakKeyDictionary()
_REGISTRY_LOCK = threading.Lock()


def usb_interface(session: Session, name: str) -> UsbtmcInterface:
    """The USBTMC interface of one instrument; every access path of the session shares it."""
    with _REGISTRY_LOCK:
        return _REGISTRY.setdefault(session, {}).setdefault(name, UsbtmcInterface(session, name))
