"""Two domain errors every layer may raise, below the server (#1394).

A caller asked for something by a name that does not exist (:class:`Unknown`), or gave a value
that cannot be used (:class:`Invalid`). They subclass ``KeyError`` / ``ValueError`` so code that
catches the built-ins still does; the seams (HTTP, MCP, the assistant's tools,
:mod:`.server.errors`) catch these and not a bare ``KeyError`` or ``ValueError``, which is how a
bug looks too and surfaces as itself.
"""

from __future__ import annotations


class Unknown(KeyError):
    """Nothing by the name asked for: an instrument, a session, a conversation turn, a net."""


class Invalid(ValueError):
    """A value the caller gave cannot be used: a file name, an id, an option, an unsuitable state."""
