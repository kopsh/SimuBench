"""The scope screen's measurement table against the scope's own SCPI measurements (#356, #280).

Through the service API (``/api/sessions/{id}/instruments/scope/screen``), on the two real-model
example benches: after one acquisition (DSO-X ``:DIGitize``, SDS2104X Plus ``:TRIGger:MODE
SINGle``) every value of the table equals the instrument's reply for the same record —
``:MEASure:<item>? CHANnel1`` on the DSO-X, ``:MEASure:SIMPle:VALue? <item>`` on the Siglent (whose
replies have three significant digits). Also: a running screen (live frames), a stopped scope with
no record, a displayed channel the acquisition did not take, and a value that cannot be measured.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from simubench.server.app import create_app
from simubench.server.manager import SessionManager

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-token"
KEYS = ("vpp", "vmax", "vmin", "mean", "rms", "frequency", "period")


@pytest.fixture
def client():
    manager = SessionManager(ROOT / "examples", scpi_base_port=None)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        yield c


class Bench:
    def __init__(self, client: TestClient, path: str) -> None:
        self.client = client
        self.sid = client.post("/api/sessions", json={"bench_path": path, "force": True}).json()["id"]

    def scpi(self, command: str, instrument: str = "scope") -> str:
        r = self.client.post(
            f"/api/sessions/{self.sid}/instruments/{instrument}/scpi", json={"command": command}
        ).json()
        assert r["errors"] == [], (command, r["errors"])
        return (r["response"] or "").strip()

    def screen(self) -> dict:
        return self.client.get(f"/api/sessions/{self.sid}/instruments/scope/screen").json()


def _row(screen: dict, channel: int) -> dict:
    return next(m for m in screen["measurements"] if m["channel"] == channel)


def test_dsox_after_digitize_the_table_is_the_measure_replies(client):
    b = Bench(client, "benches/keysight-dsox2024a.bench.yaml")
    b.scpi(":DIGitize CHANnel1")
    screen = b.screen()
    assert screen["source"] == "record" and not screen["running"] and screen["acquisitions"] == 1
    row = _row(screen, 1)
    items = {
        "vpp": "VPP",
        "vmax": "VMAX",
        "vmin": "VMIN",
        "mean": "VAVerage",
        "rms": "VRMS",
        "frequency": "FREQuency",
        "period": "PERiod",
    }
    for key, item in items.items():
        reply = float(b.scpi(f":MEASure:{item}? CHANnel1"))
        # The DSO-X answers with six significant digits.
        assert row[key] == pytest.approx(reply, rel=1e-5, abs=1e-12), key
    # The same record twice: the table does not change between reads.
    assert _row(b.screen(), 1) == row


def test_siglent_after_a_single_acquisition_the_table_is_the_simple_values(client):
    b = Bench(client, "benches/siglent_scope_rc.bench.yaml")
    b.scpi("C1:OUTP ON", "awg")
    b.scpi(":CHANnel1:PROBe VALue,10")  # the bench's 10x probe
    b.scpi(":TRIGger:MODE SINGle")
    screen = b.screen()
    assert screen["source"] == "record" and screen["acquisitions"] == 1
    row = _row(screen, 1)
    b.scpi(":MEASure:SIMPle:SOURce C1")
    items = {
        "vpp": "PKPK",
        "vmax": "MAX",
        "vmin": "MIN",
        "mean": "MEAN",
        "rms": "RMS",
        "frequency": "FREQ",
        "period": "PER",
    }
    for key, item in items.items():
        reply = float(b.scpi(f":MEASure:SIMPle:VALue? {item}"))
        # The SDS answers with three significant digits (1.70E+00): agree to its last digit.
        assert row[key] == pytest.approx(reply, rel=6e-3, abs=1e-6), key
    assert row["vpp"] == pytest.approx(1.7, rel=1e-3)
    assert row["frequency"] == pytest.approx(1000.0, rel=1e-3)


def test_a_running_screen_measures_its_live_frame(client):
    b = Bench(client, "benches/keysight-dsox2024a.bench.yaml")
    screen = b.screen()
    assert screen["source"] == "live" and screen["running"]
    row = _row(screen, 1)
    # the crossings carry the range table's noise (#745): a percent, not 0.1
    assert row["frequency"] == pytest.approx(1000.0, rel=1e-2)
    assert row["vpp"] > 0


def test_a_running_screen_holds_its_record_when_the_average_is_beyond_the_model(client):
    """#986/#1012: a frame of 8192 averages of the 100 000-point record is beyond what a screen
    computes (``ENVELOPE_BUDGET``); the screen holds its record and is not an error."""
    b = Bench(client, "benches/keysight-dsox2024a.bench.yaml")
    b.scpi(":DIGitize CHANnel1")
    b.scpi(":RUN")
    b.scpi(":ACQuire:TYPE AVERage")
    b.scpi(":ACQuire:COUNt 8192")
    screen = b.screen()
    assert screen["source"] == "record" and not screen["running"]
    assert screen["traces"] and screen["traces"] == b.screen()["traces"]  # held, not a new frame each time


def test_a_stopped_scope_without_a_record_has_no_table(client):
    b = Bench(client, "benches/keysight-dsox2024a.bench.yaml")
    b.scpi(":STOP")
    screen = b.screen()
    assert screen["source"] == "record" and screen["acquisitions"] == 0
    assert screen["traces"] == [] and screen["measurements"] == []


def test_only_the_channels_of_the_record_are_measured(client):
    b = Bench(client, "benches/keysight-dsox2024a.bench.yaml")
    b.scpi(":CHANnel2:DISPlay ON")
    b.scpi(":DIGitize CHANnel1")
    assert [m["channel"] for m in b.screen()["measurements"]] == [1]


def test_a_value_that_cannot_be_measured_is_empty_not_a_number(client):
    """No signal (the generator's output off): no edges, so no frequency or period — null in the
    table, as the scope's own reply is its 'cannot measure' value."""
    b = Bench(client, "benches/keysight-dsox2024a.bench.yaml")
    b.scpi("C1:OUTP OFF", "awg")
    b.scpi(":DIGitize CHANnel1")
    row = _row(b.screen(), 1)
    assert row["frequency"] is None and row["period"] is None
    assert float(b.scpi(":MEASure:FREQuency? CHANnel1")) > 9e37  # 9.9E+37
    assert row["vpp"] is not None


def test_a_deep_record_screen_is_an_envelope_and_the_memory_depth_says_so(client):
    """#1012: a deep record (6.4 Mpts here) reaches the screen as per-pixel extremes, not sample by
    sample; its measurement row is empty (the screen does not read the whole record); the session
    marks the memory depth setting as computed on read, and no other setting."""
    b = Bench(client, "benches/keysight-uxr0334b.bench.yaml")
    for command in (":ACQuire:POINts 10000000", ":TIMebase:SCALe 1E-5", ":DIGitize"):
        b.scpi(command)
    screen = b.screen()
    trace = next(t for t in screen["traces"] if t["channel"] == 1)
    assert trace["volts"] == [] and len(trace["minmax"]) == 2 * 1000
    lows, highs = trace["minmax"][0::2], trace["minmax"][1::2]
    assert (
        all(h >= lo for lo, h in zip(lows, highs, strict=True)) and max(highs) - min(lows) > 0.3
    )  # 400 mVpp
    assert all(_row(screen, 1)[k] is None for k in KEYS)
    scope = next(
        i for i in client.get(f"/api/sessions/{b.sid}").json()["instruments"] if i["name"] == "scope"
    )
    marked = {v["path"] for v in scope["state"] if v["fidelity"] == "computed_on_read"}
    assert marked == {"acquire.points"}
