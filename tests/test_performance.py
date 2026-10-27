"""Performance and stability budgets (M5). Budgets are about 10x what a laptop needs, so they catch
regressions (an accidental O(n^2), a leak) without failing on a slow CI runner."""

import time
import tracemalloc
from pathlib import Path

from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
BENCHES = ROOT / "acceptance" / "benches"


def test_long_command_run_stays_fast_and_bounded():
    s = open_bench(BENCHES / "awg_rc_scope.bench.yaml", clock="command")
    started = time.perf_counter()
    for i in range(10_000):
        s.write("awg", f":SOURce1:FREQuency {1000 + i % 50}\n".encode())
        s.write("awg", b":SOURce1:FREQuency?\n")
        s.read("awg")
    assert time.perf_counter() - started < 10.0  # ~0.9 s measured
    assert len(s.history) <= 20_000  # the in-memory event history is a ring, not a leak
    tracemalloc.start()
    before, _ = tracemalloc.get_traced_memory()
    for _ in range(5_000):
        s.write("awg", b"*IDN?\n")
        s.read("awg")
    after, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert after - before < 16 * 1024 * 1024  # steady state, no growth with the number of commands
    s.close()


def test_many_instruments():
    n = 20
    inst, duts, wires = {}, {}, []
    for k in range(n):
        inst[f"awg{k}"] = {"model": "sb-awg-2", "state": {"ch1.output": True, "ch1.frequency": 1000 + k}}
        inst[f"scope{k}"] = {"model": "sb-dso-4", "state": {"ch1.scale": 0.1}}
        duts[f"rc{k}"] = {"type": "rc_lowpass", "params": {"r": 1000, "c": 1e-7}}
        wires += [{"from": f"awg{k}.OUT1", "to": f"rc{k}.IN"}, {"from": f"rc{k}.OUT", "to": f"scope{k}.CH1"}]
    bench = BenchDefinition.model_validate({"name": "big", "instruments": inst, "duts": duts, "wires": wires})
    started = time.perf_counter()
    s = open_bench(bench, clock="command")
    assert time.perf_counter() - started < 2.0  # 40 instruments, 40 nets; ~0.07 s measured
    started = time.perf_counter()
    for i in range(100):
        s.write(f"awg{i % n}", f":SOURce1:FREQuency {2000 + i}\n".encode())
        s.write(f"scope{i % n}", b":DIGitize;:MEASure:VPP? CHANnel1\n")
        assert s.read(f"scope{i % n}")
    assert time.perf_counter() - started < 15.0
    assert s.stats["solves"] >= 100  # every change was solved, not served stale
    assert s.stats["solve_seconds"] / s.stats["solves"] < 0.05  # per-solve budget for a 40-node circuit
    s.close()


def test_an_hour_of_virtual_time_on_a_dynamic_instrument():
    s = open_bench(BENCHES / "lockin_rc.bench.yaml", clock="stepped")
    started = time.perf_counter()
    first = None
    for i in range(3600):
        s.advance(1.0)
        if i % 60 == 0:
            s.write("lia", b":MEASure:R?\n")
            r = float(s.read("lia") or b"nan")
            first = first or r
            assert abs(r - first) < 0.01 * first  # settled and steady over the hour
    assert time.perf_counter() - started < 30.0  # ~2 s measured
    assert s.now_ns == 3600 * 10**9
    s.close()
