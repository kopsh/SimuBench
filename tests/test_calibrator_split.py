"""The split calibrator blocks (#329): the setpoint and the output control behave as the combined
calibrator_output did — the 5522A runs on them (its manual cases are the golden standard)."""

from __future__ import annotations

from simubench.library.loader import DefinitionLibrary


def test_the_5522a_uses_the_split_blocks_and_the_control_keeps_the_reading_keys():
    d = DefinitionLibrary().get("fluke-5522a")
    types = {c.name: c.type for c in d.components}
    assert types["dac"] == "calibrator_setpoint" and types["out"] == "calibrator_control"


def test_operate_settle_standby_through_the_split_pair():
    from simubench.api import open_bench
    from simubench.models.bench import BenchDefinition

    bench = BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "cal",
            "instruments": {"cal": {"model": "fluke-5522a", "deviation": "nominal"}},
            "parts": {"r": {"type": "load_resistor", "params": {"r": 1.0e6}}},
            "wires": [{"from": "cal.OUT", "to": "r.A"}],
            "run": {"clock": "command", "idle": "off"},
        }
    )
    s = open_bench(bench)

    def q(cmd: str) -> str:
        s.write("cal", (cmd + "\n").encode())
        out = s.read("cal")
        return out.decode().strip() if out else ""

    try:
        out = s.instruments["cal"].components["out"]
        q("OUT 1 V")
        assert out.readings()["values"]["output"] == "STBY"
        q("OPER")
        assert out.readings()["values"]["settling"] is True
        q("*OPC?")  # waits the 5 s settle in virtual time
        values = out.readings()["values"]
        assert values["output"] == "OPER"
        assert values["voltage"] > 0.999
        q("OUT 40 V")  # above 33 V: back to standby, OPER needed
        assert out.readings()["values"]["output"] == "STBY"
    finally:
        s.close()
