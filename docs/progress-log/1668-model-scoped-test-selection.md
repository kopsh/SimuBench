## 快速检查按改动的型号选测（Issue 1668、1680）

完成条件：PR 的快速检查耗时不随型号数线性增长；不删除、不跳过、不削弱任何用例；全量仍可在本地和 main 运行。

- `scripts/select_tests.py` 按改动路径选范围：只动某型号（定义、覆盖表、手册用例、带标记的测试文件，及 `extends` 它的型号）→ 这些型号全量，其余型号手册用例只跑前 3 条；动了公共代码、未知路径、main 或手动触发 → 全量。
- `conftest.py` 新增 `--model-scope`、`--model-smoke`；选择逻辑在 `tests/model_scope.py`（纯函数，有单元测试）；27 个型号测试文件加 `pytestmark = pytest.mark.model(...)`；`check_test_rules.py` 要求型号命名的测试文件有标记、标记的 id 必须是库里的型号。
- `scripts/check.sh` 与 CI 的两个 Python job 走同一选择；`SIMUBENCH_FULL=1` 强制全量；CI 日志第一行打印选测原因。
- 同步 `AGENTS.md`、`dev-workflow` 与 `real-model` skill、D49。

自检（本机沙箱，`-n 4`，只改 bk-8614 的情形）：全量 5375 个用例 124 s；选测 4335 个用例 94 s。选测去掉的是型号相关的 23% 用例；剩余的时间在引擎层与端到端测试（lfcal 场景 24 s、pytest 插件 15 s、示波器触发 12 s、server 10 s、命令等价 10 s 等，不属于任何单个型号），所以选测解决的是“随型号线性增长”，本身没有把基线压到 75 s——那部分见 #1668 的后续。
