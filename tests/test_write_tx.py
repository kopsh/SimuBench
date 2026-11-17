"""The write transaction (#1379): a batch of writes validated, judged by the constraints and the
dynamic limits, applied with its watchers and taken back whole when refused -- tested against the
narrow host interface (specs/state/watchers/notify/limits) directly, without a session."""

from __future__ import annotations

from typing import Any

import pytest

from simubench.engine.write_tx import WriteEvent, apply_writes
from simubench.models.definition import ConstraintSpec, StateSpec
from simubench.scpi.errors import ScpiError

FREQ = StateSpec(type="float", unit="HZ", min=0.0, max=120.0e6, default=1000.0)
LEVEL = StateSpec(type="float", unit="V", min=-5.0, max=5.0, default=0.0, electrical=True)
MODE = StateSpec(type="enum", options=["RTIMe", "SEGMented"], default="RTIMe")
COUNT = StateSpec(type="int", min=1, max=10, default=1)
CAL = StateSpec(type="float", default=1.0, readonly=True)
OUT = StateSpec(type="bool", default=False, electrical=True)


class Watcher:
    """A component double: told of changes, able to adjust, refuse or write as a side effect."""

    def __init__(self, uid: str, paths: tuple[str, ...], owner: FakeInstrument | None = None) -> None:
        self.uid = uid
        self.paths = paths
        self.owner = owner
        self.seen: list[tuple[str | None, str, Any, Any]] = []
        self.checked = 0
        self.adjust: dict[str, Any] = {}
        self.refuse: str | None = None  # the path whose write it refuses
        self.side_effect: tuple[str, Any] | None = None  # written when any of its paths changes
        self.limits: dict[str, tuple[float | None, float | None]] = {}

    def on_state(self, key: str | None, path: str, old: Any, new: Any) -> Any:
        self.seen.append((key, path, old, new))
        if self.refuse == path:
            raise ScpiError(-221, "the combination is not allowed")
        if self.side_effect is not None and self.owner is not None:
            target, value = self.side_effect
            self.owner.set(target, value, source="watcher")
        return self.adjust.get(path)

    def check_settings(self) -> None:
        self.checked += 1
        if self.owner is not None and self.owner.broken:
            raise ScpiError(-222, "the settings together make no sense")

    def state_limits(self, path: str) -> tuple[float | None, float | None] | None:
        return self.limits.get(path)


class FakeInstrument:
    """The narrow host: specs, state, watchers, notification, dynamic limits, constraints."""

    def __init__(self, specs: dict[str, StateSpec], constraints: list[ConstraintSpec] | None = None):
        self.specs: dict[str, StateSpec] = specs
        self.state: dict[str, Any] = {p: s.default for p, s in specs.items()}
        self.constraints = constraints or []
        self.watchers_of: dict[str, list[Watcher]] = {}
        self.param_of: dict[tuple[str, str], str | None] = {}
        self.sent: list[WriteEvent] = []
        self.write_events: list[WriteEvent] | None = None
        self.broken = False  # check_settings refuses while True
        self.named: list[tuple[str, str]] = []

    def bind(self, watcher: Watcher, key: str | None) -> Watcher:
        for path in watcher.paths:
            self.watchers_of.setdefault(path, []).append(watcher)
            self.param_of.setdefault((path, watcher.uid), key)
        return watcher

    # ---- the WriteHost protocol -----------------------------------------------------------------

    def watchers(self, path: str) -> list[Watcher]:
        return self.watchers_of.get(path, [])

    def watch_param(self, path: str, uid: str) -> str | None:
        return self.param_of.get((path, uid))

    def limits(self, path: str) -> tuple[float | None, float | None]:
        lo, hi = self.specs[path].min, self.specs[path].max
        for comp in self.watchers(path):
            narrowed = comp.state_limits(path)
            if narrowed is None:
                continue
            if narrowed[0] is not None:
                lo = narrowed[0] if lo is None else max(lo, narrowed[0])
            if narrowed[1] is not None:
                hi = narrowed[1] if hi is None else min(hi, narrowed[1])
        return lo, hi

    def notify_write(self, path: str, old: Any, new: Any, *, electrical: bool, source: str) -> None:
        self.sent.append(WriteEvent(path, old, new, electrical, source))

    def name_error(self, err: ScpiError, path: str, spec: StateSpec | None) -> ScpiError:
        self.named.append((path, str(err.code)))
        return err

    # ---- convenience: one write as the instrument's set_state makes it --------------------------

    def set(
        self, path: str, value: Any, *, judged: bool = False, internal: bool = False, source: str = "t"
    ) -> None:
        apply_writes(self, {path: value}, source=source, judged=judged, internal=internal)


