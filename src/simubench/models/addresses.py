"""VISA resource addresses compared as names (#1589).

One spelling for an address, so ``tcpip0::192.168.1.20::inst0::instr`` and
``TCPIP0::192.168.1.20::INSTR`` count as the same instrument. The PyVISA backend resolves the
addresses a script opens through it, and bench validation refuses two instruments at one address
with it: the two must agree, or validation would pass a bench the backend cannot tell apart.
"""

from __future__ import annotations

import re

from pyvisa import rname

#: ``TCPIP0::127.0.0.1,<port>::inst0::INSTR`` is what ``serve`` prints when no port mapper is on
#: 111; the port is random per run, so it is not part of the name.
_VXI11_PORT = re.compile(r"^(TCPIP\d*::[^:,]+),\d+(::(?:inst|gpib))", flags=re.IGNORECASE)


def normalize_address(address: str) -> str:
    """The canonical upper-case form of a VISA resource name (VISA's defaults written out); a
    string VISA does not parse is only upper-cased."""
    address = _VXI11_PORT.sub(r"\1\2", address.strip())
    try:
        return str(rname.parse_resource_name(address)).upper()
    except rname.InvalidResourceName:
        return address.upper()
