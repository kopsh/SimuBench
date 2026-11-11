"""``wait_for_free_ports`` (#857): the snapshot fixtures' fixed ports are waited for, not failed on."""

from __future__ import annotations

import socket
import sys
import threading
import time

import pytest

from helpers import raises_with, wait_for_free_ports


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _hold(port: int) -> socket.socket:
    held = socket.socket()
    if sys.platform != "win32":  # on Windows SO_REUSEADDR would let others share the port
        held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    held.bind(("127.0.0.1", port))
    held.listen()
    return held


@pytest.mark.unit
def test_free_ports_return_at_once():
    start = time.monotonic()
    wait_for_free_ports([_free_port(), _free_port()], timeout=5.0)
    assert time.monotonic() - start < 0.05


@pytest.mark.unit
def test_a_port_held_for_a_moment_is_waited_for():
    port = _free_port()
    held = _hold(port)
    threading.Timer(0.03, held.close).start()
    wait_for_free_ports([port], timeout=5.0)  # returns once the holder lets go
    with socket.socket() as again:  # and the port really can be bound now
        again.bind(("127.0.0.1", port))


@pytest.mark.unit
def test_a_port_that_stays_held_raises_naming_it():
    port = _free_port()
    held = _hold(port)
    try:
        with raises_with(RuntimeError, str(port)):
            wait_for_free_ports([port], timeout=0.05)
    finally:
        held.close()
