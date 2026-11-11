"""The internal probe (signal-chain.md 6, #951): the signal at an output port inside an instrument."""

import shutil
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from helpers import raises_with
from simubench.api import open_bench
from simubench.models import api as M
from simubench.models.bench import BenchDefinition
from simubench.server import views
from simubench.server.app import create_app
from simubench.server.manager import ServedSession, SessionManager
from simubench.server.services import internal_probe

ROOT = Path(__file__).resolve().parents[1]
#: On a point of the 4096-sample spectrum at the DSO-X 2024A's 2 GSa/s.
TONE = 2e9 * 8 / 4096
BENCH = {
    "name": "t",
    "instruments": {
        "awg": {
            "model": "sb-awg-2",
            "state": {"ch1.frequency": TONE, "ch1.amplitude": 2.0, "ch1.output": True},
        },
        "scope": {"model": "sb-dso-4", "state": {"ch1.scale": 1.0, "ch1.probe": 1.0}},
        "dsox": {"model": "keysight-dsox2024a", "state": {"ch1.scale": 1.0, "ch1.probe": 1.0}},
    },
    "wires": [{"from": "awg.OUT1", "to": "scope.CH1"}, {"from": "awg.OUT1", "to": "dsox.CH1"}],
    "run": {"seed": 3, "clock": "command"},
}


@pytest.fixture
def served():
    session = open_bench(BenchDefinition.model_validate(BENCH))
    yield ServedSession("t", session, None)
    session.close()


def test_an_analog_port_reads_the_signal_the_block_passes_on(served):
    p = internal_probe.probe(served, "scope", "ch1.front.out")
    assert p.medium == "signal" and p.format is None and p.spectrum is None
    # 2 Vpp is the level into 50 Ω; into the scope's 1 MΩ the generator's open-circuit 4 Vpp shows:
    # 2 V peak.
    assert p.lines[0].freq_hz == pytest.approx(TONE)
    assert p.peak == pytest.approx(2.0, rel=1e-3) and p.rms == pytest.approx(np.sqrt(2), rel=1e-3)
    assert len(p.waveform.volts) == internal_probe.WAVE_POINTS and p.waveform.net == "ch1.front.out"
    assert p.silence == []


def test_a_sample_stream_port_reads_one_frame_with_its_rate_and_format(served):
    p = internal_probe.probe(served, "dsox", "adc.out1")
    assert p.medium == "samples" and p.lines == []
    assert p.format is not None and (p.format.kind, p.format.bits) == ("volts", 8)
    adc = served.session.instrument("scope").components["adc"]
    adc = served.session.instrument("dsox").components["adc"]
    assert p.sample_rate_hz == pytest.approx(2e9)
    assert len(p.waveform.volts) == internal_probe.PROBE_FRAME
    # 4 Vpp on 1 V/div and 8 divisions: half of full scale, not clipped.
    assert p.full_scale_used == pytest.approx(0.5, abs=0.02) and not p.clipped
    # Quantized: every sample sits on a code step.
    step = 1.0 / adc.codes_per_div()
    assert np.allclose(np.round(np.array(p.waveform.volts) / step) * step, p.waveform.volts, atol=1e-12)
    # The spectrum's strongest point is the tone, at half of full scale: -6 dBFS.
    assert p.spectrum is not None and p.spectrum.unit == "dBFS"
    top = int(np.argmax(p.spectrum.level[1:])) + 1
    assert p.spectrum.freq_hz[top] == pytest.approx(TONE)
    assert p.spectrum.level[top] == pytest.approx(-6.0, abs=0.2)


def test_the_probe_is_passive_and_repeatable(served):
    s = served.session
    s.write("scope", b":WAVeform:SOURce CHANnel1;:DIGitize;:WAVeform:PREamble?\n")
    before = s.read("scope")
    now, events = s.now_ns, len(s.history)
    first = internal_probe.probe(served, "scope", "adc.out1")
    again = internal_probe.probe(served, "scope", "dec.out1")
    assert internal_probe.probe(served, "scope", "adc.out1") == first
    assert internal_probe.probe(served, "scope", "dec.out1") == again
    assert (s.now_ns, len(s.history)) == (now, events)
    # A script's next reading is what it would have been on a bench nobody probed.
    s.write("scope", b":MEASure:VPP? CHANnel1\n")
    probed = s.read("scope")
    fresh = open_bench(BenchDefinition.model_validate(BENCH))
    try:
        fresh.write("scope", b":WAVeform:SOURce CHANnel1;:DIGitize;:WAVeform:PREamble?\n")
        assert fresh.read("scope") == before
        fresh.write("scope", b":MEASure:VPP? CHANnel1\n")
        assert fresh.read("scope") == probed
    finally:
        fresh.close()


def test_a_silent_port_says_why(served):
    p = internal_probe.probe(served, "scope", "ch2.front.out")
    assert p.peak == 0 and [r.kind for r in p.silence] == ["unconnected"]
    assert p.silence[0].path == ["scope.CH2"]


