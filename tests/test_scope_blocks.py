"""The scope family's blocks on the fake hosts (issue #1724): run control, cursors and values,
driven without a bench. The family host protocols (scope_*_host.py) are the whole test surface —
the golden runs cover the behaviour on real models; these pin the blocks' own logic: the mode
words, the status events, the writes a command makes and the replies it formats."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from helpers import FakeScopeCapture, FakeScopeHost, raises_with
from simubench.library.components.scope_cursors import Cursors
from simubench.library.components.scope_recorder import Recorder
from simubench.library.components.scope_run import RunControl
from simubench.library.components.scope_values import Values
from simubench.scpi.errors import ScpiError

pytestmark = pytest.mark.unit


def record(triggered: bool = True, trigger_ns: int = 1_000) -> SimpleNamespace:
    """One record as the recorder's store and the acquisition event read it."""
    return SimpleNamespace(
        triggered=triggered,
        trigger_ns=trigger_ns if triggered else None,
        t_start_ns=0,
        dt_s=1e-6,
        points=1000,
        trigger_index=200,
        origin=-2e-4,
        deep=None,
        volts=np.zeros(1000),
    )


def spec(options: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(options=options, type="string")


# ---- run control (scope_run.py, with the real recorder block) -------------------------------------


def run_host(**params) -> FakeScopeHost:
    host = FakeScopeHost(
        params={
            "mode": "AUTO",
            "auto": ["AUTO"],
            "normal": ["NORMal"],
            "single": ["SINGle"],
            "stop": [],  # no stop mode: stopped is the run control's own flag
            "force": [],
            "completion": "immediate",
            "after_single": "",
            "reacquire": "explicit",
            "missing": "error",
            "while_waiting": "previous",
            "overhead_s": 1e-3,
            **params,
        },
        bound={"mode": "trigger.mode"},
    )
    host.owner.state["trigger.mode"] = "AUTO"
    host.capture = FakeScopeCapture(records={1: record()}, used=1_000, triggered=True)
    host.recorder = Recorder(host)
    return host


def test_run_state_and_flags_follow_the_mode():
    host = run_host()
    runner = RunControl(host)
    assert runner.run_state() == "RUN"
    assert runner.status_flags() == {"RUN": True, "WAIT": True}  # auto, no trigger in sight now
    runner.stop()
    assert runner.run_state() == "STOP"
    assert runner.status_flags() == {"RUN": False, "WAIT": False}


def test_a_single_acquisition_arms_acquires_and_reports():
    host = run_host()
    runner = RunControl(host)
    result = runner.run_mode(mode="SINGle")
    assert host.owner.events == ["ARM", "TRG", "DONE"]  # armed, then the record met its trigger
    assert result.duration_ns == 1_000  # the record's virtual time
    assert list(host.recorder.records) == [1]
    assert runner.run_state() == "STOP"  # done at once: nothing left armed
    assert host.owner.blocked == [("scope.fake", False)]  # the pending seam was cancelled, not armed


def test_status_events_for_an_untriggered_auto_sweep():
    host = run_host()
    host.capture = FakeScopeCapture(records={1: record(triggered=False)}, used=1_000, triggered=False)
    runner = RunControl(host)
    runner.acquire_now()  # a running auto scope takes its next record
    assert host.owner.events == ["DONE", "AUTO"]  # stored without its trigger: an auto trigger


def test_digitize_writes_the_channels_display_and_refuses_an_unknown_source():
    host = run_host()
    runner = RunControl(host)
    runner.digitize([b"CHANnel2"])
    assert ("ch2.display", True, "scpi", False) in host.owner.writes
    assert host.capture.computed == [[2]]
    with raises_with(ScpiError, "unknown source"):
        runner.digitize([b"CHANnel9"])


def test_opc_query_waits_and_a_write_arms_the_register():
    host = run_host()
    runner = RunControl(host)
    assert runner.opc(query=True).response == b"1"
    assert "opc_query" in host.owner.pending_log
    runner.opc()
    assert host.owner.pending_log[-2:] == ["wait_pending", "arm_opc"]


def test_the_mode_query_formats_the_bound_setting():
    host = run_host()
    runner = RunControl(host)
    assert runner.mode_text() == b"AUTO"  # trigger.mode as the definition formats it


# ---- cursors (scope_cursors.py) -------------------------------------------------------------------


def cursor_host(**params) -> FakeScopeHost:
    host = FakeScopeHost(
        params={
            "marker_mode": "MANual",
            "invalid_value": "UNDEF",
            "marker_x_units": "",
            "marker_y_units": "",
            "marker_x1": 1e-3,
            "marker_x2": 3.5e-3,
            "marker_y1": 0.5,
            "marker_y2": -0.5,
            **params,
        },
        bound={
            "marker_mode": "marker.mode",
            "marker_x1": "marker.x1",
            "marker_x2": "marker.x2",
            "marker_xa1": "marker.xa1",
            "marker_xa2": "marker.xa2",
        },
    )
    host.owner.specs = {
        "marker.mode": spec(["OFF", "MANual", "WAVeform"]),
        "marker.x1": spec(),
        "marker.x2": spec(),
        "marker.xa1": spec(),
        "marker.xa2": spec(),
    }
    return host


def test_x_delta_answers_the_formatted_difference_without_writes():
    host = cursor_host()
    result = Cursors(host).x_delta()
    assert result.response == host.owner.format_number(3.5e-3 - 1e-3)
    assert host.owner.writes == []


def test_a_position_write_moves_the_mode_with_the_definitions_word_then_stores():
    host = cursor_host()
    Cursors(host).op_x_position("X1", b"0.002")
    assert host.owner.writes == [
        ("marker.mode", "MANual", "scpi", True),  # the option word the definition spells
        ("marker.x1", 0.002, "scpi", True),
    ]


def test_a_y_position_is_refused_while_the_cursors_track():
    host = cursor_host(marker_mode="WAVeform")
    with raises_with(ScpiError, "track the waveform"):
        Cursors(host).op_y_position("Y1", b"0.5")


def test_units_use_stores_the_anchors_from_the_positions():
    host = cursor_host()
    Cursors(host).op_units_use("X")
    assert host.owner.writes == [
        ("marker.xa1", 1e-3, "scpi", True),
        ("marker.xa2", 3.5e-3, "scpi", True),
    ]


def test_a_cursor_read_puts_the_markers_in_the_tracking_mode():
    host = cursor_host()
    result = Cursors(host).cursor("STARt")
    assert host.owner.writes[0] == ("marker.mode", "WAVeform", "scpi", True)
    assert result.response == b",".join([host.owner.format_number(1e-3), host.owner.format_number(0.5)])


# ---- values (scope_values.py) ---------------------------------------------------------------------


def test_threshold_spec_reads_only_the_declared_settings():
    host = FakeScopeHost(params={"thresholds": "meas"})
    host.owner.specs = {
        "meas.general1.upper": spec(),
        "meas.general1.middle": spec(),
    }
    host.owner.state = {"meas.general1.upper": 0.9, "meas.general1.middle": 0.5}
    assert Values(host).threshold_spec(1, "general") == {"upper": 0.9, "middle": 0.5}


def test_a_threshold_field_without_a_setting_keeps_the_default():
    host = FakeScopeHost(params={"thresholds": "meas"})
    host.owner.specs = {"meas.general1.method": spec()}  # the model declares the method alone
    host.owner.state = {"meas.general1.method": "PERCent"}
    assert Values(host).threshold_spec(1, "general") == {"method": "PERCent"}
