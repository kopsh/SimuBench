"""HiSLIP acceptance (D41): the bench reached as TCPIP INSTR resources through pyvisa-py.

The same scripts that drive hardware connect to ``TCPIP0::127.0.0.1::hislip<i>,<port>::INSTR``
without a single change. Locks and Trigger need no VISA client support (pyvisa-py implements
neither viLock nor viAssertTrigger), so they are covered by the raw protocol tests in
tests/test_hislip.py.
"""

from __future__ import annotations

import pytest

from .conftest import open_rig, run_over_hislip

BENCH = "awg_rc_scope.bench.yaml"


def test_identity_matches_the_in_process_backend():
    def hislip(awg, scope):
        return awg.query("*IDN?"), scope.query("*IDN?")

    rm, awg, scope = open_rig(BENCH)
    expected = (awg.query("*IDN?"), scope.query("*IDN?"))
    rm.close()
    assert run_over_hislip(BENCH, hislip) == expected


def test_status_byte_and_clear_match_the_in_process_backend():
    def drive(inst):
        # ESE without SRE: ESB is set while RQS stays down, so no AsyncServiceRequest is in
        # flight — pyvisa-py's read_stb cannot cope with unsolicited SRQ messages. The SRQ
        # behaviour itself is covered by the raw client in tests/test_hislip.py.
        inst.write("*CLS;*ESE 1;*OPC")
        stb = inst.read_stb()  # ESB: the same bits both ways
        assert inst.query(":SYSTem:ERRor?").startswith("0,")
        inst.clear()  # device clear over HiSLIP: same handshake, same effect
        return stb, inst.read_stb()  # the status byte survives the clear the same way

    rm, _awg, scope = open_rig(BENCH)
    expected = drive(scope)
    rm.close()
    assert run_over_hislip(BENCH, lambda awg, scope: drive(scope)) == expected


def test_binary_block_with_a_line_feed_is_not_cut():
    """The reason HiSLIP exists: 0x0A inside a response keeps the message boundary intact."""

    def hislip(awg, scope):
        awg.write(":OUTPut1:STATe ON")
        for freq in (100, 137, 211, 333, 512, 777, 1000, 1499):
            awg.write(f":SOURce1:FREQuency {freq}")
            scope.write(":CHANnel1:SCALe 0.05;:TIMebase:SCALe 0.001;:DIGitize")
            scope.read_termination = None
            scope.write(":WAVeform:DATA?")
            full = scope.read_raw()
            scope.read_termination = "\n"
            if 0x0A in full[6:-1]:
                assert len(full) == 2007 and full[:6] == b"#42000"
                # Even with the termination character armed, nothing cuts mid-block over
                # HiSLIP: DataEnd marks the boundary, so the whole message arrives intact.
                scope.write(":WAVeform:DATA?")
                with_term = scope.read_raw()
                assert with_term == full
                return
        pytest.skip("no capture with a 0x0A byte found")

    run_over_hislip(BENCH, hislip)


def test_two_sessions_and_a_held_query():
    """A second client connects to the same instrument while the first one keeps working."""

    def hislip(awg, scope):
        import pyvisa

        other = pyvisa.ResourceManager("@py")  # a second script, its own manager
        try:
            second = other.open_resource(
                scope.resource_name, read_termination="\n", write_termination="\n", timeout=500
            )
            assert second.query("*OPC?") == "1"
            assert scope.query("*OPC?") == "1"
        finally:
            other.close()

    run_over_hislip(BENCH, hislip)
