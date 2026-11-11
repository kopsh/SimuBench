"""The domain errors the application raises, and the tuples of them a seam catches (#936).

A service raises these (never ``HTTPException`` or ``ToolError``); the HTTP routes map them through
``ApiContext.http_error`` and the MCP tools through ``McpContext.fail``, one table each.

``Forbidden`` and ``NotFound`` subclass the built-in ``PermissionError`` / ``FileNotFoundError``: the
operating system raises the built-ins itself (opening a file that is gone), and both kinds must meet
the same table row. Code that raises one of its own uses the domain name, which says what it means.
"""

from __future__ import annotations


class Forbidden(PermissionError):
    """The operation is not allowed here: a path outside the workspace, a file kind that cannot be removed."""


class NotFound(FileNotFoundError):
    """The thing asked for does not exist: a version, a run record, a file."""


#: What reading or writing a workspace file can raise, as a seam catches it.
FILE_ERRORS = (PermissionError, FileNotFoundError)
