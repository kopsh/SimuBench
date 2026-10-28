"""Shared helpers: scripts drive SimuBench the way they drive hardware, through PyVISA (D5)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
import pyvisa

BENCHES = Path(__file__).parent / "benches"


@dataclass
class Capture:
    t: np.ndarray
    v: np.ndarray
    preamble: list[float]


def open_rig(bench: str):
    rm = pyvisa.ResourceManager(f"{BENCHES / bench}@simubench")
    awg = rm.open_resource("awg", read_termination="\n", write_termination="\n")
    scope = rm.open_resource("scope", read_termination="\n", write_termination="\n")
    return rm, awg, scope


def run_over_hislip(bench: str, body: Callable, count: int = 2) -> None:
    """Serve a bench over HiSLIP and run ``body(*resources)`` against it with pyvisa-py (D41).

    The blocking VISA client runs on a worker thread while the server keeps the event loop.
    """
    from simubench.access.hislip_server import HislipServer
    from simubench.api import open_bench

    def client(port: int):
        rm = pyvisa.ResourceManager("@py")
        try:
            resources = [
                rm.open_resource(
                    f"TCPIP0::127.0.0.1::hislip{i},{port}::INSTR",
                    read_termination="\n",
                    write_termination="\n",
                    timeout=5000,
                )
                for i in range(count)
            ]
            return body(*resources)
        finally:
            rm.close()

    async def serve():
        session = open_bench(BENCHES / bench)
        server = HislipServer(session, port=0)
        await server.start()
        try:
            return await asyncio.to_thread(client, server.port)
        finally:
            await server.stop()
            session.close()

    return asyncio.run(serve())


def capture(scope, probe: float = 1.0, source: int = 1, digitize: bool = True) -> Capture:
    if digitize:
        scope.write(":DIGitize")
    scope.write(f":WAVeform:SOURce CHANnel{source}")
    pre = scope.query_ascii_values(":WAVeform:PREamble?")
    codes = scope.query_binary_values(
        ":WAVeform:DATA?", datatype="h", is_big_endian=False, container=np.array
    )
    _fmt, _typ, points, _cnt, x_inc, x_org, x_ref, y_inc, y_org, y_ref = pre
    assert len(codes) == int(points)
    v = ((codes - y_ref) * y_inc + y_org) * probe
    t = (np.arange(len(codes)) - x_ref) * x_inc + x_org
    return Capture(t, v, pre)


@pytest.fixture
def rig():
    rm, awg, scope = open_rig("awg_rc_scope.bench.yaml")
    yield awg, scope
    rm.close()
