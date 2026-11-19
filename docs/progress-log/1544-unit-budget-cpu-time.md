## D49 单元预算改按 CPU 时间计（Issue 1544）

| 完成条件 | 自检 |
| --- | --- |
| 纯计算的慢用例仍超预算；只被抢占的快用例不超 | ✅ `tests/unit_budget.py` 的 `unit_budget_verdict`：CPU 时间超预算即失败，墙钟只在超过 10 倍预算时失败（拦睡眠与等待）。`tests/test_unit_budget.py` 三条：#1529 的 5 ms / 0.85 s 墙钟通过、0.5 s CPU 失败、3.5 s 等待失败。临时探针本地实测：烧 0.2 s CPU 报「over budget」，睡 1.1 s 报「waited」 |
| D49 与 skill 同步 | ✅ D49 第 1 条、`dev-workflow` skill；CI 倍数（×3、Windows ×10）不变 |
| 慢用例之外的偶发超预算 | ✅ 全量 GC 落在恰好分配内存的那条用例上（CI worker 上 0.1–0.6 s，`test_the_guard_sees_the_boilerplate` 读到 0.627 s CPU，本地 0.65 s 跑完整个文件）。`conftest.py` 的 `pytest_runtest_call` 在调用阶段关闭循环 GC，teardown 再开；`tests/test_unit_budget.py` 加一条断言调用阶段 GC 已关 |