def refuse() -> ConstraintSpec:
    return ConstraintSpec(when={"mode": "SEGMented"}, forbid={"level": [1.0]}, error=-221)


def coerce() -> ConstraintSpec:
    return ConstraintSpec(
        when={"mode": "SEGMented"}, forbid={"level": [1.0]}, action="coerce", coerce={"level": 0.0}
    )


# ---- the pass ------------------------------------------------------------------------------------


def test_every_write_is_validated_before_any_is_applied():
    inst = FakeInstrument({"freq": FREQ, "level": LEVEL})
    inst.state["freq"] = 10.0
    with pytest.raises(ScpiError) as err:
        apply_writes(inst, [("freq", 20.0), ("level", 11.0)], source="t")
    assert err.value.code == -222
    assert inst.state["freq"] == 10.0 and inst.state["level"] == 0.0  # nothing applied
    assert inst.sent == []
    with pytest.raises(ScpiError) as err:
        apply_writes(inst, [("freq", 20.0), ("level", 11.0)], source="t", name_error=inst.name_error)
    assert inst.named == [("level", "-222")]  # the refused value's setting is named


def test_a_later_entry_for_the_same_path_wins():
    inst = FakeInstrument({"freq": FREQ})
    apply_writes(inst, [("freq", 10.0), ("freq", 20.0)], source="t")
    assert inst.state["freq"] == 20.0
    assert [(e.path, e.old, e.new) for e in inst.sent] == [("freq", 1000.0, 20.0)]


def test_an_unknown_path_is_refused():
    inst = FakeInstrument({"freq": FREQ})
    with pytest.raises(ScpiError) as err:
        apply_writes(inst, {"nowhere": 1.0}, source="t")
    assert err.value.code == -113


def test_a_read_only_setting_takes_the_instruments_own_write_only():
    inst = FakeInstrument({"cal": CAL})
    with pytest.raises(ScpiError) as err:
        inst.set("cal", 2.0)
    assert err.value.code == -221
    inst.set("cal", 2.0, internal=True)
    assert inst.state["cal"] == 2.0


def test_a_write_that_changes_nothing_is_a_no_op():
    inst = FakeInstrument({"freq": FREQ})
    inst.state["freq"] = 10.0
    watcher = inst.bind(Watcher("w", ("freq",)), "level")
    apply_writes(inst, {"freq": 10.0}, source="t", judged=True)
    assert watcher.seen == [] and inst.sent == [] and watcher.checked == 0


def test_only_the_surviving_writes_are_sent_in_the_order_they_were_applied():
    inst = FakeInstrument({"freq": FREQ, "count": COUNT, "out": OUT})
    apply_writes(inst, [("out", True), ("freq", 5.0), ("count", 3)], source="scpi")
    assert [(e.path, e.old, e.new, e.electrical, e.source) for e in inst.sent] == [
        ("out", False, True, True, "scpi"),
        ("freq", 1000.0, 5.0, False, "scpi"),
        ("count", 1, 3, False, "scpi"),
    ]


# ---- the watchers --------------------------------------------------------------------------------


def test_the_watchers_of_each_written_path_are_told_with_the_parameter_and_the_change():
    inst = FakeInstrument({"freq": FREQ})
    bound = inst.bind(Watcher("bound", ("freq",)), "level")
    watched = inst.bind(Watcher("watched", ("freq",)), None)
    inst.set("freq", 5.0)
    assert bound.seen == [("level", "freq", 1000.0, 5.0)]
    assert watched.seen == [(None, "freq", 1000.0, 5.0)]


def test_a_watchers_adjustment_is_kept_and_validated():
    inst = FakeInstrument({"freq": FREQ})
    watcher = inst.bind(Watcher("w", ("freq",)), "level")
    watcher.adjust["freq"] = 7.0
    inst.set("freq", 5.0)
    assert inst.state["freq"] == 7.0
    assert [(e.path, e.old, e.new) for e in inst.sent] == [("freq", 1000.0, 7.0)]


