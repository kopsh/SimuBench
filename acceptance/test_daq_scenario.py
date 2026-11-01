"""DAQ scenario (E2 #191): the scan table closes the relays in ascending channel order, the
internal DMM reads the loaded references, and the card keeps one channel closed at a time.

The bench (``benches/daq_scan.bench.yaml``): 1 V / 2 V / 4 V references behind 1 MΩ on channels
101/102/103 of a 34901A. Closing a channel puts the internal DMM's 10 MΩ across the reference, so
each reading is the reference times 10/11.
"""

from __future__ import annotations

import pytest
import pyvisa

from .conftest import BENCHES


def open_daq():
    rm = pyvisa.ResourceManager(f"{BENCHES / 'daq_scan.bench.yaml'}@simubench")
    daq = rm.open_resource("daq", read_termination="\n", write_termination="\n")
    return rm, daq


@pytest.fixture
def daq():
    rm, instrument = open_daq()
    yield instrument
    rm.close()


def test_the_scan_reads_every_channel_with_the_loaded_reference(daq):
    daq.write(":READ?")
    parts = daq.read().split(",")
    channels = [int(float(x)) for x in parts[0::2]]
    readings = [float(x) for x in parts[1::2]]
    assert channels == [101, 102, 103]  # ascending, the 34970A's scan order
    assert readings[0] == pytest.approx(1.0 * 10 / 11, rel=1e-4)  # the loaded reference
    assert readings[1] / readings[0] == pytest.approx(2.0, rel=1e-4)
    assert daq.query(":DATA:POIN?") == "3"
    assert daq.query("ROUT:CLOS? (@101,102,103)") == "0,0,0"  # a scan leaves the relays open


def test_the_card_refuses_a_second_closed_channel(daq):
    daq.write("ROUT:CLOS (@101)")
    assert daq.query("ROUT:CLOS? (@101,102)") == "1,0"
    daq.write("ROUT:CLOS (@102)")
    assert daq.query(":SYST:ERR?").startswith("-221")
    assert daq.query("ROUT:CLOS? (@101,102)") == "1,0"
    daq.write("ROUT:OPEN (@101)")
    daq.write("ROUT:CLOS (@102)")
    daq.write(":MEAS?")
    assert float(daq.read()) == pytest.approx(2.0 * 10 / 11, rel=1e-4)
