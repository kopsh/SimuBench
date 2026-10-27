"""Calibration groundwork (M5, calibration-notes.md): identity, calendar, register, adjustment, decisions."""

import pytest

from simubench.api import open_bench
from simubench.engine.calibration import Spec, assess, decide
from simubench.models.bench import BenchDefinition

SPEC = Spec(3.2, 3.4)


def test_decision_rules_from_the_notes():
    # Spec 3.2–3.4 V, U = 4 mV (k = 2), guard band = U: acceptance zone 3.204–3.396 V.
    assert decide(3.399, SPEC, rule="simple") == "pass"
    assert decide(3.399, SPEC, rule="guard_band", u=0.004) == "no_statement"
    assert decide(3.300, SPEC, rule="guard_band", u=0.004) == "pass"
    assert decide(3.405, SPEC, rule="guard_band", u=0.004) == "fail"
    assert decide(3.203, SPEC, rule="guard_band", u=0.004) == "no_statement"
    # A true value just outside the limit, read just inside: simple acceptance accepts it wrongly.
    assert assess(decide(3.399, SPEC), 3.401, SPEC) == "false_accept"
    assert assess(decide(3.399, SPEC, rule="guard_band", u=0.004), 3.401, SPEC) == "withheld"
    assert assess("fail", 3.399, SPEC) == "false_reject"
    assert assess("pass", 3.3, SPEC) == "correct"


def bench(**run) -> BenchDefinition:
    return BenchDefinition.model_validate(
        {
            "name": "cal",
            "instruments": {
                "src": {
                    "model": "sb-smu-1",
                    "state": {
                        "source.voltage.level": 0.0,
                        "compliance.current.limit": 0.01,
                        "output.enabled": True,
                    },
                },
                "dmm": {
                    "model": "sb-dmm-1",
                    "serial": "MY12345",
                    "firmware": "2.1.0",
                    "hidden": {"meter": {"gain_error_ppm": 150.0, "offset": 200e-6}},
                },
            },
            "wires": [{"from": "src.HI", "to": "dmm.IN"}],
            "assets": [
                {"serial": "MY12345", "model": "SB-DMM-1", "cal_due": "2026-05-10"},
                {"serial": "MY99999", "status": "out_of_service"},
            ],
            "run": {"clock": "command", "start": "2026-05-04T09:00:00Z", **run},
        }
    )


@pytest.fixture
def s():
    session = open_bench(bench())
    yield session
    session.close()


def q(s, inst: str, cmd: str) -> str:
    s.write(inst, (cmd + "\n").encode())
    out = s.read(inst)
    return out.decode().strip() if out else ""


def test_identity_and_calendar(s):
    assert q(s, "dmm", "*IDN?") == "SIMUBENCH,SB-DMM-1,MY12345,2.1.0"
    assert q(s, "dmm", ":SYSTem:DATE?") == "2026,05,04"
    s.advance(3 * 86400 + 3600)
    assert q(s, "dmm", ":SYST:DATE?") == "2026,05,07" and q(s, "dmm", ":SYST:TIME?").startswith("10,00,")
    q(s, "dmm", ":SYST:DATE 2025,1,1")  # a mis-set instrument clock, not the bench's calendar
    assert q(s, "dmm", ":SYST:DATE?") == "2025,01,01" and q(s, "src", ":SYST:DATE?") == "2026,05,07"
    assert q(s, "dmm", ":SYST:ERR?").startswith("0,")
    assert s.calendar().isoformat().startswith("2026-05-07T10:00")


def test_adjustment_with_known_truth(s):
    def reading(v: float) -> float:
        s.write("src", f":SOUR:VOLT {v}\n".encode())
        return float(q(s, "dmm", ":MEAS:VOLT?"))

    as_found = {v: reading(v) for v in (0.0, 1.0, 10.0)}
    assert as_found[0.0] == pytest.approx(200e-6) and as_found[10.0] == pytest.approx(
        10 * (1 + 150e-6) + 200e-6
    )
    s.write("dmm", b":CAL:ZERO?\n")
    assert q(s, "dmm", ":SYST:ERR?") == '701,"Calibration error; calibration is secured"'
    s.write("dmm", b':CAL:SEC:STAT OFF,"SIMUBENCH"\n')
    reading(0.0)
    assert q(s, "dmm", ":CAL:ZERO?") == "0"
    reading(1.0)
    s.write("dmm", b":CAL:VAL 1\n")
    assert q(s, "dmm", ":CAL?") == "0"
    s.write("dmm", b":CAL:STOR;:CAL:SEC:STAT ON\n")
    assert q(s, "dmm", ":CAL:COUN?") == "1" and q(s, "dmm", ":CAL:DATE?") == '"2026-05-04"'
    as_left = {v: reading(v) for v in (0.0, 1.0, 10.0)}
    assert as_left == pytest.approx({0.0: 0.0, 1.0: 1.0, 10.0: 10.0}, abs=1e-12)
    # A wrong reference is caught, not silently applied.
    s.write("dmm", b':CAL:SEC:STAT OFF,"SIMUBENCH"\n')
    reading(1.0)
    s.write("dmm", b":CAL:VAL 2\n")
    assert q(s, "dmm", ":CAL?") == "1"
    assert q(s, "dmm", ":SYST:ERR?").startswith("702,")
    s.write("dmm", b":CAL:COUN 0\n")
    assert q(s, "dmm", ":SYST:ERR?").startswith("-221,")  # coefficients are not settable directly


def test_register_on_the_bench_calendar(tmp_path, capsys):
    from pathlib import Path

    import yaml
    from fastapi.testclient import TestClient

    from simubench.cli import main
    from simubench.engine.edit import to_yaml_data
    from simubench.server.app import create_app
    from simubench.server.manager import SessionManager

    (tmp_path / "cal.bench.yaml").write_text(yaml.safe_dump(to_yaml_data(bench())), encoding="utf-8")
    assert main(["assets", str(tmp_path / "cal.bench.yaml")]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "serial,model,status,cal_date,cal_due,certificate,notes"
    assert out[1] == "MY12345,SB-DMM-1,in_use,,2026-05-10,," and out[2].startswith("MY99999,,out_of_service")

    app = create_app(SessionManager(tmp_path, scpi_base_port=None), token="t", web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = "t"
        sid = c.post("/api/sessions", json={"bench_path": "cal.bench.yaml"}).json()["id"]
        reg = c.get(f"/api/sessions/{sid}/assets").json()
        assert reg["calendar"].startswith("2026-05-04T09:00")
        assert [a["due_in_days"] for a in reg["assets"]] == [6, None]
        c.post(f"/api/sessions/{sid}/clock/advance", json={"seconds": 7 * 86400})
        assert c.get(f"/api/sessions/{sid}/assets").json()["assets"][0]["due_in_days"] == -1  # overdue now


def test_true_values_only_when_revealed():
    for reveal in (False, True):
        s = open_bench(bench(reveal_true_values=reveal))
        s.write("src", b":SOUR:VOLT 1\n")
        values = s.instrument("dmm").components["meter"].readings()["values"]
        assert ("true_DC" in values) is reveal
        if reveal:
            assert values["true_DC"] == pytest.approx(1.0) and values["DC"] == pytest.approx(
                1.0 + 150e-6 + 200e-6
            )
        s.close()