def test_a_refusing_write_takes_its_side_effects_back_and_sends_nothing():
    inst = FakeInstrument({"freq": FREQ, "level": LEVEL, "out": OUT})
    first = inst.bind(Watcher("first", ("freq",)), None)
    first.side_effect = ("level", 2.0)
    first.owner = inst
    second = inst.bind(Watcher("second", ("level",)), None)
    second.refuse = "level"
    inst.state["freq"] = 10.0
    with pytest.raises(ScpiError):
        apply_writes(inst, [("freq", 20.0), ("level", 1.0)], source="t")
    assert inst.state["freq"] == 10.0 and inst.state["level"] == 0.0  # the side effect too
    assert inst.sent == []  # nothing became visible


def test_a_watchers_own_write_is_a_transaction_of_one_inside_the_batch():
    """Its events join the batch's collector and go out only when the batch survives."""
    inst = FakeInstrument({"freq": FREQ, "level": LEVEL})
    watcher = inst.bind(Watcher("w", ("freq",)), None)
    watcher.side_effect = ("level", 2.0)
    watcher.owner = inst
    inst.set("freq", 20.0)
    assert [(e.path, e.old, e.new) for e in inst.sent] == [
        ("level", 0.0, 2.0),  # the side effect was applied during the write...
        ("freq", 1000.0, 20.0),  # ...and the write's own event follows it
    ]


# ---- the judged batch ----------------------------------------------------------------------------


def test_a_refusing_constraint_takes_the_batch_down_before_anything_is_written():
    inst = FakeInstrument({"mode": MODE, "level": LEVEL}, constraints=[refuse()])
    with pytest.raises(ScpiError) as err:
        apply_writes(inst, {"mode": "SEGMented", "level": 1.0}, source="t", judged=True)
    assert err.value.code == -221
    assert inst.state["mode"] == "RTIMe" and inst.state["level"] == 0.0
    assert inst.sent == []


def test_the_constraints_judge_the_state_the_batch_leaves_together():
    inst = FakeInstrument({"mode": MODE, "level": LEVEL}, constraints=[refuse()])
    with pytest.raises(ScpiError):
        apply_writes(inst, {"mode": "SEGMented", "level": 1.0}, source="t", judged=True)  # broken at its end
    assert inst.state["mode"] == "RTIMe" and inst.state["level"] == 0.0  # …refused whole
    inst.state["mode"] = "SEGMented"
    apply_writes(inst, {"level": 1.0, "mode": "RTIMe"}, source="t", judged=True)  # legal at its end:
    assert inst.state["mode"] == "RTIMe" and inst.state["level"] == 1.0  # level alone would conflict


def test_a_coercing_constraint_adds_its_write_to_the_batch():
    inst = FakeInstrument({"mode": MODE, "level": LEVEL}, constraints=[coerce()])
    apply_writes(inst, {"mode": "SEGMented", "level": 1.0}, source="t", judged=True)
    assert inst.state["mode"] == "SEGMented" and inst.state["level"] == 0.0
    assert [(e.path, e.old, e.new) for e in inst.sent] == [
        ("mode", "RTIMe", "SEGMented"),  # the command's own write; the coerced level was already 0.0
    ]


def test_an_unjudged_write_is_not_settled():
    inst = FakeInstrument({"mode": MODE, "level": LEVEL}, constraints=[refuse()])
    inst.set("level", 1.0)  # the instrument's own write
    assert inst.state["level"] == 1.0


def test_the_dynamic_limits_hold_for_the_state_a_judged_batch_leaves():
    inst = FakeInstrument({"freq": FREQ, "level": LEVEL})
    watcher = inst.bind(Watcher("w", ("freq",)), None)
    watcher.limits["freq"] = (None, 1.0e6)  # the function narrows the frequency
    inst.state["freq"] = 5.0e5
    with pytest.raises(ScpiError) as err:
        inst.set("freq", 2.0e6, judged=True)
    assert err.value.code == -222
    assert inst.state["freq"] == 5.0e5  # the watchers ran, and the batch still went back whole
    inst.set("freq", 2.0e6)  # the instrument's own write is not judged
    assert inst.state["freq"] == 2.0e6


