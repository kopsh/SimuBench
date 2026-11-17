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
KEYS = ("amplitude", "vpp", "vmax", "vmin", "mean", "rms", "frequency", "period")


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
        "amplitude": "VAMPlitude",
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
        "amplitude": "AMPL",
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


def test_the_screen_draws_the_math_functions_turned_on(client):
    """A function turned on (``:FUNCtion<f>:DISPlay ON``) comes with the screen, computed from the
    record it shows without a new acquisition: an FFT as a spectrum (its bins, its span setting,
    its peaks where :MEASure:FFT finds them), a time-domain function on the channels' grid; a
    function turned off is not drawn."""
    b = Bench(client, "demo/uxr.bench.yaml")
    b.scpi(":CHANnel2:DISPlay ON;:TIMebase:SCALe 1E-6;:ACQuire:POINts 2000;:DIGitize")
    assert b.screen()["functions"] == []
    b.scpi(":FUNCtion1:FFTMagnitude CHANnel2;:FUNCtion1:FFT:STOP 10E6;:FUNCtion1:DISPlay ON")
    b.scpi(":FUNCtion2:ADD CHANnel1,CHANnel2;:FUNCtion2:DISPlay ON")
    before = b.screen()["acquisitions"]
    screen = b.screen()
    assert screen["acquisitions"] == before  # drawn from the record shown: nothing acquired
    fft, add = screen["functions"]
    assert (fft["function"], fft["operator"], fft["spectrum"]) == (1, "FFTMagnitude", True)
    assert fft["x0"] == 0.0 and fft["x0"] + fft["dx"] * (len(fft["values"]) - 1) <= 10e6
    peak = fft["x0"] + fft["dx"] * fft["values"].index(max(fft["values"]))
    assert peak == pytest.approx(float(b.scpi(":MEASure:FFT:FREQuency? FUNCtion1,1,-30")))
    # the reply carries six significant digits (+d.dddddE+0d): equal to within its last digit
    assert max(fft["values"]) == pytest.approx(
        float(b.scpi(":MEASure:FFT:MAGNitude? FUNCtion1,1,-30")), rel=5e-6
    )
    ch1 = next(t for t in screen["traces"] if t["channel"] == 1)
    assert (add["function"], add["spectrum"], add["unit"]) == (2, False, "VOLT")
    assert len(add["values"]) == len(ch1["volts"]) and add["x0"] == pytest.approx(ch1["t0_s"])
    b.scpi(":FUNCtion2:DISPlay OFF")
    assert [f["function"] for f in b.screen()["functions"]] == [1]


def test_the_scpi_console_shows_a_binary_block_as_its_header_and_size(client):
    """POST …/scpi (the Web's SCPI console and the MCP send_scpi alike): a WORD waveform block is
    shown as its header and byte count, as the log writes it, not as replacement characters."""
    b = Bench(client, "demo/uxr.bench.yaml")
    b.scpi(":ACQuire:POINts 2000;:DIGitize;:WAVeform:SOURce CHANnel1;:WAVeform:FORMat WORD")
    assert b.scpi(":WAVeform:DATA?") == "#44000… (4007 B)"
    assert b.scpi(":WAVeform:FORMat ASCii;:WAVeform:DATA?").count(",") == 1999


def test_a_stopped_record_keeps_its_own_time_span_when_the_timebase_changes(client):
    """#1333: the record is the acquisition's, so a new time per division moves the screen's window
    around it (it does not stretch it), and the record's own parameters (``:WAVeform:XORigin?``,
    the screen's ``t0_s`` / ``dt_s`` / ``points``) and measurements stay as they were."""
    b = Bench(client, "benches/keysight-dsox2024a.bench.yaml")
    b.scpi(":TIMebase:SCALe 5e-6")
    b.scpi(":DIGitize CHANnel1")
    b.scpi(":WAVeform:SOURce CHANnel1")
    before = b.screen()
    tr = before["traces"][0]
    span = tr["points"] * tr["dt_s"]
    assert before["screen_left_s"] == pytest.approx(tr["t0_s"], abs=3 * tr["dt_s"]), (
        "the record fills its screen"
    )
    assert span == pytest.approx(10 * 5e-6, rel=1e-6)
    xorigin = float(b.scpi(":WAVeform:XORigin?"))
    row = _row(before, 1)

    b.scpi(":TIMebase:SCALe 5e-4")  # 100x slower, no new acquisition
    slow = b.screen()
    t = slow["traces"][0]
    assert slow["source"] == "record" and slow["acquisitions"] == before["acquisitions"]
    assert (t["t0_s"], t["dt_s"], t["points"]) == (tr["t0_s"], tr["dt_s"], tr["points"])
    assert float(b.scpi(":WAVeform:XORigin?")) == xorigin
    assert _row(slow, 1) == row, "measurements are the record's, not the display's"
    # the screen now spans 5 ms; the 50 us record covers a hundredth of it, centred on the trigger
    window = 10 * slow["timebase_scale"]
    assert window == pytest.approx(5e-3, rel=1e-6)
    assert slow["screen_left_s"] == pytest.approx(-window / 2, rel=1e-6)
    assert t["t0_s"] > slow["screen_left_s"] and t["t0_s"] + span < slow["screen_left_s"] + window
    assert span / window == pytest.approx(0.01, rel=1e-6)

    b.scpi(":TIMebase:SCALe 5e-7")  # 10x faster: the screen shows a stretch of the record
    fast = b.screen()
    assert fast["screen_left_s"] == pytest.approx(-2.5e-6, rel=1e-6)
    assert fast["traces"][0]["t0_s"] < fast["screen_left_s"]
