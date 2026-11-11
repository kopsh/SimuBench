"""The numbers SCPI uses for an instrument's channels (#207, D51; #895).

A definition may number its channels in blocks (a DAQ's first channel is 101); without a numbering
the numbers are the indexes 1..count. ``index`` is the channel index (``ch<n>`` in the definition)
a SCPI number names; ``number`` is the other way round.
"""

from __future__ import annotations

from ..models.definition import InstrumentDefinition
from ..scpi.errors import ScpiError


class ChannelMap:
    def __init__(self, definition: InstrumentDefinition) -> None:
        self.count = definition.channels.count
        #: number -> channel index, and back. Empty when the definition declares no numbering.
        self.by_number: dict[int, int] = {}
        self.by_index: dict[int, int] = {}
        offset = 0
        for block in definition.channels.numbering:
            for i in range(block.count):
                self.by_number[block.first + i] = offset + i + 1
                self.by_index[offset + i + 1] = block.first + i
            offset += block.count

    def number(self, channel: int) -> int:
        """The channel's number on the bus: 101 for a DAQ's first channel, else its index."""
        return self.by_index.get(channel, channel)

    def exists(self, number: int) -> bool:
        """Whether a SCPI channel number names a channel of this instrument."""
        if self.by_number:
            return number in self.by_number
        return 1 <= number <= self.count

    def index(self, number: int) -> int:
        """The channel index (``ch<n>`` in the definition) that a SCPI channel number names."""
        if self.by_number:
            index = self.by_number.get(number)
            if index is None:
                raise ScpiError(-114, f"channel {number} does not exist")
            return index
        if not 1 <= number <= self.count:
            raise ScpiError(-114, f"channel {number} does not exist")
        return number
