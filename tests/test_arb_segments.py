"""The waveform memory of segments (#879, #882), asserted on the component itself: the branches the
manual cases of keysight-m8194a (command level) and its flow test (the played wave) do not reach.

One bench, one flow (D49, #704): each ``# --`` act states what it checks; ``overrides`` widen the
component's limits the way another model's definition would (more segment numbers, 16-bit codes).
"""

from pathlib import Path

import numpy as np
import pytest

from simubench.api import open_bench
from simubench.scpi.errors import ScpiError

BENCH = Path(__file__).resolve().parents[1] / "examples" / "benches" / "keysight-m8194a.bench.yaml"


def code(error: pytest.ExceptionInfo[ScpiError]) -> int:
    return error.value.code


def test_the_segment_memory_flow():
    s = open_bench(BENCH, idle="off")
    try:
        arb = s.instruments["awg"].components["ch1.arb"]
        arb.overrides.update({"max_id": 3})
        # -- numbers: a segment outside 1..max_id is -222; a free number is handed out in order
        with pytest.raises(ScpiError) as e:
            arb.op_define(segment_id=4, length=128)
        assert code(e) == -222
        assert arb.op_define_new(length=128).response == b"1"
        assert arb.op_define_new(length=128, init=5).response == b"2"
        assert arb.op_catalog().response == b"1,128,2,128"
        assert arb.segments[2].codes.tolist() == [5] * 128
        # -- memory: all segments together hold max_samples (524288 here, 512 kSa); -225 beyond it
        with pytest.raises(ScpiError) as e:
            arb.op_define(segment_id=3, length=524288)
        assert code(e) == -225
        # replacing a segment frees its own samples first: segment 1 may grow to the rest
        arb.op_define(segment_id=1, length=524288 - 128)
        assert arb.segments[1].codes.size == 524288 - 128
        arb.op_delete(segment_id=1)
        # -- redefining keeps the name and the comment
        arb.op_name(segment_id=2, text=b'"keep"')
        arb.op_comment(segment_id=2, text=b'"note"')
        arb.op_define(segment_id=2, length=256, init=-7)
        assert arb.op_name(query=True, segment_id=2).response == b'"keep"'
        assert arb.op_comment(query=True, segment_id=2).response == b'"note"'
        assert arb.segments[2].codes.tolist() == [-7] * 256
        # -- the write-only variants hand out numbers too, and cannot be read back
        assert arb.op_define_wonly_new(length=128).response == b"1"
        with pytest.raises(ScpiError) as e:
            arb.op_data(query=True, segment_id=1, offset=0, data=[b"4"])
        assert code(e) == -221
        # -- no number is free: -225
        assert arb.op_define_new(length=128).response == b"3"
        with pytest.raises(ScpiError) as e:
            arb.op_define_new(length=128)
        assert code(e) == -225
        # -- names and comments have a length limit (-224)
        with pytest.raises(ScpiError) as e:
            arb.op_name(segment_id=2, text=b'"' + b"x" * 33 + b'"')
        assert code(e) == -224
        with pytest.raises(ScpiError) as e:
            arb.op_comment(segment_id=2, text=b'"' + b"x" * 257 + b'"')
        assert code(e) == -224
        # -- data: a block of signed bytes, then a list; both land at their offset; a bad code is -222
        arb.op_define(segment_id=2, length=128, init=0)
        arb.op_data(segment_id=2, offset=0, data=[b"#3004" + bytes([1, 2, 3, 255])])
        arb.op_data(segment_id=2, offset=4, data=[b"10", b"-128", b"127"])
        assert arb.segments[2].codes[:7].tolist() == [1, 2, 3, -1, 10, -128, 127]
        for bad in ([b"128"], [b"-129"]):
            with pytest.raises(ScpiError) as e:
                arb.op_data(segment_id=2, offset=0, data=bad)
            assert code(e) == -222
        # a block whose length is not a whole number of samples is not tested for 8-bit; a block
        # that says more bytes than it has is -160
        with pytest.raises(ScpiError) as e:
            arb.op_data(segment_id=2, offset=0, data=[b"#3010" + bytes(4)])
        assert code(e) == -160
        # -- offsets are checked against the segment, not against the order of the writes: the guide
        # asks for ascending offsets but gives no error for others, so the model writes where told
        arb.op_data(segment_id=2, offset=100, data=[b"1", b"2"])
        arb.op_data(segment_id=2, offset=0, data=[b"9"])
        assert arb.segments[2].codes[[0, 100, 101]].tolist() == [9, 1, 2]
        with pytest.raises(ScpiError) as e:
            arb.op_data(segment_id=2, offset=-1, data=[b"1"])
        assert code(e) == -222
        with pytest.raises(ScpiError) as e:
            arb.op_data(query=True, segment_id=2, offset=100, data=[b"29"])
        assert code(e) == -222
        # -- the read-back is a block of signed bytes
        assert arb.op_data(query=True, segment_id=2, offset=100, data=[b"2"]).response == b"#12\x01\x02"
        # -- 16-bit codes (a DAC wider than 8 bits): words, little endian
        arb.overrides.update({"dac_bits": 16})
        arb.op_define(segment_id=3, length=128, init=0)
        arb.op_data(segment_id=3, offset=0, data=[b"#14" + np.array([-2, 300], dtype="<i2").tobytes()])
        assert arb.segments[3].codes[:2].tolist() == [-2, 300]
        assert arb.op_data(query=True, segment_id=3, offset=0, data=[b"2"]).response == (
            b"#14" + np.array([-2, 300], dtype="<i2").tobytes()
        )
        with pytest.raises(ScpiError) as e:
            arb.op_data(segment_id=3, offset=0, data=[b"#13abc"])  # three bytes: not whole words
        assert code(e) == -161
        arb.overrides.update({"dac_bits": 8})
        # -- playing: which segment loops is ``play``; one that is not defined is silence
        arb.op_delete_all()
        arb.op_define(segment_id=2, length=128, init=127)
        arb.overrides.update({"play": 1})
        assert arb.points() == 0
        assert arb.output("out").amp.size == 0
        arb.overrides.update({"play": 2})
        assert arb.points() == 128
        assert arb.output("out").dc == pytest.approx(0.5)  # full-scale code x scale
    finally:
        s.close()
