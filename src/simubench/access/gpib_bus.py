"""A virtual IEEE 488.1 bus (GPIB) with the bench's instruments on it (D63).

What a script can observe of a GPIB bus is simulated here, not just its addresses: the controller
(the VISA board interface) addresses talkers and listeners with command bytes, the addressed
commands (SDC, GET, GTL) act on the addressed listeners, the universal ones (DCL, LLO) on every
device, REN and IFC are lines of the bus, SRQ is the OR of the devices' request-service latches
and a serial poll returns the status byte and clears RQS.

Devices are the bench instruments whose ``address:`` is ``GPIB<board>::<primary>[::<secondary>]::INSTR``.
The board's own primary address is 0 (30 when a device has 0). The controller is always the one in
charge and the system controller: no simulated device can take control, so passing it fails.

Everything here is the *behaviour*; how a client reaches it (in the PyVISA backend, over HiSLIP
vendor messages, ...) is the access layer's business. Not modelled: parallel poll (the devices are
PP0), EOI as a separate line (a message ends where the engine's message ends), handshake timing.
"""

from __future__ import annotations

import threading
import weakref
from dataclasses import dataclass, field

from pyvisa import constants, rname

from ..engine.session import Session
from .bus import bus_of, observe, serial_poll

# 488.1 command bytes (table 1 of IEEE 488.1, as in VPP-4.3's viGpibCommand description).
GTL, SDC, PPC, GET, TCT = 0x01, 0x04, 0x05, 0x08, 0x09
LLO, DCL, PPU, SPE, SPD = 0x11, 0x14, 0x15, 0x18, 0x19
UNL, UNT = 0x3F, 0x5F
MLA, MTA, MSA = 0x20, 0x40, 0x60  # base of listen, talk and secondary address commands

CONTROLLER = "<controller>"  # the board interface as talker or listener