def test_an_unknown_port_lists_the_ports_that_can_be_probed(served):
    with raises_with(KeyError, "no output port 'adc.nope'", "adc.out1", "ch1.front.out"):
        internal_probe.probe(served, "scope", "adc.nope")
    with pytest.raises(KeyError):
        internal_probe.probe(served, "nobody", "adc.out1")


def test_the_internals_view_says_what_flows_on_each_wire(served):
    wires = {w.from_: w for w in views.internals(served, "scope").wires}
    assert wires["ch1.front.out"].medium == "signal" and wires["ch1.front.out"].format is None
    stream = wires["adc.out1"]
    # sb-dso-4's converter is ideal and has no rate of its own: it runs at the decimator's.
    assert stream.medium == "samples" and stream.format == M.ProbeFormat(kind="volts", bits=0)
    dec = served.session.instrument("scope").components["dec"]
    assert stream.sample_rate_hz == pytest.approx(1.0 / dec.sample_interval("out1"))
    real = {w.from_: w for w in views.internals(served, "dsox").wires}["adc.out1"]
    assert real.sample_rate_hz == pytest.approx(2e9) and real.format == M.ProbeFormat(kind="volts", bits=8)


def hidden(reveal: bool) -> dict:
    scope = {**BENCH["instruments"]["dsox"], "hidden": {"ch1.front": {"gain_error_ppm": 20000.0}}}
    return {
        **BENCH,
        "instruments": {**BENCH["instruments"], "dsox": scope},
        "run": {**BENCH["run"], "reveal_true_values": reveal},
    }


def test_validation_mode_withholds_an_instrument_with_hidden_values():
    """The signal inside an instrument shows its hidden per-unit values: kept back unless the bench
    reveals true values. The structure stays readable, and an instrument without hidden values —
    the generator here — hides nothing."""
    session = open_bench(BenchDefinition.model_validate(hidden(reveal=False)))
    try:
        served = ServedSession("t", session, None)
        with raises_with(internal_probe.ProbeWithheld, "validation mode", "dsox"):
            internal_probe.probe(served, "dsox", "adc.out1")
        assert views.internals(served, "dsox").components
        assert internal_probe.probe(served, "awg", "ch1.level.out").peak > 0
        assert internal_probe.probe(served, "scope", "dec.out1").format is not None
    finally:
        session.close()
    session = open_bench(BenchDefinition.model_validate(hidden(reveal=True)))
    try:
        p = internal_probe.probe(ServedSession("t", session, None), "dsox", "ch1.front.out")
        assert p.peak == pytest.approx(2.04, rel=1e-3)
    finally:
        session.close()


# ---- the two seams: HTTP and MCP ----------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path):
    import yaml

    for name, reveal in (("open", True), ("closed", False)):
        (tmp_path / f"{name}.bench.yaml").write_text(
            yaml.safe_dump({"schema": "simubench.bench/2", **hidden(reveal)}), encoding="utf-8"
        )
    shutil.copy(ROOT / "validation" / "benches" / "awg_rc_scope.bench.yaml", tmp_path / "plain.bench.yaml")
    return tmp_path


def test_http_route(workspace):
    manager = SessionManager(workspace, scpi_base_port=None)
    with TestClient(create_app(manager, token=None, web_dist=Path("/nonexistent"))) as c:
        c.base_url = "http://127.0.0.1"
        sid = c.post("/api/sessions", json={"bench_path": "open.bench.yaml"}).json()["id"]
        url = f"/api/sessions/{sid}/instruments/dsox/internals/probe"
        ok = c.get(url, params={"port": "adc.out1"})
        assert ok.status_code == 200 and ok.json()["format"] == {"kind": "volts", "bits": 8}
        missing = c.get(url, params={"port": "adc.nope"})
        assert missing.status_code == 404 and "adc.out1" in missing.json()["detail"]
        sid = c.post("/api/sessions", json={"bench_path": "closed.bench.yaml"}).json()["id"]
        url = f"/api/sessions/{sid}/instruments/dsox/internals/probe"
        refused = c.get(url, params={"port": "adc.out1"})
        assert refused.status_code == 403 and "validation mode" in refused.json()["detail"]
        assert c.get(f"/api/sessions/{sid}/instruments/dsox/internals").status_code == 200


async def test_mcp_tool(workspace):
    pytest.importorskip("mcp")
    from mcp import Client

    from helpers import mcp_result
    from simubench.mcp_server import build_server

    manager = SessionManager(workspace, scpi_base_port=None)
    server = build_server(manager, ui_url="http://127.0.0.1:1/?token=x")  # no --allow-control

    async def call(c, name, **args):
        return mcp_result(await c.call_tool(name, args), error_ok=True)

    async with Client(server) as c:
        await call(c, "start_session", path="open.bench.yaml")
        p = await call(c, "probe_internal", instrument="dsox", port="adc.out1")
        assert p["medium"] == "samples" and p["format"]["bits"] == 8
        await call(c, "start_session", path="closed.bench.yaml")
        r = await call(c, "probe_internal", instrument="dsox", port="adc.out1")
        assert "验证模式" in r["error"]
    for sid in list(manager.sessions):
        await manager.stop(sid)
