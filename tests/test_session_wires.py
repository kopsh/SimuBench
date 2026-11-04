"""Wire-end validation in the session (issue 598): a malformed endpoint is refused at bench
construction with the endpoint echoed — unknown owner, unknown port on an instrument, unknown port
on a bench part."""

from __future__ import annotations

import pytest

from simubench.engine.session import Session
from simubench.library.loader import DefinitionLibrary
from simubench.models.bench import BenchDefinition

LIB = DefinitionLibrary()


def open_bench(wire: dict) -> None:
    bench = BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/1",
            "name": "t",
            "instruments": {"awg": {"model": "sb-awg-2"}},
            "parts": {"rc": {"type": "rc_lowpass", "dut": True}},
            "wires": [wire],
        }
    )
    Session(bench, LIB.get, clock="stepped", seed=1)


def test_an_unknown_owner_is_refused_with_the_endpoint_echoed():
    with pytest.raises(ValueError) as exc:
        open_bench({"from": "awg.OUT1", "to": "node.V"})
    assert str(exc.value) == "wire end 'node.V': no instrument or DUT named 'node'"


def test_an_unknown_instrument_port_is_refused_with_the_endpoint_echoed():
    with pytest.raises(ValueError) as exc:
        open_bench({"from": "awg.NOSUCH", "to": "rc.IN"})
    assert str(exc.value) == "wire end 'awg.NOSUCH': instrument 'awg' has no port 'NOSUCH'"


def test_an_unknown_part_port_is_refused_with_the_endpoint_echoed():
    with pytest.raises(ValueError) as exc:
        open_bench({"from": "rc.NOSUCH", "to": "awg.OUT1"})
    assert str(exc.value) == "wire end 'rc.NOSUCH': rc_lowpass has no electrical port 'NOSUCH'"