class GpibError(Exception):
    """A bus condition VISA reports as an error status."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GpibAddress:
    pad: int
    sad: int | None = None


@dataclass
class GpibBus:
    """One board: its devices, the lines and who is addressed."""

    session: Session
    board: int
    devices: dict[GpibAddress, str]
    controller_pad: int = 0
    atn: bool = False
    talker: str | None = None
    listeners: list[str] = field(default_factory=list)
    spe: bool = False
    ifc_count: int = 0
    _pending: tuple[str, int] | None = None  # the primary address just sent, a secondary may follow
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    # ---- who is on the bus ---------------------------------------------------------------------------

    def address_of(self, name: str) -> GpibAddress:
        for address, device in self.devices.items():
            if device == name:
                return address
        raise KeyError(name)

    def _emit(self, client: str, action: str, instrument: str | None = None) -> None:
        self.session.emit(
            {
                "kind": "bus_control",
                "instrument": instrument,
                "client": client,
                "action": f"GPIB{self.board}: {action}",
            }
        )

    @property
    def ren(self) -> bool:
        return any(bus_of(self.session, n).remote_local.ren for n in self.devices.values())

    @property
    def srq(self) -> bool:
        """The SRQ line: asserted while any device holds a request for service."""
        for name in self.devices.values():
            observe(self.session, name)
        return any(bus_of(self.session, n).service_request.latch for n in self.devices.values())

    def address_state(self, name: str | None) -> int:
        """VI_ATTR_GPIB_ADDR_STATE for a device (or the board, with ``None``)."""
        who = name or CONTROLLER
        if self.talker == who:
            return constants.AddressState.talker
        if who in self.listeners:
            return constants.AddressState.listenr
        return constants.AddressState.unaddressed

    # ---- the controller's commands (ATN true) -----------------------------------------------------------

    def command(self, data: bytes, client: str = "pyvisa") -> int:
        """Send command bytes with ATN asserted; returns how many were sent (viGpibCommand)."""
        with self._lock:
            for byte in data:
                self._command_byte(byte & 0x7F, client)
            self.flush_address()  # a primary address that ends the string is the plain device
        return len(data)

    def _resolve(self, pad: int, sad: int | None) -> str | None:
        if sad is None and pad == self.controller_pad:
            return CONTROLLER
        return self.devices.get(GpibAddress(pad, sad))

    def _command_byte(self, byte: int, client: str) -> None:
        pending, self._pending = self._pending, None
        if MSA <= byte < UNT + 0x20 and byte != 0x7F:  # a secondary address follows a primary one
            if pending is not None:
                kind, pad = pending
                self._address(kind, self._resolve(pad, byte & 0x1F))
            return
        if pending is not None:  # a primary address with no secondary: the plain device, if there is one
            kind, pad = pending
            self._address(kind, self._resolve(pad, None))
        if MLA <= byte < UNL:
            self._pending = ("L", byte & 0x1F)
        elif byte == UNL:
            self.listeners.clear()
        elif MTA <= byte < UNT:
            self._pending = ("T", byte & 0x1F)
        elif byte == UNT:
            self.talker = None
        elif byte == SPE:
            self.spe = True
        elif byte == SPD:
            self.spe = False
        elif byte == DCL:
            self._emit(client, "DCL")
            for name in self.devices.values():
                self._device_clear(name, client)
        elif byte == LLO:
            self._emit(client, "LLO")
            for name in self.devices.values():
                bus_of(self.session, name).remote_local.llo()
        elif byte in (GTL, SDC, GET, PPC, TCT):
            self._addressed(byte, client)
        # PPU and the reserved codes do nothing here: no device implements parallel poll.

    def flush_address(self) -> None:
        """A primary address at the end of a command string stands for the plain device."""
        if self._pending is not None:
            kind, pad = self._pending
            self._pending = None
            self._address(kind, self._resolve(pad, None))

    def _address(self, kind: str, who: str | None) -> None:
        if who is None:
            return  # nobody there: the command is not an error, nobody answers
        if kind == "L":
            if who not in self.listeners:
                self.listeners.append(who)
            if who != CONTROLLER:
                bus_of(self.session, who).remote_local.listen()  # REN and addressed to listen: remote
        else:
            self.talker = who  # a new talker unaddresses the old one

    def _device_clear(self, name: str, client: str) -> None:
        self.session.clear_output(name, client=client)

    def _addressed(self, byte: int, client: str) -> None:
        """The addressed commands act on the devices addressed to listen."""
        self.flush_address()
        for name in [n for n in self.listeners if n != CONTROLLER]:
            if byte == GTL:
                bus_of(self.session, name).remote_local.gtl()
                self._emit(client, "GTL", name)
            elif byte == SDC:
                self._device_clear(name, client)
                self._emit(client, "SDC", name)
            elif byte == GET:
                self.session.write(name, b"*TRG", client=client)
                observe(self.session, name)
                self._emit(client, "GET", name)
            # PPC and TCT: the device has no parallel poll and cannot take control (PP0, C0).

    # ---- the lines ------------------------------------------------------------------------------------------

    def send_ifc(self, client: str = "pyvisa") -> None:
        """Interface clear: every device's bus interface goes idle; the devices themselves are left alone."""
        with self._lock:
            self.ifc_count += 1
            self.talker, self.spe = None, False
            self.listeners.clear()
            self._pending = None
            self._emit(client, "IFC")

    def set_atn(self, mode: int) -> None:
        self.atn = mode in (constants.ATNLineOperation.asrt, constants.ATNLineOperation.asrt_immediate)

    def set_ren(self, mode: int, client: str = "pyvisa") -> None:
        """viGpibControlREN on the board or, with the addressing modes, on one device."""
        with self._lock:
            if mode == constants.RENLineOperation.deassert:
                self._release_ren()
            elif mode == constants.RENLineOperation.asrt:
                self._assert_ren()
            elif mode == constants.RENLineOperation.asrt_llo:
                self._assert_ren()
                self.command(bytes([LLO]), client)
            else:
                raise GpibError(constants.VI_ERROR_INV_MODE, "REN mode needs a device to address")
            self._emit(client, f"REN {constants.RENLineOperation(mode).name}")

    def _assert_ren(self) -> None:
        for name in self.devices.values():
            bus_of(self.session, name).remote_local.assert_ren()

    def _release_ren(self) -> None:
        for name in self.devices.values():
            bus_of(self.session, name).remote_local.release_ren()

    def control_ren(self, name: str, mode: int, client: str = "pyvisa") -> None:
        """viGpibControlREN from a device session: the same modes, the device being the one addressed."""
        ops = constants.RENLineOperation
        with self._lock:
            if mode in (ops.deassert, ops.asrt, ops.asrt_llo):
                self.set_ren(mode, client)
                return
            address = self.address_of(name)
            to_listen = self._listen_commands(address)
            if mode == ops.asrt_address:
                self._assert_ren()
                self.command(bytes([UNL, *to_listen]), client)
            elif mode == ops.asrt_address_llo:
                self._assert_ren()
                self.command(bytes([UNL, *to_listen, LLO]), client)
            elif mode == ops.deassert_gtl:
                self.command(bytes([UNL, *to_listen, GTL]), client)
                self._release_ren()
            elif mode == ops.address_gtl:
                self.command(bytes([UNL, *to_listen, GTL]), client)
            else:
                raise GpibError(constants.VI_ERROR_INV_MODE, f"unknown REN mode {mode}")
            self._emit(client, f"REN {ops(mode).name}", name)

    # ---- what VISA does on the bus for an instrument session ------------------------------------------

    def _listen_commands(self, address: GpibAddress) -> list[int]:
        out = [MLA | address.pad]
        if address.sad is not None:
            out.append(MSA | address.sad)
        return out

    def _talk_commands(self, address: GpibAddress) -> list[int]:
        out = [MTA | address.pad]
        if address.sad is not None:
            out.append(MSA | address.sad)
        return out

    def _me_talk(self) -> int:
        return MTA | self.controller_pad

    def _me_listen(self) -> int:
        return MLA | self.controller_pad

    def write(self, name: str, data: bytes, client: str = "pyvisa") -> list[str]:
        """viWrite on an instrument session: address it to listen, then send the message (ATN false)."""
        with self._lock:
            address = self.address_of(name)
            self.command(bytes([UNL, self._me_talk(), *self._listen_commands(address)]), client)
            self.flush_address()
            return self.deliver(data, client)

    def deliver(self, data: bytes, client: str = "pyvisa") -> list[str]:
        """Data bytes to every device addressed to listen (the board writes; ATN false)."""
        with self._lock:
            self.flush_address()
            targets = [n for n in self.listeners if n != CONTROLLER]
            if not targets:
                raise GpibError(constants.VI_ERROR_NLISTENERS, "no listeners: nothing is addressed to listen")
            for name in targets:
                bus_of(self.session, name).remote_local.listen()
                self.session.write(name, data, client=client)
                observe(self.session, name)
            return targets

    def take(self, name: str | None, client: str = "pyvisa") -> bytes | None:
        """The next response message of the addressed talker (the board listens). ``None``: it has none."""
        with self._lock:
            talker = name or self.talker
            if talker is None or talker == CONTROLLER:
                raise GpibError(constants.VI_ERROR_TMO, "no talker is addressed")
            out = self.session.read(talker, client=client)
            observe(self.session, talker)
            return out

    def address_for_read(self, name: str, client: str = "pyvisa") -> None:
        with self._lock:
            address = self.address_of(name)
            self.command(bytes([UNL, self._me_listen(), *self._talk_commands(address)]), client)
            self.flush_address()

    def device_clear(self, name: str, client: str = "pyvisa") -> None:
        """viClear: SDC to the addressed device."""
        with self._lock:
            address = self.address_of(name)
            self.command(bytes([UNL, self._me_talk(), *self._listen_commands(address), SDC]), client)

    def trigger(self, name: str, client: str = "pyvisa") -> None:
        """viAssertTrigger: GET to the addressed device."""
        with self._lock:
            address = self.address_of(name)
            self.command(bytes([UNL, self._me_talk(), *self._listen_commands(address), GET]), client)

    def read_stb(self, name: str, client: str = "pyvisa") -> int:
        """viReadSTB: serial poll enable, the device as talker, its status byte, serial poll disable."""
        with self._lock:
            address = self.address_of(name)
            self.command(bytes([UNL, self._me_listen(), SPE, *self._talk_commands(address)]), client)
            self.flush_address()
            stb = serial_poll(self.session, name)
            self.command(bytes([SPD, UNT]), client)
            return stb


