"""Measurement data saved as files (#1071): a file holds what the query would answer at that moment —
the DAQ970A's readings as FETCh? writes them, the UXR's record as :WAVeform:DATA? transfers it."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from simubench.api import open_bench
from simubench.library.components.datafiles import infiniium_bin, readings_table, waveform_text

ROOT = Path(__file__).resolve().parents[1]


def ask(s, name: str, text: str) -> bytes | None:
    s.write(name, text.encode() + b"\n")
    return s.read(name) if "?" in text.split()[0] else None


def block(reply: bytes) -> bytes:
    n = int(reply[1:2])
    size = int(reply[2 : 2 + n])
    return reply[2 + n : 2 + n + size]


def test_readings_go_in_one_column_per_channel_and_one_row_per_sweep():
    rows = [{101: "1", 102: "2"}, {101: "3", 102: "4"}]
    assert readings_table(rows, [101, 102], "Chan {channel}", ";") == "Chan 101;Chan 102\r\n1;2\r\n3;4\r\n"


def test_the_daq_file_holds_the_readings_fetch_answers():
    with open_bench(ROOT / "examples/benches/keysight-daq970a.bench.yaml", idle="off") as s:
        ask(s, "daq", "CONF:VOLT:DC 10,(@101:103)")
        ask(s, "daq", "TRIG:COUN 3")
        ask(s, "daq", "INIT")
        fetched = ask(s, "daq", "FETC?").decode().strip().split(",")
        ask(s, "daq", 'MMEM:STOR:DATA RDG_STORE,"INT:\\Run"')
        lines = block(ask(s, "daq", 'MMEM:UPL? "INT:\\Run.csv"')).decode().split("\r\n")
        assert lines[0] == "Chan 101,Chan 102,Chan 103"
        assert [x for line in lines[1:] if line for x in line.split(",")] == fetched
        assert len([line for line in lines[1:] if line]) == 3  # three sweeps


def test_the_scope_files_hold_the_record_the_transfer_reads():
    with open_bench(ROOT / "tests/models/keysight-uxr0334b/bench.yaml", idle="off") as s:
        ask(s, "scope", ":TIMebase:SCALe 20E-9")
        ask(s, "scope", ":DIGitize CHANnel1")
        rec, _ = s.instrument("scope").components["acq"].read_record(1, start=False)
        ask(s, "scope", ':DISK:SAVE:WAVeform CHANnel1,"w",CSV')
        ask(s, "scope", ':DISK:SAVE:WAVeform CHANnel1,"w",BIN')
        text = block(ask(s, "scope", ':DISK:GETFILE? "w.csv"')).decode().strip().split("\r\n")
        volts = np.array([float(line.split(",")[1]) for line in text])
        assert volts == pytest.approx(rec.volts, abs=5e-7 * max(1.0, float(np.abs(rec.volts).max())))
        times = np.array([float(line.split(",")[0]) for line in text])
        assert times[0] == pytest.approx(rec.origin) and np.diff(times).mean() == pytest.approx(rec.dt_s)
        raw = block(ask(s, "scope", ':DISK:GETFILE? "w.bin"'))
        assert raw[:4] == b"AG10" and struct.unpack("<ii", raw[4:12]) == (len(raw), 1)
        header_size, kind, buffers, points = struct.unpack("<4i", raw[12:28])
        assert (header_size, kind, buffers, points) == (140, 1, 1, rec.volts.size)
        x_inc, x_org = struct.unpack("<2d", raw[12 + 32 : 12 + 48])
        assert (x_inc, x_org) == (rec.dt_s, rec.origin)
        data = np.frombuffer(raw[12 + 140 + 12 :], dtype="<f4")
        assert np.array_equal(data, rec.volts.astype(np.float32))


def test_formats_and_sources_not_written_are_refused():
    with open_bench(ROOT / "tests/models/keysight-uxr0334b/bench.yaml", idle="off") as s:
        for cmd, code in (
            (':DISK:SAVE:WAVeform CHANnel1,"a",MATlab', b"-257"),
            (':DISK:SAVE:WAVeform CHANnel1,"a.wfm"', b"-257"),
            (':DISK:SAVE:WAVeform WMEMory1,"a",CSV', b"-224"),
        ):
            ask(s, "scope", cmd)
            assert ask(s, "scope", ":SYSTem:ERRor?").strip() == code
        assert ask(s, "scope", ":DISK:DIRectory?") == b"0\n"


def test_text_and_binary_layouts():
    v = np.array([0.5, -0.25])
    assert waveform_text(v, -1e-9, 1e-9, ",", {"Points": "2"}).splitlines()[0] == "Points,2"
    raw = infiniium_bin(
        v, x_origin=0.0, x_increment=1.0, x_display_range=10.0, x_display_origin=0.0,
        date="", time="", frame="", label="", count=1,
    )  # fmt: skip
    assert len(raw) == 12 + 140 + 12 + 2 * 4
