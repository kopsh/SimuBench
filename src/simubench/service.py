"""Assembling the service (issue 637): the options the serve / tui / mcp entries share, the
manager those options build, and the address table's one line format. The seven-parameter
manager build was three copied blocks, and a flag one entry lacked was a setting its users could
not make (``mcp`` had no ``--record-raw`` while its builder already read it). The workspace rule
stays with each entry: serve falls back to the bench's folder, mcp takes the working folder, the
TUI launch takes the bench's.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .server.manager import SessionManager


def add_service_options(
    parser: argparse.ArgumentParser, *, logs: bool = True, token: str = "require"
) -> None:
    """The connection, record and plugin options every serving entry takes. ``token``:
    ``"require"`` adds ``--require-token`` (serve and mcp start a service, D43); ``"present"``
    only takes ``--token VALUE`` (the TUI presents a token to a service it did not start).
    ``logs=False`` for entries that do not configure logging."""
    parser.add_argument(
        "--scpi-base-port", type=int, default=5025, help="first SCPI port; 0 lets the OS choose"
    )
    parser.add_argument(
        "--hislip-port",
        type=int,
        default=4880,
        help="shared HiSLIP port (D41); 0 lets the OS choose, and --no-scpi disables it too",
    )
    parser.add_argument("--no-scpi", action="store_true", help="do not open SCPI sockets")
    parser.add_argument("--record-dir", help="write run records here")
    parser.add_argument("--record-raw", action="store_true", help="also keep waveform blocks byte for byte")
    parser.add_argument(
        "--allow-plugins",
        action="store_true",
        help="run components/*.py from the workspace as plugins (code; default off, D3)",
    )
    if token == "require":
        parser.add_argument("--token", help="require this access token (default: no token, D43)")
        parser.add_argument(
            "--require-token",
            action="store_true",
            help="require a random access token; use on a computer shared with other users",
        )
    elif token == "present":
        parser.add_argument("--token", help="access token of the service (default: no token, D43)")
    if logs:
        parser.add_argument("--log-level", default="warning")
        parser.add_argument(
            "--log-format", choices=["text", "json"], default="text", help="json: one object per line"
        )
        parser.add_argument("--log-file", help="also write the log here")


def build_manager(args: argparse.Namespace, workspace: Path) -> SessionManager:
    """The manager the shared options describe; loopback only (D9: a local service)."""
    return SessionManager(
        workspace,
        scpi_host="127.0.0.1",
        scpi_base_port=None if args.no_scpi else args.scpi_base_port,
        hislip_port=None if args.no_scpi else args.hislip_port,
        record_dir=Path(args.record_dir).resolve() if args.record_dir else None,
        record_raw=bool(getattr(args, "record_raw", False)),
        allow_plugins=args.allow_plugins,
    )


def address_line(kind: str, name: str, address: str) -> str:
    """One row of the startup address table: the same line from serve and from the TUI launch."""
    return f"  {kind:<6} {name:<12} {address}"
