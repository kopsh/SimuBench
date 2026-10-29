"""C7: ``simubench verdicts`` checks a calibration script's statements against the truth.

The script runs the way it would on real instruments (it never imports SimuBench): it sets the
reference, reads the DUT, states a conformity decision per point and writes a report. The checker
replays the run record, matches the report's rows to the DUT's read replies in order and judges
each statement with the true input behind the reading.
"""

import json
from pathlib import Path

import pytest

from simubench.api import open_bench
from simubench.cli import main
from simubench.engine.verdicts import VerdictError, check_verdicts
from simubench.models.bench import BenchDefinition

MAP = """\
instrument: dmm
columns:
  nominal: nominal
  reading: reading
  low: limit_low
  high: limit_high
  decision: verdict
decisions:
  PASS: pass
  FAIL: fail
  "N/A": no_statement
"""

#: The bench: a 1 kΩ lead between the reference and the meter's 10 MΩ input loads the reference by
#: 1/(1 + 1e-4); the DUT meter's hidden gain error is +400 ppm. With the setpoint P:
#:     true input = P · L,  L = 10e6 / 10.001e6
#:     reading    = true input · (1 + 400e-6)     (replied with 7 decimals, NR3)
#:     script error = reading − P,  true error = reading − true input = true input · 400e-6
#: The script decides with a guard band: limits ±250 µV, U = 50 µV, so it accepts within ±200 µV
#: and rejects beyond ±250 µV. 0.65 V: script error +195 µV → pass, true error +260 µV → outside:
#: a false accept. 0.75 V: script error +225 µV → in the guard band → no statement (withheld).
#: 1.2 V: script error +360 µV → fail, true error +480 µV → outside: correct.
POINTS = (0.65, 0.75, 1.2)
LOAD = 10e6 / (10e6 + 1e3)
GAIN = 400e-6
U = 50e-6
#: The readings as the script saw them, at the reply's printed digits.
REPLIES = {0.65: "0.65019498", 0.75: "0.75022498", 1.2: "1.2003600"}


def bench() -> BenchDefinition:
    return BenchDefinition.model_validate(
        {
            "name": "verdicts",
            "instruments": {
                "src": {"model": "sb-smu-1", "state": {"output.enabled": True}},
                "dmm": {"model": "sb-dmm-1", "hidden": {"meter": {"gain_error_ppm": 400.0, "offset": 0.0}}},
            },
            "parts": {"lead": {"type": "resistor", "params": {"r": 1000.0}}},
            "wires": [
                {"from": "src.HI", "to": "lead.A"},
                {"from": "lead.B", "to": "dmm.IN"},
            ],
            "run": {"clock": "command", "start": "2026-05-04T09:00:00Z"},
        }
    )


def run_script(tmp_path: Path, points: tuple[float, ...] = POINTS) -> tuple[Path, Path]:
    """The calibration script, written the way it would run against real instruments."""
    s = open_bench(bench(), record_dir=tmp_path)
    rows = ["nominal,reading,limit_low,limit_high,verdict"]
    s.scpi("dmm", "*IDN?")
    for p in points:
        s.write("src", f":SOURce:VOLTage:LEVel {p}\n".encode())
        reply = s.scpi("dmm", ":MEASure:VOLTage:DC?")
        reading = float(reply)
        low, high = -250e-6, 250e-6
        if low + U <= reading - p <= high - U:
            statement = "PASS"
        elif reading - p < low - U or reading - p > high + U:
            statement = "FAIL"
        else:
            statement = "N/A"
        rows.append(f"{p},{reply},{low},{high},{statement}")
    s.scpi("dmm", ":SYSTem:ERR?")  # not a reading: no number, the checker skips it
    s.close()
    record = next(tmp_path.glob("run-*.jsonl"))
    report = tmp_path / "report.csv"
    report.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return record, report