def test_the_dynamic_limits_are_judged_on_the_batches_end_state():
    """A batch that also writes what the limits depend on is judged with it in: count 1 narrows
    the frequency, count 5 does not."""
    inst = FakeInstrument({"freq": FREQ, "count": COUNT})

    class Narrower(Watcher):
        def state_limits(self, path: str) -> tuple[float | None, float | None] | None:
            if path == "freq" and self.owner.state.get("count") == 1:
                return (None, 1.0e6)
            return None

    inst.bind(Narrower("w", ("count", "freq")), None).owner = inst  # it watches freq to narrow it
    inst.state["count"] = 5
    with pytest.raises(ScpiError):
        apply_writes(inst, {"count": 1, "freq": 2.0e6}, source="t", judged=True)  # the end state narrows
    assert inst.state["count"] == 5 and inst.state["freq"] == 1000.0  # back whole
    apply_writes(inst, {"count": 5, "freq": 2.0e6}, source="t", judged=True)  # count stays 5: free
    assert inst.state["freq"] == 2.0e6


def test_the_combination_check_runs_once_per_batch_for_the_watchers_of_the_written_paths():
    inst = FakeInstrument({"freq": FREQ, "level": LEVEL})
    freq_watcher = inst.bind(Watcher("wf", ("freq",)), None)
    level_watcher = inst.bind(Watcher("wl", ("level",)), None)
    both = inst.bind(Watcher("wb", ("freq", "level")), None)
    apply_writes(inst, [("freq", 5.0), ("level", 1.0)], source="t", judged=True)
    assert (freq_watcher.checked, level_watcher.checked, both.checked) == (1, 1, 1)
    inst.set("freq", 6.0, judged=True)  # one write is one batch
    assert freq_watcher.checked == 2 and level_watcher.checked == 1
    apply_writes(inst, [("freq", 7.0), ("level", 2.0)], source="t")  # unjudged: no check
    assert freq_watcher.checked == 2 and level_watcher.checked == 1 and both.checked == 2


def test_a_broken_combination_takes_the_whole_judged_batch_back():
    inst = FakeInstrument({"freq": FREQ, "level": LEVEL})
    watcher = inst.bind(Watcher("w", ("freq",)), None)
    watcher.owner = inst
    watcher.side_effect = ("level", 2.0)
    inst.broken = True
    with pytest.raises(ScpiError) as err:
        inst.set("freq", 5.0, judged=True)
    assert err.value.code == -222
    assert inst.state["freq"] == 1000.0 and inst.state["level"] == 0.0
    assert inst.sent == []


# ---- the reinforced batch (a setup put back, #436) ----------------------------------------------


def test_a_reinforced_batch_writes_its_values_again_after_the_watchers():
    inst = FakeInstrument({"freq": FREQ, "level": LEVEL})
    watcher = inst.bind(Watcher("w", ("freq",)), None)
    watcher.side_effect = ("level", 2.0)
    watcher.owner = inst
    setup = {"freq": 5.0, "level": 1.0}
    apply_writes(inst, setup, source="scpi", judged=True, reinforce=True, batch_event="*RCL")
    assert inst.state["level"] == 1.0  # what the watcher wrote as a side effect did not survive
    assert inst.state["freq"] == 5.0
    assert [(e.path, e.old, e.new) for e in inst.sent] == [
        ("level", 1.0, 2.0),  # the side effect's event (the setup's level was already in), as on...
        ("*RCL", None, None),  # ...and one event for the whole setup
    ]
    assert inst.sent[-1].electrical and inst.sent[-1].source == "scpi"


def test_a_reinforced_batch_announces_itself_even_when_it_changes_nothing():
    inst = FakeInstrument({"freq": FREQ})
    inst.state["freq"] = 5.0
    apply_writes(inst, {"freq": 5.0}, source="scpi", reinforce=True, batch_event="*RCL")
    assert [(e.path, e.old, e.new) for e in inst.sent] == [("*RCL", None, None)]


def test_a_refused_setup_changes_nothing_and_announces_nothing():
    inst = FakeInstrument({"freq": FREQ, "level": LEVEL}, constraints=[refuse()])
    inst.state["level"] = 1.0
    watcher = inst.bind(Watcher("w", ("freq",)), None)
    watcher.side_effect = ("level", 2.0)
    watcher.owner = inst
    with pytest.raises(ScpiError):
        apply_writes(
            inst,
            {"freq": 5.0, "mode": "SEGMented"},
            source="scpi",
            judged=True,
            reinforce=True,
            batch_event="*RCL",
        )
    assert inst.state["freq"] == 1000.0 and inst.state["level"] == 1.0
    assert inst.sent == []
