"""BuiltinCommands directly (#1380): the IEEE 488.2 common commands and the model's built-in
headers — *IDN?/*OPT?/*RST/*CLS/*OPC, the status-byte round trip (ESE/SRE/ESR/STB), the SYSTem
error family and clock, and the reply-echo switches — on the shared fake host
(helpers.FakeHost, no bench). One behaviour per test, unit tier."""

from __future__ import annotations

import pytest

from helpers import dispatch_unit, seam_instrument
from simubench.models.definition import InstrumentDefinition
from simubench.scpi.errors import ScpiError

pytestmark = pytest.mark.unit

_FORMAT = {"number": "nr3", "digits": 6, "sign": True, "exponent_digits": 2, "error_code": "signed"}

DATA = {
    "schema": "simubench.instrument/1",
    "id": "builtin-demo",
    "manufacturer": "ACME",
    "model": "BUI",
    "class": "multimeter",
    "format": _FORMAT,
    "identity": {
        "idn": "ACME,{model},SN:{serial},FW:{firmware} OPT:{$disp.text}",
        "serial": "S1",
        "firmware": "F1",
        "options_reply": "{options}",
        "options_none": "NONE",
    },
    "options": [{"id": "P03"}],
    "state": {
        "out.level": {"type": "float", "unit": "V", "min": 0.0, "max": 10.0, "default": 1.0},
        "disp.text": {"type": "string", "default": "none"},
        "disp.echo": {"type": "bool", "default": False},
        "disp.long": {"type": "bool", "default": False},
    },
    "commands": [{"header": ":LEVel", "state": "out.level"}],
    "dialect": {"echo_header": "$disp.echo", "long_form": "$disp.long"},
}

DEFINITION = InstrumentDefinition.model_validate(DATA)


def _inst(**over):
    return seam_instrument(DEFINITION, **over)


def test_idn_answers_the_template_and_the_setting_it_names():
    inst = _inst()
    reply, _ = dispatch_unit(inst, "*IDN?")
    assert reply == b"ACME,BUI,SN:S1,FW:F1 OPT:none"
    inst.state["disp.text"] = "P03"
    reply, _ = dispatch_unit(inst, "*IDN?")
    assert reply == b"ACME,BUI,SN:S1,FW:F1 OPT:P03"


def test_idn_answers_the_instance_s_own_template():
    from helpers import FakeHost
    from simubench.engine.instrument import Instrument
    from simubench.models.bench import InstrumentInstance

    inst = Instrument("x", DEFINITION, InstrumentInstance(model="BUI", idn="OVR,{model}"), FakeHost())
    reply, _ = dispatch_unit(inst, "*IDN?")
    assert reply == b"OVR,BUI"


def test_opt_answers_the_fitted_options_in_the_definition_s_form():
    reply, _ = dispatch_unit(_inst(), "*OPT?")
    assert reply == b"NONE"
    reply, _ = dispatch_unit(_inst(options=["P03"]), "*OPT?")
    assert reply == b"P03"


def test_rst_puts_the_settings_back_and_clears_the_pending():
    inst = _inst()
    dispatch_unit(inst, ":LEVel 5")
    inst.register_pending("acq", inst.now_ns() + 10**9)
    assert inst.pending
    dispatch_unit(inst, "*RST")
    assert inst.state["out.level"] == 1.0 and inst.pending == {}


def test_cls_clears_the_queue_the_registers_and_the_armed_opc():
    inst = _inst()
    inst.push_error(-222, "overload")
    inst.register_pending("acq", inst.now_ns() + 10**9)
    inst.arm_opc()  # armed: nothing is due yet
    dispatch_unit(inst, "*CLS")
    assert len(inst.errors) == 0 and inst.esr == 0
    inst.session.now_ns += 10**10  # the operation is due now
    inst.settle_pending()
    assert inst.esr == 0  # the armed *OPC was disarmed: no OPC event


def test_opc_sets_the_bit_at_once_when_nothing_is_pending():
    inst = _inst()
    dispatch_unit(inst, "*OPC")
    assert inst.esr == 1


def test_opc_waits_for_what_is_pending():
    inst = _inst()
    inst.register_pending("acq", inst.now_ns() + 10**9)
    dispatch_unit(inst, "*OPC")
    assert inst.esr == 0
    inst.session.now_ns += 10**10
    dispatch_unit(inst, ":LEVel?")  # every dispatch settles what is due
    assert inst.esr == 1


def test_opc_query_is_silent_while_something_blocks():
    inst = _inst()
    inst.block("trg", True)
    reply, _used = dispatch_unit(inst, "*OPC?")
    assert reply is None
    inst.block("trg", False)
    reply, _ = dispatch_unit(inst, "*OPC?")
    assert reply == b"1"