def write_map(tmp_path: Path, text: str = MAP) -> Path:
    path = tmp_path / "verdict-map.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_script_statements_are_judged_against_the_truth(tmp_path):
    record, report = run_script(tmp_path)
    checked = check_verdicts(record, report, write_map(tmp_path))
    assert [p.verdict for p in checked.points] == ["false_accept", "withheld", "correct"]
    for p, nominal in zip(checked.points, POINTS, strict=True):
        true_input = nominal * LOAD
        assert p.true_input == pytest.approx(true_input, rel=1e-8)
        # The script's reading is the reply (quantized to its digits), the truth is the solver's:
        # true error = reading − true input, agreeing with the hand calculation to a nanovolt.
        assert p.true_error == pytest.approx(float(REPLIES[nominal]) - true_input, abs=2e-9)
        assert p.reference_deviation == pytest.approx(true_input - nominal, abs=2e-9)
    assert checked.summary == {"correct": 1, "false_accept": 1, "false_reject": 0, "withheld": 1}
    assert not checked.ok and checked.warnings == []


def test_cli_exit_codes_and_json(tmp_path, capsys):
    record, report = run_script(tmp_path)
    write_map(tmp_path)
    assert main(["verdicts", str(record), str(report), "--map", str(tmp_path / "verdict-map.yaml")]) == 1
    assert "1 false_accept" in capsys.readouterr().out
    assert (
        main(["verdicts", str(record), str(report), "--map", str(tmp_path / "verdict-map.yaml"), "--json"])
        == 1
    )
    parsed = json.loads(capsys.readouterr().out)
    assert [p["verdict"] for p in parsed["points"]] == ["false_accept", "withheld", "correct"]
    # The same points, decided honestly from the truth the script cannot see: every statement right.
    honest = tmp_path / "honest.csv"
    honest.write_text(
        "nominal,reading,limit_low,limit_high,verdict\n"
        f"0.65,{REPLIES[0.65]},-0.00025,0.00025,FAIL\n"
        f"0.75,{REPLIES[0.75]},-0.00025,0.00025,N/A\n"
        f"1.2,{REPLIES[1.2]},-0.00025,0.00025,FAIL\n",
        encoding="utf-8",
    )
    assert main(["verdicts", str(record), str(honest), "--map", str(tmp_path / "verdict-map.yaml")]) == 0
    assert "every statement agrees with the truth" in capsys.readouterr().out


def test_a_row_that_fits_no_reply_is_reported_not_guessed(tmp_path, capsys):
    record, report = run_script(tmp_path)
    write_map(tmp_path)
    wrong = tmp_path / "wrong.csv"
    lines = report.read_text().splitlines()
    name, reading, rest = lines[1].split(",")[:1], float(lines[1].split(",")[1]), lines[1].split(",")[2:]
    wrong.write_text(
        lines[0] + "\n" + f"{name[0]},{reading + 1e-3}," + ",".join(rest) + "\n", encoding="utf-8"
    )
    with pytest.raises(VerdictError, match=r"row 1.*does not match dmm's next numeric reply"):
        check_verdicts(record, wrong, tmp_path / "verdict-map.yaml")
    assert main(["verdicts", str(record), str(wrong), "--map", str(tmp_path / "verdict-map.yaml")]) == 2
    assert "does not match" in capsys.readouterr().err


def test_rows_are_matched_in_order_not_by_value(tmp_path):
    record, report = run_script(tmp_path)
    lines = report.read_text().splitlines()
    swapped = tmp_path / "swapped.csv"
    swapped.write_text("\n".join([lines[0], lines[2], lines[1], lines[3]]) + "\n", encoding="utf-8")
    with pytest.raises(VerdictError, match="row 1"):
        check_verdicts(record, swapped, write_map(tmp_path))


def test_leftover_replies_are_a_warning(tmp_path):
    record, report = run_script(tmp_path)
    short = tmp_path / "short.csv"
    lines = report.read_text().splitlines()
    short.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    checked = check_verdicts(record, short, write_map(tmp_path))
    assert len(checked.points) == 2
    assert checked.warnings == ["dmm: 1 numeric read replies in the record match no report row"]


