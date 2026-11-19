## D49 单元预算改按 CPU 时间计（Issue 1544）

| 完成条件 | 自检 |
| --- | --- |
| 纯计算的慢用例仍超预算；只被抢占的快用例不超 | ✅ `tests/unit_budget.py` 的 `unit_budget_verdict`：CPU 时间超预算即失败，墙钟只在超过 10 倍预算时失败（拦睡眠与等待）。`tests/test_unit_budget.py` 三条：#1529 的 5 ms / 0.85 s 墙钟通过、0.5 s CPU 失败、3.5 s 等待失败。临时探针本地实测：烧 0.2 s CPU 报「over budget」，睡 1.1 s 报「waited」 |
| D49 与 skill 同步 | ✅ D49 第 1 条、`dev-workflow` skill；CI 倍数（×3、Windows ×10）不变 |
