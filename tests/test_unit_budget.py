"""The unit tier's time budget (D49, #1544): CPU time decides, the wall clock only bounds waiting."""

import pytest

from unit_budget import unit_budget_verdict as verdict

pytestmark = pytest.mark.unit


def test_a_load_spike_in_wall_time_alone_keeps_the_budget():
    # #1529: a 5 ms test that read 0.85 s on a busy runner, against the 0.3 s CI budget
    assert verdict(cpu_s=0.005, wall_s=0.85, budget_s=0.3) is None


def test_real_work_over_the_budget_fails_on_cpu_time():
    assert "CPU" in verdict(cpu_s=0.5, wall_s=0.5, budget_s=0.3)


def test_waiting_ten_budgets_long_fails_on_wall_time():
    assert "waited" in verdict(cpu_s=0.001, wall_s=3.5, budget_s=0.3)


def test_the_cyclic_gc_stays_out_of_the_measured_call_phase():
    # a full collection walks the whole worker's heap (0.1-0.6 s on CI) and fires in whichever test
    # allocates next: a 1 ms test read 0.627 s CPU. conftest holds it off during the call.
    import gc

    assert not gc.isenabled()
