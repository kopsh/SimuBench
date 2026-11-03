"""A query reply as an IEEE 488.2 definite-length block (#415): the DAQ970A answers ROUTe:SCAN? with
the scan list in a block, an empty list being ``#13(@)``. The 34970A definition is patched here."""

from __future__ import annotations

from pathlib import Path

import yaml

from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "src" / "simubench" / "library" / "instruments"


def test_value_block_wraps_the_reply(tmp_path):
    d = yaml.safe_load((LIB / "keysight-34970a.yaml").read_text("utf-8"))
    for c in d["commands"]:
        if c["header"] == "ROUTe:SCAN":
            c["reply"] = "{value_block}"
    (tmp_path / "keysight-34970a.yaml").write_text(yaml.safe_dump(d, allow_unicode=True), "utf-8")
    bench = BenchDefinition.model_validate(
        {"schema": "simubench.bench/2", "name": "b", "instruments": {"daq": {"model": "keysight-34970a"}}}
    )
    session = open_bench(bench, definition_dirs=[tmp_path])
    try:

        def q(text: str) -> bytes:
            session.write("daq", (text + "\n").encode())
            reply = session.read("daq")
            assert reply is not None
            return reply.rstrip(b"\n")

        assert q("ROUT:SCAN?") == b"#13(@)"
        session.write("daq", b"ROUT:SCAN (@101:103,110)\n")
        reply = q("ROUT:SCAN?")
        assert reply.startswith(b"#2")
        body = reply[4:]
        assert int(reply[2:4]) == len(body)
        assert body.startswith(b"(@101") and body.endswith(b"110)")
    finally:
        session.close()
