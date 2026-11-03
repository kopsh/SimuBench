"""The RF modulator's master switch (#290, #347), block by block: with ``enabled`` off every
modulation passes the bare carrier; on, the lines follow Carson's rule with Bessel amplitudes; and
a modulation index set while the switch is off is refused (-222) when the switch is turned on,
which leaves the switch off."""

from __future__ import annotations

import numpy as np
import pytest
from scipy.special import jv

from simubench.engine.session import Session
from simubench.engine.signal import Signal
from simubench.library.components.rfgen import RfModulator, carson_orders
from simubench.library.loader import DefinitionLibrary
from simubench.models.bench import BenchDefinition
from simubench.scpi.errors import ScpiError

F0 = 10e6
RATE = 1e3


class _Source:
    """The modulation source with an exact clock: the tone at its set rate."""

    def frequency(self, kind: str) -> float:
        return RATE


class _Stub(RfModulator):
    """The block with its carrier and modulation source given directly."""

    def __init__(self, **params):
        self.overrides = {"fm_rate": RATE, "pm_rate": RATE, "am_rate": RATE, **params}
        self._params = {}
        self.owner = object()  # on_state only reaches the owner for an FM/PM conflict

    def param(self, key):
        return self.overrides.get(key, self.defaults[key])

    def input(self, port):
        return Signal.lines([F0], [1.0])

    def _source(self):
        return _Source()

    def bound_paths(self):
        return {key: key for key in self.defaults}


def _lines(stub: _Stub) -> tuple[np.ndarray, np.ndarray]:
    out = stub.output("out").merged()
    order = np.argsort(out.freq)
    return out.freq[order], np.abs(out.amp[order])


@pytest.mark.unit
@pytest.mark.parametrize(
    "params",
    [
        {"am": True, "am_depth": 50.0},
        {"fm": True, "fm_dev": 3e3},
        {"pm": True, "pm_dev": 2.0},
    ],
    ids=["AM", "FM", "PM"],
)
def test_with_the_master_switch_off_each_modulation_passes_the_bare_carrier(params):
    freq, amp = _lines(_Stub(enabled=False, **params))
    assert freq.tolist() == [F0]
    assert amp.tolist() == pytest.approx([1.0])


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kind", "params", "beta"),
    [("fm", {"fm": True, "fm_dev": 3e3}, 3.0), ("pm", {"pm": True, "pm_dev": 2.0}, 2.0)],
)
def test_with_the_switch_on_the_angle_modulation_is_bessel_lines_to_carsons_rule(kind, params, beta):
    stub = _Stub(enabled=True, **params)
    assert stub.angle_kind() == kind
    freq, amp = _lines(stub)
    k = carson_orders(beta)
    n = np.arange(-k, k + 1)
    assert freq.tolist() == pytest.approx((F0 + n * RATE).tolist())
    assert amp.tolist() == pytest.approx(np.abs(jv(n, beta)).tolist(), abs=1e-12)


@pytest.mark.unit
def test_with_the_switch_on_am_adds_two_sidebands_of_half_the_depth():
    freq, amp = _lines(_Stub(enabled=True, am=True, am_depth=50.0))
    assert freq.tolist() == pytest.approx([F0 - RATE, F0, F0 + RATE])
    assert amp.tolist() == pytest.approx([0.25, 1.0, 0.25])


@pytest.mark.unit
def test_a_large_index_set_with_the_switch_off_is_refused_when_it_is_turned_on():
    stub = _Stub(enabled=False, fm=True, fm_dev=1e6, fm_rate=10.0)  # beta = 1e5
    stub.on_state("fm_dev", 0.0, 1e6)  # off: nothing is expanded, so nothing is refused
    stub.overrides["enabled"] = True
    with pytest.raises(ScpiError) as err:
        stub.on_state("enabled", False, True)
    assert err.value.code == -222


def test_on_the_dsg815_the_refused_switch_stays_off_and_the_carrier_bare():
    """The instrument rolls the refused setting back: :MOD:STAT stays 0 after the -222."""
    lib = DefinitionLibrary()
    bench = BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "dsg815",
            "instruments": {
                "sg": {
                    "model": "rigol-dsg815",
                    "state": {"power.level": 0.0, "output.enabled": True, "freq.cw": F0},
                }
            },
        }
    )
    s = Session(bench, lambda model: lib.get(model), clock="stepped", seed=7)

    def q(cmd: str) -> str:
        s.write("sg", cmd.encode() + b"\n")
        out = s.read("sg")
        return out.decode().strip() if out else ""

    try:
        q(":FM:FREQ 10;:FM:DEV 1MHz;:FM:STAT ON")  # beta = 1e5 with the master switch off
        assert q(":SYST:ERR?") == '0,"No error"'
        q(":MOD:STAT ON")
        assert q(":SYST:ERR?").startswith("-222,")
        assert q(":MOD:STAT?") == "0"
        sg = s.instruments["sg"]
        comp, port = sg.ports["RF_OUT"]
        assert s.port_signal(sg.components[comp], port).merged().freq.size == 1
    finally:
        s.close()
