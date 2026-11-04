"""Instrument construction refuses malformed internal wiring and external port targets with the
original reference echoed (issue 598): the tolerant :func:`portref.split` must not turn a dotless
typo into ``unknown component ''``."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from simubench.engine.session import Session
from simubench.library.loader import load_yaml
from simubench.models.bench import BenchDefinition
from simubench.models.definition import InstrumentDefinition

ROOT = Path(__file__).resolve().parents[1]
DSO4 = load_yaml((ROOT / "validation" / "instruments" / "sb-dso-4.yaml").read_text(encoding="utf-8"))


def open_scope(definition: dict) -> None:
    model = InstrumentDefinition.model_validate(definition)
    bench = BenchDefinition.model_validate(
        {"schema": "simubench.bench/1", "name": "t", "instruments": {"scope": {"model": "sb-dso-4"}}}
    )
    Session(bench, {"sb-dso-4": model}.__getitem__, clock="stepped", seed=1)


def test_a_dotless_wiring_end_is_refused_with_the_reference_echoed():
    data = copy.deepcopy(DSO4)
    data["wiring"]["instrument"] = [["ch{n}.probe.out", "acq"]]
    with pytest.raises(ValueError) as exc:
        open_scope(data)
    assert "wiring end 'acq' is not <component>.<port>" in str(exc.value)


def test_a_dotless_port_target_is_refused_with_the_target_echoed():
    data = copy.deepcopy(DSO4)
    data["ports"]["instrument"] = {"AUX": "acq"}
    with pytest.raises(ValueError) as exc:
        open_scope(data)
    assert "port 'AUX' target 'acq' is not <component>.<port>" in str(exc.value)
