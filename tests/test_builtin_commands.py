"""BuiltinCommands directly (#1380): the IEEE 488.2 common commands and the model's built-in
headers — *IDN?/*OPT?/*RST/*CLS/*OPC, the status-byte round trip (ESE/SRE/ESR/STB), the SYSTem
error family and clock, and the reply-echo switches — on the shared fake host
(helpers.FakeHost, no bench). One behaviour per test, unit tier."""

from __future__ import annotations

import pytest

from helpers import dispatch_unit, seam_instrument
from simubench.engine.component import component_types
from simubench.engine.help_text import entries
from simubench.models.definition import InstrumentDefinition
from simubench.scpi.errors import ScpiError

pytestmark = pytest.mark.unit
component_types()  # load the built-in library here, not inside the first test's time budget (#1517)

_FORMAT = {"number": "nr3", "digits": 6, "sign": True, "exponent_digits": 2, "error_code": "signed"}

DATA = {
    "schema": "simubench.instrument/2",
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
    "syntax": {"echo_header": "$disp.echo", "long_form": "$disp.long"},
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
            ],
        }
    )
    inst = seam_instrument(definition)
    with pytest.raises(ScpiError) as caught:
        dispatch_unit(inst, "*TRG")
    assert caught.value.code == -113 and caught.value.reason == "builtin_excluded"
    reply, _ = dispatch_unit(inst, "*IDN?")
    assert reply.startswith(b"ACME,")


def _lines(reply: bytes, sep: bytes = b"\n") -> list[str]:
    return [line.decode() for line in reply.split(sep) if line]


def _with(**over):
    return seam_instrument(InstrumentDefinition.model_validate({**DATA, **over}))


def test_help_headers_lists_what_the_unit_executes_with_the_direction_markers():
    inst = _with(
        commands=[
            *DATA["commands"],
            {"header": ":FETCh", "op": "x.y", "set": False},
            {"header": ":ABORt", "op": "x.y", "query": False},
        ]
    )
    reply, _ = dispatch_unit(inst, ":SYSTem:HELP:HEADers?")  # a generic manufacturer: string, LF, markers
    lines = _lines(reply)
    assert ":LEVel" in lines  # both directions: no marker
    assert ":FETCh?/qonly/" in lines and ":ABORt/nquery/" in lines
    assert "*IDN?/qonly/" in lines and "*RST/nquery/" in lines and "*OPC" in lines
    assert ":SYSTem:ERRor[:NEXT]?/qonly/" in lines and ":SYSTem:HELP:HEADers?/qonly/" in lines


def test_help_headers_leaves_out_the_commands_of_an_option_the_unit_lacks():
    option_cmd = {"header": ":PULSe", "state": "out.level", "option": "P03"}
    fitted = seam_instrument(
        InstrumentDefinition.model_validate({**DATA, "commands": [*DATA["commands"], option_cmd]}),
        options=("P03",),
    )
    bare = _with(commands=[*DATA["commands"], option_cmd])
    assert ":PULSe" in _lines(dispatch_unit(fitted, ":SYST:HELP:HEAD?")[0])
    assert ":PULSe" not in _lines(dispatch_unit(bare, ":SYST:HELP:HEAD?")[0])


def test_the_keysight_preset_writes_a_definite_length_block_of_crlf_lines():
    inst = _with(help={"preset": "keysight"})
    reply, _ = dispatch_unit(inst, ":SYSTem:HELP:HEADers?")
    digits = int(reply[1:2])
    length = int(reply[2 : 2 + digits])
    payload = reply[2 + digits :]
    assert reply[:1] == b"#" and len(payload) == length  # the block header counts the payload
    assert "*IDN?/qonly/" in _lines(payload, b"\r\n") and payload.endswith(b"\r\n")


def test_the_manufacturer_picks_the_layout_when_the_preset_is_auto():
    keysight = _with(manufacturer="Keysight Technologies")
    rs = _with(manufacturer="Rohde & Schwarz")
    plain, _ = dispatch_unit(keysight, ":SYST:HELP:HEAD?")
    assert plain[:1] == b"#" and b"\r" not in plain and b"/qonly/" not in plain  # block, LF, no markers
    text, _ = dispatch_unit(rs, ":SYST:HELP:HEAD?")
    assert text[:1] != b"#" and "SYSTem:ERRor[:NEXT]?/qonly/" in _lines(text)  # no leading colon


