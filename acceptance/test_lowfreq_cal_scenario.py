"""Low-frequency calibration bench, acceptance (docs/lowfreq-cal.md §7, AC-1..AC-4).

The host program is represented by examples/lowfreq-cal/lowfreq_cal_demo.py, which follows the
host's interface contract: it opens ``TCPIP0::<host>::<port>::SOCKET`` with line-feed termination
and runs initialization, the acquisition per frequency and the wrap-up. Here it runs over real
sockets through pyvisa-py, exactly as an unmodified host program would against `simubench serve`.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from collections.abc import Callable
from itertools import pairwise
from pathlib import Path

import pytest
import pyvisa

ROOT = Path(__file__).resolve().parents[1]
# The bench and its definitions; issue 48 runs this scenario again on a workspace built through MCP alone.
WORKSPACE = Path(os.environ.get("SIMUBENCH_LFCAL_WORKSPACE") or ROOT / "examples" / "lowfreq-cal")
BENCH = WORKSPACE / "lowfreq_cal.bench.yaml"
SCRIPT = ROOT / "examples" / "lowfreq-cal" / "lowfreq_cal_demo.py"
FREQUENCIES = [1e3, 1e4, 1e5, 1e6]


def _demo():
    spec = importlib.util.spec_from_file_location("lowfreq_cal_demo", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


demo = _demo()


def over_sockets(body: Callable, bench: Path = BENCH):
    """Serve the bench on raw sockets and HiSLIP (FR-6) and run ``body(scope, calibrator, addresses)``."""
    from simubench.access.hislip_server import HislipServer
    from simubench.access.scpi_server import ScpiServer
    from simubench.api import open_bench

    def client(addresses: dict[str, str]):
        rm = pyvisa.ResourceManager("@py")
        try:
            scope = demo.open_instrument(rm, addresses["scope"], "LFCAL-REFSCOPE")
            cal = demo.open_instrument(rm, addresses["calibrator"], "LFCAL-CALIBRATOR")
            return body(scope, cal, addresses)
        finally:
            rm.close()

    async def serve():
        session = open_bench(bench)
        scpi = ScpiServer(session, base_port=0)
        hislip = HislipServer(session, port=0)
        listeners = await scpi.start()
        await hislip.start()
        addresses = {lst.instrument: lst.address for lst in listeners}
        addresses.update({f"hislip:{k}": v for k, v in hislip.addresses().items()})
        try:
            return await asyncio.to_thread(client, addresses)
        finally:
            await hislip.stop()
            await scpi.stop()
            session.close()

    return asyncio.run(serve())


def test_ac1_host_program_runs_through_over_raw_sockets():
    """AC-1 / FR-6: initialization, acquisition per frequency and wrap-up, with no error anywhere."""

    def body(scope, cal, addresses):
        assert addresses["scope"].endswith("::SOCKET") and addresses["calibrator"].endswith("::SOCKET")
        assert addresses["hislip:scope"].endswith("::INSTR")
        response = demo.run(scope, cal, frequencies=FREQUENCIES)
        return response, scope.query("SYST:ERR?"), cal.query(":SYSTem:ERRor?")

    response, scope_err, cal_err = over_sockets(body)
    assert [f for f, _ in response] == FREQUENCIES
    assert scope_err == '0,"No error"' and cal_err == '0,"No error"'


@pytest.mark.parametrize("point", [30000, 100000])
def test_ac2_any_point_count_reads_back(point):
    """AC-2: the FP pieces join into exactly ``point`` samples, which are the record."""

    def body(scope, cal, _):
        demo.setup(scope, cal, 1, 1, point)
        cal.write(":CHANnel1:BASE:AMPLitude 1.0")
        cal.write(":CHANnel1:BASE:FREQuency 1000")
        cal.write(":CHANnel1:OUTPut 1")
        scope.write("C1:VDIV 200 mV")
        scope.write("TDIV 1E-3")
        scope.write("TRMD SINGLE")
        data = demo.read_waveform(scope, 1, point)
        scope.write("WAVEFORM_SETUP SP,0,NP,0,FP,0,SN,0")
        first = list(map(float, scope.query('C1:INSPECT? "DATA_ARRAY_1",FLOAT').split()[2:-1]))
        return data, first

    data, first = over_sockets(body)
    assert len(data) == point
    assert data[: len(first)] == first  # the pieces are the same record read from the start
    period = point // 10  # 1 kHz over 10 ms: one period every tenth of the record
    assert data[: period * 9] == pytest.approx(data[period : period * 10], abs=3e-3)


def test_ac3_frequency_response_is_in_range():
    """AC-3: one point per frequency, amplitude about the 1 Vpp set, not rising with frequency."""
    response = over_sockets(lambda scope, cal, _: demo.run(scope, cal, frequencies=FREQUENCIES))
    amps = [a for _, a in response]
    assert len(amps) == len(FREQUENCIES)
    assert all(0.99 < a < 1.01 for a in amps)
    assert all(b <= a for a, b in pairwise(amps))


def test_ac4_same_seed_same_result():
    """AC-4: two deployments with the same seed give the same response, digit for digit."""

    def body(scope, cal, _):
        return demo.run(scope, cal, frequencies=FREQUENCIES, point=20000)

    assert over_sockets(body) == over_sockets(body)
