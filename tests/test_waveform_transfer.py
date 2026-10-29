"""The generic waveform transfer component (instrument-authoring.md 3.1, issue 43)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from simubench.library.components.scope import Record
from simubench.library.components.transfer import WaveformTransfer


def transfer(**params) -> WaveformTransfer:
    return WaveformTransfer("xfer", params, None, SimpleNamespace())  # type: ignore[arg-type]


def record(volts, scale=0.5, offset=0.0) -> Record:
    v = np.asarray(volts, dtype=float)
    return Record(
        channel=1,
        t_start_ns=0,
        dt_s=1e-6,
        trigger_index=0,
        volts=v,
        scale=scale,
        offset=offset,
        triggered=True,
    )


def test_selection_first_step_count_and_limit():
    assert transfer(first=2, step=3).selection(12).tolist() == [2, 5, 8, 11]
    assert transfer(first=2, step=3, count=2).selection(12).tolist() == [2, 5]
    assert transfer(max_points=4).selection(10).tolist() == [0, 1, 2, 3]
    assert transfer(count=6, max_points=4).selection(10).tolist() == [0, 1, 2, 3, 4, 5]  # count wins
    assert transfer(step=0).selection(3).tolist() == [0, 1, 2]  # 0 is 1
    assert transfer(first=20).selection(10).tolist() == []


def test_text_of_volts():
    t = transfer(encoding="text", values="volts", format="%.3f", separator=" ")
    assert t.encode(record([0.25, -1.5, 2.0])) == b"0.250 -1.500 2.000"
    assert t.encode(None) == b""


def test_ascii_is_nr3_with_commas():
    assert (
        transfer(encoding="ascii", values="volts", digits=3).encode(record([0.25, -2.0]))
        == b"+2.500E-01,-2.000E+00"
    )


def test_codes_scale_offset_and_limits():
    # 0.5 V/div and 32 codes per division: 1 V is 64 codes; the 8-bit ADC stops at -128..127.
    t = transfer(codes_per_div=32.0, adc_bits=8, width=8)
    assert t.codes(record([1.0, -1.0, 10.0, -10.0])).tolist() == [64, -64, 127, -128]
    # The record's offset setting, subtracted (sb) or added (Siglent).
    assert transfer(codes_per_div=32.0).codes(record([1.0], offset=0.5)).tolist() == [32]
    assert transfer(codes_per_div=32.0, offset="add").codes(record([1.0], offset=0.5)).tolist() == [96]


def test_codes_are_aligned_to_the_word():
    up = transfer(codes_per_div=100.0, adc_bits=10, width=16)  # 10 bits left-aligned in 16
    assert up.codes(record([0.5])).tolist() == [100 << 6]
    down = transfer(codes_per_div=100.0, adc_bits=10, width=8)  # the top 8 of 10 bits
    assert down.codes(record([0.5])).tolist() == [100 >> 2]
    assert transfer(codes_per_div=32.0, unsigned=True).codes(record([0.0])).tolist() == [128]


@pytest.mark.parametrize(
    ("params", "payload"),
    [
        ({"width": 8}, bytes([64, 0xC0])),
        ({"width": 8, "unsigned": True}, bytes([192, 64])),
        (
            {"width": 16, "adc_bits": 16, "codes_per_div": 32.0},
            (64).to_bytes(2, "little") + (-64).to_bytes(2, "little", signed=True),
        ),
        (
            {"width": 16, "adc_bits": 16, "codes_per_div": 32.0, "byteorder": "MSBFirst"},
            (64).to_bytes(2, "big") + (-64).to_bytes(2, "big", signed=True),
        ),
    ],
)
def test_blocks(params, payload):
    t = transfer(**{"codes_per_div": 32.0, **params})
    assert t.encode(record([1.0, -1.0])) == b"#1" + str(len(payload)).encode() + payload


def test_block_length_digits_and_trailer():
    t = transfer(codes_per_div=32.0, block_digits=9, trailer="\n")
    assert t.encode(record([1.0])) == b"#9000000001" + bytes([64]) + b"\n"


def test_an_operation_reply_goes_through_the_command_template():
    """C1:INSPECT? frames the transfer's numbers with its reply template, following COMM_HEADER."""
    from pathlib import Path

    from simubench.api import open_bench

    bench = Path(__file__).resolve().parents[1] / "examples" / "lowfreq-cal" / "lowfreq_cal.bench.yaml"
    session = open_bench(bench)
    try:

        def query(cmd: str) -> str:
            session.write("scope", cmd.encode() + b"\n")
            return session.read("scope").decode().strip()

        session.write("scope", b"WAVEFORM_SETUP SP,0,NP,3,FP,0,SN,0\n")
        short = query('C1:INSPECT? "DATA_ARRAY_1",FLOAT').split()
        assert short[:2] == ["C1:INSP", '"'] and short[-1] == '"' and len(short) == 6
        session.write("scope", b"CHDR LONG\n")
        assert query('C1:INSPECT? "DATA_ARRAY_1",FLOAT').startswith('C1:INSPECT " ')
        session.write("scope", b"CHDR OFF\n")
        assert query('C1:INSPECT? "DATA_ARRAY_1",FLOAT').startswith('" ')
    finally:
        session.close()