def test_help_does_not_follow_the_builtins_list_but_its_own_switch():
    only_idn = _with(builtins=["*IDN"])
    reply, _ = dispatch_unit(only_idn, ":SYST:HELP:HEAD?")
    assert "*IDN?/qonly/" in _lines(reply) and "*TRG/nquery/" not in _lines(
        reply
    )  # the list still narrows the optional ones (the mandatory 488.2 ten always answer)
    off = _with(help={"enabled": False})
    with pytest.raises(ScpiError) as caught:
        dispatch_unit(off, ":SYST:HELP:HEAD?")
    assert caught.value.code == -113 and caught.value.reason == "builtin_excluded"
    assert not [e for e in entries(off.definition, ()) if "HELP" in e.header]  # nor listed


def test_a_command_of_the_definition_replaces_the_default_help_header():
    own = {"header": ":SYSTem:HELP:HEADers", "state": "disp.text", "set": False}
    inst = _with(commands=[*DATA["commands"], own])
    reply, _ = dispatch_unit(inst, ":SYST:HELP:HEAD?")
    assert reply == b'"none"'  # the model's own answer, not the generated list


def test_help_syntax_gives_one_command_in_any_short_or_long_form():
    inst = _with(
        commands=[
            *DATA["commands"],
            {"header": ":FREQuency", "state": "out.level", "params": [{"name": "f", "type": "float"}]},
        ]
    )
    reply, _ = dispatch_unit(inst, ':SYSTem:HELP:SYNTax? ":FREQ"')
    assert reply == b'":FREQuency[?] <numeric value>"'
    assert dispatch_unit(inst, ':SYST:HELP:SYNT? "*IDN"')[0] == b'"*IDN?"'


def test_help_syntax_refuses_an_unknown_command_and_a_missing_header():
    inst = _with()
    with pytest.raises(ScpiError) as caught:
        dispatch_unit(inst, ':SYST:HELP:SYNT? "NOPE"')
    assert caught.value.code == -113
    with pytest.raises(ScpiError) as caught:
        dispatch_unit(inst, ":SYST:HELP:SYNT?")
    assert caught.value.code == -109


def test_help_syntax_all_gives_a_line_per_command():
    inst = _with()
    lines = _lines(dispatch_unit(inst, ":SYST:HELP:SYNT:ALL?")[0])
    assert ":LEVel[?]" in lines and "*IDN?" in lines


def test_the_mandatory_488_2_commands_answer_whatever_builtins_lists():
    """IEEE 488.2 makes *CLS *ESE *ESR *IDN *OPC *RST *SRE *STB *TST *WAI mandatory: a ``builtins`` list cannot take them away."""
    inst = seam_instrument(InstrumentDefinition.model_validate({**DATA, "builtins": ["*IDN"]}))
    for query in ("*ESR?", "*ESE?", "*SRE?", "*STB?", "*TST?", "*OPC?"):
        reply, _ = dispatch_unit(inst, query)
        assert reply, query


def test_psc_round_trips_and_survives_a_reset():
    inst = _inst()
    assert dispatch_unit(inst, "*PSC?")[0] == b"1"  # a unit clears its status enables at power-on
    dispatch_unit(inst, "*PSC 0")
    assert dispatch_unit(inst, "*PSC?")[0] == b"0"
    dispatch_unit(inst, "*RST")
    assert dispatch_unit(inst, "*PSC?")[0] == b"0"  # *RST does not touch it
    dispatch_unit(inst, "*PSC ON")
    assert dispatch_unit(inst, "*PSC?")[0] == b"1"
    with pytest.raises(ScpiError) as caught:
        dispatch_unit(inst, "*PSC")
    assert caught.value.code == -109


def test_system_version_answers_the_scpi_version():
    assert dispatch_unit(_inst(), ":SYSTem:VERSion?")[0] == b"1999.0"


def test_the_builtins_list_governs_psc_and_version():
    inst = _with(builtins=["*IDN"])
    for message in ("*PSC?", ":SYSTem:VERSion?"):
        with pytest.raises(ScpiError) as caught:
            dispatch_unit(inst, message)
        assert caught.value.code == -113 and caught.value.reason == "builtin_excluded", message