def test_esr_reads_and_clears():
    inst = _inst()
    inst.push_error(-222, "overload")  # an execution error latches ESR bit 4
    reply, _ = dispatch_unit(inst, "*ESR?")
    assert reply == b"16"
    reply, _ = dispatch_unit(inst, "*ESR?")
    assert reply == b"0"


def test_ese_and_sre_round_trip():
    inst = _inst()
    dispatch_unit(inst, "*ESE 32")
    reply, _ = dispatch_unit(inst, "*ESE?")
    assert reply == b"32"
    dispatch_unit(inst, "*SRE 16")
    reply, _ = dispatch_unit(inst, "*SRE?")
    assert reply == b"16"


def test_stb_shows_the_error_and_the_unread_reply():
    inst = _inst()
    assert int(dispatch_unit(inst, "*STB?")[0]) & (1 << 2) == 0
    inst.push_error(-222, "overload")  # EAV (bit 2): the queue is not empty
    assert int(dispatch_unit(inst, "*STB?")[0]) & (1 << 2) == 1 << 2
    inst.receive(b":LEVel?\n")  # an unread reply waits on the instrument: MAV (bit 4)
    assert int(dispatch_unit(inst, "*STB?")[0]) & (1 << 4) == 1 << 4


def test_tst_answers_zero():
    reply, _ = dispatch_unit(_inst(), "*TST?")
    assert reply == b"0"


def test_an_enabled_esr_bit_summarizes_into_esb():
    inst = _inst()
    dispatch_unit(inst, "*ESE 16")
    inst.push_error(-222, "overload")  # bit 4, enabled: ESB (bit 5)
    assert int(dispatch_unit(inst, "*STB?")[0]) & (1 << 5) == 1 << 5


def test_the_error_family_answers_the_queue():
    inst = _inst()
    inst.push_error(-222, "overload")
    reply, _ = dispatch_unit(inst, ":SYSTem:ERRor?")
    assert reply == b'-222,"Data out of range; overload"'
    reply, _ = dispatch_unit(inst, ":SYSTem:ERRor:COUNt?")
    assert reply == b"0"
    reply, _ = dispatch_unit(inst, ":SYSTem:ERRor?")
    assert reply == b'+0,"No error"'


def test_the_error_family_dumps_the_queue():
    inst = _inst()
    inst.push_error(-222, "overload")
    inst.push_error(-113, ":NOSUCH")
    reply, _ = dispatch_unit(inst, ":SYSTem:ERRor:ALL?")
    text = reply.decode()
    # the dump reads the queue out, oldest first; the +0 tail is only for an empty queue
    assert text == '-222,"Data out of range; overload",-113,"Undefined header; :NOSUCH"'
    reply, _ = dispatch_unit(inst, ":SYSTem:ERRor:ALL?")
    assert reply == b'+0,"No error"'


def test_the_clock_commands_move_the_instrument_s_own_clock():
    inst = _inst()
    reply, _ = dispatch_unit(inst, ":SYSTem:DATE?")
    assert reply == b"2026,01,01"
    dispatch_unit(inst, ":SYSTem:TIME 12,0,0")
    reply, _ = dispatch_unit(inst, ":SYSTem:TIME?")
    assert reply.startswith(b"12,00,00")
    dispatch_unit(inst, ":SYSTem:DATE 2026,6,15")
    reply, _ = dispatch_unit(inst, ":SYSTem:DATE?")
    assert reply == b"2026,06,15"


def test_a_clock_command_with_the_wrong_number_of_parameters_is_refused():
    inst = _inst()
    with pytest.raises(ScpiError) as caught:
        dispatch_unit(inst, ":SYSTem:TIME 12,0")
    assert caught.value.code == -109


def test_the_echo_switches_read_their_settings():
    inst = _inst()
    assert inst._common.echo_mode() == "off" and inst._common.long_form() is False
    inst.state["disp.echo"] = True
    assert inst._common.echo_mode() == "short"
    inst.state["disp.long"] = True
    assert inst._common.echo_mode() == "long"  # short becomes long with the long form on


def test_a_builtin_the_definition_leaves_out_is_refused_as_excluded():
    definition = InstrumentDefinition.model_validate(
        {
            **DATA,
            "builtins": [
                "*IDN",
                "*RST",
                "*CLS",
                "*OPC",
                "*ESR",
                "*ESE",
                "*SRE",
                "*STB",
                "*OPT",
                "*WAI",
                "*TRG",
            ],
        }
    )
    inst = seam_instrument(definition)
    with pytest.raises(ScpiError) as caught:
        dispatch_unit(inst, "*TST?")
    assert caught.value.code == -113 and caught.value.reason == "builtin_excluded"
    reply, _ = dispatch_unit(inst, "*IDN?")
    assert reply.startswith(b"ACME,")