def test_instrument_by_column(tmp_path):
    record, report = run_script(tmp_path)
    with_column = tmp_path / "with-column.csv"
    lines = report.read_text().splitlines()
    with_column.write_text(
        "\n".join(
            [lines[0].replace("nominal", "dut,nominal", 1)]
            + [",".join(["dmm", *line.split(",")]) for line in lines[1:]]
        )
        + "\n",
        encoding="utf-8",
    )
    mapping = tmp_path / "map.yaml"
    mapping.write_text(MAP.replace("instrument: dmm", "instrument: dut"), encoding="utf-8")
    checked = check_verdicts(record, with_column, mapping)
    assert [p.instrument for p in checked.points] == ["dmm"] * 3


@pytest.mark.parametrize(
    "report_text, match",
    [
        ("nominal,reading,limit_low,verdict\n", "no 'limit_high' column"),
        (
            f"nominal,reading,limit_low,limit_high,verdict\n0.65,{REPLIES[0.65]},-0.00025,0.00025,MAYBE\n",
            "MAYBE",
        ),
        ("nominal,reading,limit_low,limit_high,verdict\n0.65,x,-0.00025,0.00025,PASS\n", "not a number"),
        (
            f"nominal,reading,limit_low,limit_high,verdict\n0.65,{REPLIES[0.65]},0.00025,-0.00025,PASS\n",
            "reversed",
        ),
    ],
)
def test_report_and_map_errors_name_the_row(tmp_path, report_text, match):
    record, _ = run_script(tmp_path)
    bad = tmp_path / "bad.csv"
    bad.write_text(report_text, encoding="utf-8")
    with pytest.raises(VerdictError, match=match):
        check_verdicts(record, bad, write_map(tmp_path))


def test_the_map_needs_a_known_instrument(tmp_path):
    record, report = run_script(tmp_path)
    with pytest.raises(VerdictError, match="neither a column"):
        check_verdicts(
            record, report, write_map(tmp_path, MAP.replace("instrument: dmm", "instrument: dmm2"))
        )


def test_not_a_record(tmp_path):
    junk = tmp_path / "junk.jsonl"
    junk.write_text("{}\n", encoding="utf-8")
    report = tmp_path / "report.csv"
    report.write_text("nominal,reading,limit_low,limit_high,verdict\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not a SimuBench run record"):
        check_verdicts(junk, report, write_map(tmp_path))


async def test_mcp_check_verdicts(tmp_path):
    pytest.importorskip("mcp")
    from mcp import Client

    from simubench.mcp_server import build_server
    from simubench.server.manager import SessionManager

    record, report = run_script(tmp_path)
    write_map(tmp_path)
    manager = SessionManager(tmp_path, scpi_base_port=None)
    tools = {}

    async def call(server, **args):
        async with Client(server) as c:
            tools.update({t.name: t for t in (await c.list_tools()).tools})
            r = await c.call_tool("check_verdicts", args)
            if r.is_error:
                return {"error": r.content[0].text}
            sc = r.structured_content
            return sc.get("result", sc) if set(sc) == {"result"} else sc

    off = build_server(manager, ui_url="http://127.0.0.1:1/?token=x")
    refused = await call(off, record=record.name, report=report.name, map="verdict-map.yaml")
    assert "--allow-control" in refused["error"]
    on = build_server(manager, ui_url="http://127.0.0.1:1/?token=x", allow_control=True)
    result = await call(on, record=record.name, report=report.name, map="verdict-map.yaml")
    assert [p["verdict"] for p in result["points"]] == ["false_accept", "withheld", "correct"]
    assert not result["ok"]
    schema = json.dumps(tools["check_verdicts"].input_schema)
    assert "record" in schema and "report" in schema