def _device_addresses(session: Session, board: int) -> dict[GpibAddress, str]:
    devices: dict[GpibAddress, str] = {}
    for name, inst in session.instruments.items():
        try:
            parsed = rname.parse_resource_name(inst.instance.address or "")
        except rname.InvalidResourceName:
            continue
        if not isinstance(parsed, rname.GPIBInstr) or int(parsed.board) != board:
            continue
        sad = int(parsed.secondary_address) if parsed.secondary_address is not None else None
        address = GpibAddress(int(parsed.primary_address), sad)
        if address in devices:
            raise ValueError(
                f"{devices[address]} and {name} are both at GPIB{board}::{address.pad}: "
                "two devices cannot share a GPIB address"
            )
        devices[address] = name
    return devices


def boards_of(session: Session) -> list[int]:
    """The boards that have at least one device, in order."""
    out: set[int] = set()
    for inst in session.instruments.values():
        try:
            parsed = rname.parse_resource_name(inst.instance.address or "")
        except rname.InvalidResourceName:
            continue
        if isinstance(parsed, rname.GPIBInstr):
            out.add(int(parsed.board))
    return sorted(out)


_REGISTRY: weakref.WeakKeyDictionary[Session, dict[int, GpibBus]] = weakref.WeakKeyDictionary()
_REGISTRY_LOCK = threading.Lock()


def gpib_board(session: Session, board: int) -> GpibBus:
    """The bus of one board of a session; every access path of the session shares it."""
    with _REGISTRY_LOCK:
        boards = _REGISTRY.setdefault(session, {})
        if board not in boards:
            devices = _device_addresses(session, board)
            taken = {a.pad for a in devices if a.sad is None}
            boards[board] = GpibBus(session, board, devices, controller_pad=30 if 0 in taken else 0)
        return boards[board]
