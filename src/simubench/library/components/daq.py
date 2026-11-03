"""Data acquisition: a mainframe's backplane and modules, and its scan engine (DAQ, #191 E2).

First wave: one 34970A mainframe with a 34901A 20-channel multiplexer in slot 1. The card is one
component: its two-wire channels are relay inputs to a common analog bus, and the bus carries the
internal DMM's input impedance and feeds the DMM as a signal. Closing a relay therefore loads the
DUT wired to that channel, as it does on the bench; the card's closure limit is its
break-before-make scanning rule (a second channel is refused).

The scan table turns a channel list into a run: the trigger model's sampler closes each channel in
turn, waits the relay settling time, takes one reading with the internal DMM, and gives each
reading its channel number as a field (the reading buffer keeps the channel with the reading).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...engine.circuit import Circuit
from ...engine.component import Component, OpResult, PortSpec, component, op
from ...engine.signal import Signal
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .meters import DmmAdc, DmmInputTerminal


def _flag(value: Any) -> bool:
    return value if isinstance(value, bool) else str(value).strip().upper() in ("1", "ON", "TRUE")


@component("daq_backplane")
class DaqBackplane(Component):
    """The mainframe's slots and installed modules (#191 E2).

    The modules named here are what a SCPI channel number refers to: ``101`` is channel 1 of the
    module in slot 1 (``slot_stride`` is the SCPI slot field, 100). First wave: the 34970A has
    three slots and one 34901A in slot 1; a channel of an empty slot does not exist. The scan
    table and the multiplexer card check their channels against it.
    """

    defaults = {
        "slots": 3,  # module slots in the mainframe
        "modules": [],  # installed modules: [{slot: 1, model: "34901A", channels: 20}]
        "slot_stride": 100,  # the SCPI channel number's slot field: 101 -> slot 1, channel 1
    }

    def module_at(self, slot: int) -> dict[str, Any] | None:
        """The module installed in a slot, or None when the slot is empty."""
        return next((m for m in self.param("modules") or [] if int(m["slot"]) == slot), None)

    def slot_of(self, number: int) -> int:
        return int(number) // int(self.param("slot_stride"))

    def module_of(self, number: int) -> dict[str, Any] | None:
        return self.module_at(self.slot_of(number))

    def check_channel(self, number: int) -> None:
        """A SCPI channel number this mainframe does not have: -114 (a channel of no module, or a
        channel beyond the module's own count)."""
        number = int(number)
        module = self.module_of(number)
        if module is None:
            raise ScpiError(
                -114, f"channel {number} does not exist (no module in slot {self.slot_of(number)})"
            )
        channel = number % int(self.param("slot_stride"))
        if not 1 <= channel <= int(module.get("channels", 0)):
            raise ScpiError(-114, f"channel {number} does not exist")

    def readings(self) -> dict[str, Any]:
        modules = ", ".join(f"slot {m['slot']}: {m['model']}" for m in self.param("modules") or [])
        return {"values": {"modules": modules or "—", "slots": int(self.param("slots"))}}


@component("mux_card")
class MuxCard(Component):
    """A multiplexer card's relays and analog bus (#191 E2, first wave: 34901A, 20 channels).

    Every channel is a two-wire input (``H``/``L``); a closed relay connects it to the card's
    internal analog bus through the contact resistance ``relay_r``. The bus carries the internal
    DMM's input resistance (asked of the ``dmm`` terminal, so a high-impedance range changes it)
    and its voltage is the signal the internal DMM measures — closing a channel therefore loads
    the DUT on that channel. ``close_limit`` is the number of relays that may be closed at once:
    the first wave's card is break-before-make while scanning, so the default is one, and a second
    closure is refused (-221).

    The relay state lives in the instrument's channel state (``ch<n>.<relay_state>``, declared as
    ``channel_state`` by the definition); the card reads it whenever it stamps the circuit.
    """

    refs = {
        "backplane": ("daq_backplane", "instrument"),
        "dmm": ("dmm_input_terminal", "instrument"),
    }
    ports = {
        "out": PortSpec("out", "signal", "the analog bus, to the internal DMM"),
        "sense": PortSpec("out", "signal", "the four-wire sense bus (FRES), to the DMM's sense input"),
    }
    #: The channel terminals: H/L of each channel, e.g. ch101H (created by the instrument ports).
    port_pattern = r"ch\d{3}[HL]"
    units = {"relay_r": "OHM", "relay_settle": "S"}
    defaults = {
        "slot": 1,  # the module slot the card sits in
        "relay_r": 0.5,  # contact resistance of a closed relay, ohm
        "relay_settle": 3.0e-3,  # s from closing a relay to a settled reading (card switching time)
        "close_limit": 1,  # relays that may be closed at once (0: no limit)
        "relay_state": "closed",  # the channel state a relay writes: ch<n>.closed
        "backplane": "",  # the daq_backplane component (slots and modules)
        "dmm": "",  # the internal dmm_input_terminal whose input resistance loads the bus
        # Four-wire pairing (#301): in FRES, channel n carries the test current and channel
        # n + four_wire_offset the sense (34901A: n and n+10, n = 1..10). 0: no four-wire.
        "four_wire_offset": 0,
        "current_channels": [],  # the module's current channels (34901A: 21, 22; with a shunt)
    }

    def _backplane(self) -> DaqBackplane:
        assert self.owner is not None
        return self.owner.components[str(self.param("backplane"))]  # type: ignore[return-value]

    def _terminal(self) -> DmmInputTerminal:
        assert self.owner is not None
        return self.owner.components[str(self.param("dmm"))]  # type: ignore[return-value]

    # ---- relay state -----------------------------------------------------------------------------

    def _state(self, index: int) -> str:
        return f"ch{index}.{self.param('relay_state')}"

    def closed(self, index: int) -> bool:
        assert self.owner is not None
        return bool(self.owner.get_state(self._state(index)))

    def closed_channels(self) -> list[int]:
        assert self.owner is not None
        return [n for n in range(1, self.owner.channel_count + 1) if self.closed(n)]

    def close_channel(self, index: int) -> None:
        """Close one channel's relay (break-before-make: the previous closure stays, the limit
        refuses a second when ``close_limit`` is reached)."""
        assert self.owner is not None
        number = self.owner.channel_number(index)
        self._backplane().check_channel(number)
        if self.closed(index):
            return
        limit = int(self.param("close_limit"))
        closed = self.closed_channels()
        if limit > 0 and len(closed) >= limit:
            raise ScpiError(
                -221, f"channel {number}: at most {limit} channel{'s' if limit != 1 else ''} can be closed"
            )
        self.owner.set_state(self._state(index), True)
        self.session.invalidate(electrical=True)

    def open_channel(self, index: int) -> None:
        if self.closed(index):
            assert self.owner is not None
            self.owner.set_state(self._state(index), False)
            self.session.invalidate(electrical=True)

    def open_all(self) -> None:
        opened = False
        for index in self.closed_channels():
            assert self.owner is not None
            self.owner.set_state(self._state(index), False)
            opened = True
        if opened:
            self.session.invalidate(electrical=True)

    # ---- functions on channels (#301) -----------------------------------------------------------

    def _local(self, number: int) -> int:
        return int(number) % int(self._backplane().param("slot_stride"))

    def is_current_channel(self, number: int) -> bool:
        return self._local(number) in [int(c) for c in self.param("current_channels") or []]

    def sense_pair(self, number: int) -> int | None:
        """The four-wire sense channel of ``number`` (its SCPI number), or None when it has none."""
        offset = int(self.param("four_wire_offset"))
        if offset <= 0 or not 1 <= self._local(number) <= offset:
            return None
        return int(number) + offset

    def check_function(self, number: int) -> None:
        """Whether the internal DMM's function can be measured on a channel (#301): current only on
        the current channels (they have the shunt), the other functions not on them, and four-wire
        resistance only on a channel that has a sense pair. Otherwise -221 (settings conflict)."""
        mode = self._terminal().mode()
        current = self.is_current_channel(number)
        if mode == "CURRent" and not current:
            listed = ", ".join(str(c) for c in self.param("current_channels") or []) or "none"
            raise ScpiError(-221, f"channel {number}: current is measured on the current channels ({listed})")
        if mode != "CURRent" and current:
            raise ScpiError(-221, f"channel {number} is a current channel")
        if mode == "FRESistance" and self.sense_pair(number) is None:
            raise ScpiError(-221, f"channel {number} has no four-wire sense pair")

    # ---- circuit ---------------------------------------------------------------------------------

    def _bus_mode(self) -> str:
        return self._terminal().mode()

    def emf(self) -> dict[str, Signal]:
        """The resistance functions' test current, pushed onto the analog bus (#301)."""
        if self._bus_mode() in ("RESistance", "FRESistance"):
            return {self.uid: Signal.constant(self._terminal().per_range("test_current"))}
        return {}

    def stamp(self, circuit: Circuit, node: Callable[[str], str]) -> None:
        assert self.owner is not None
        relay = max(float(self.param("relay_r")), 1e-9)
        mode = self._bus_mode()
        for index in self.closed_channels():
            number = self.owner.channel_number(index)
            circuit.resistor(node(f"ch{number}H"), node("bus_h"), relay)
            circuit.resistor(node(f"ch{number}L"), node("bus_l"), relay)
            pair = self.sense_pair(number) if mode == "FRESistance" else None
            if pair is not None:
                circuit.resistor(node(f"ch{pair}H"), node("sense_h"), relay)
                circuit.resistor(node(f"ch{pair}L"), node("sense_l"), relay)
        if mode in ("RESistance", "FRESistance"):
            # The test current leaves the bus HI into the DUT and returns on LO; the DMM behind it
            # draws (almost) nothing, so an open channel drives the bus past the compliance.
            circuit.resistor(node("bus_h"), node("bus_l"), 1e12)
            circuit.current(node("bus_h"), self.uid)
            circuit.current(node("bus_l"), self.uid, sign=-1.0)
            circuit.resistor(node("sense_h"), node("sense_l"), 1e12)
            return
        if mode == "CURRent":
            circuit.resistor(node("bus_h"), node("bus_l"), self._terminal().per_range("shunt"))
            return
        z = self._terminal().effective_z_in(derive=False)
        if z is not None and z > 0:
            circuit.resistor(node("bus_h"), node("bus_l"), z)

    def output(self, port: str) -> Signal:
        """The analog bus voltage (H − L), the internal DMM's input; ``sense``: the four-wire sense
        bus."""
        hi, lo = ("sense_h", "sense_l") if port == "sense" else ("bus_h", "bus_l")
        return self.pin_signal(hi) + self.pin_signal(lo).scale(-1.0)

    # ---- operations ------------------------------------------------------------------------------

    @op("close")
    def op_close(self, query: bool = False, channel: int | None = None, **_: Any) -> OpResult:
        """``ROUTe:CLOSe (@101)`` closes the channel; the query replies whether it is closed."""
        assert channel is not None
        if query:
            return OpResult(V.format_bool(self.closed(channel)))
        self.close_channel(channel)
        return OpResult()

    @op("open")
    def op_open(self, channel: int | None = None, **_: Any) -> OpResult:
        assert channel is not None
        self.open_channel(channel)
        return OpResult()

    @op("closed")
    def op_closed(self, channel: int | None = None, **_: Any) -> OpResult:
        assert channel is not None
        return OpResult(V.format_bool(self.closed(channel)))

    @op("open_all")
    def op_open_all(self, **_: Any) -> OpResult:
        self.open_all()
        return OpResult()

    def readings(self) -> dict[str, Any]:
        assert self.owner is not None
        numbers = [self.owner.channel_number(n) for n in self.closed_channels()]
        return {"values": {"closed": ", ".join(str(n) for n in numbers) or "—"}}


@component("scan_table")
class ScanTable(Component):
    """The scan table (#191 E2): a channel list read as one run, and the sampler behind it.

    ``list`` is the scan list in SCPI channel numbers (a definition binds it to an ``int_list``
    setting, e.g. ``ROUT:SCAN (@101:110)``); ``ascending`` scans it in ascending channel order (the
    34970A's rule) instead of the order it was written; ``count`` is how many times the list is
    scanned per run and ``interval`` the wait between two scans (stored for the model's timing;
    the first wave times each reading with the relay settling time plus the DMM's reading time).
    The scan runs
    through the trigger model: each of its readings closes its channel (the card's
    break-before-make rule), reads the internal DMM and carries the channel number in the ``CHAN``
    field, so the reading buffer keeps channel and value together.
    """

    refs = {
        "mux": ("mux_card", "instrument"),
        "dmm": ("dmm_adc", "instrument"),
        "backplane": ("daq_backplane", "instrument"),
    }
    defaults = {
        "list": [],  # scan list in SCPI channel numbers
        "ascending": True,  # scan in ascending channel order (the 34970A's rule), not list order
        "count": 1,  # scans of the list per run
        "interval": 0.0,  # s between two scans
        "mux": "",  # the mux_card that closes the channels
        "dmm": "",  # the dmm_adc that takes each reading
        "backplane": "",  # the daq_backplane that validates the channel numbers
    }
    units = {"interval": "S"}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._channel = 0  # the channel of the last reading (SCPI number; 0: none yet)
        self._reading = 0.0  # the last reading
        self._scan = 0  # the scan the last reading belongs to

    def _mux(self) -> MuxCard:
        assert self.owner is not None
        return self.owner.components[str(self.param("mux"))]  # type: ignore[return-value]

    def _dmm(self) -> DmmAdc:
        assert self.owner is not None
        return self.owner.components[str(self.param("dmm"))]  # type: ignore[return-value]

    def _backplane(self) -> DaqBackplane:
        assert self.owner is not None
        return self.owner.components[str(self.param("backplane"))]  # type: ignore[return-value]

    def channels(self) -> list[int]:
        """The scan list as a list of channel indexes, in list order."""
        assert self.owner is not None
        numbers = [int(n) for n in self.param("list") or []]
        if not numbers:
            raise ScpiError(-222, "the scan list is empty")
        if _flag(self.param("ascending")):
            numbers = sorted(numbers)
        for number in numbers:
            self._backplane().check_channel(number)
        for number in numbers:  # every channel exists: now whether it can take the function
            self._mux().check_function(number)
        return [self.owner.channel_index(number) for number in numbers]

    # ---- a Sampler for trigger_model -------------------------------------------------------------

    def run_samples(self) -> int | None:
        """One run is the list scanned ``count`` times."""
        return len(self.channels()) * max(1, int(self.param("count")))

    def sample_time_ns(self) -> int:
        """One reading of a scan: the relay settling time plus the DMM's own reading time."""
        settle = float(self._mux().param("relay_settle"))
        return round(settle * 1e9) + self._dmm().sample_time_ns()

    def run_begin(self) -> None:
        self.channels()  # validate the list before the first relay moves

    def run_done(self) -> None:
        self._mux().open_all()

    def sample(self, index: int) -> float:
        """Close the reading's channel (opening the previous one), take the DMM's reading."""
        channels = self.channels()
        index_channel = channels[index % len(channels)]
        self._scan = index // len(channels) + 1
        self._mux().open_all()
        self._mux().close_channel(index_channel)
        assert self.owner is not None
        self._channel = self.owner.channel_number(index_channel)
        value = float(self._dmm().measure())
        self._reading = value
        return value

    def sample_fields(self, index: int) -> dict[str, float]:
        return {"CHAN": float(self._channel), "SCAN": float(self._scan)}

    # ---- panel / status --------------------------------------------------------------------------

    def readings(self) -> dict[str, Any]:
        assert self.owner is not None
        listed = [int(n) for n in self.param("list") or []]
        text = V.format_channel_list(listed).decode() if listed else "—"
        closed = [self.owner.channel_number(n) for n in self._mux().closed_channels()]
        return {
            "values": {
                "channel": self._channel,
                "reading": self._reading,
                "list": text,
                "scan": self._scan,
                "closed": ", ".join(str(n) for n in closed) or "—",
            }
        }


__all__ = ["DaqBackplane", "MuxCard", "ScanTable"]
