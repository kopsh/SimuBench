## PR 上把改动过的测试文件重复跑 5 遍（Issue 1543）

| 完成条件 | 自检 |
| --- | --- |
| PR 上重复跑改动过的 pytest 文件，且不拉长 PR 的等待 | ✅ `scripts/repeat_changed_tests.py` + ci.yml 的 `repeat` 任务（只在 PR 与自愈复跑上，ubuntu）：与 main 的合并基比较，取 `tests/`、`acceptance/` 下改动过的 `test_*.py`，跑 5 遍；超过 10 个文件不重复，4 分钟到点即停（按已跑完的结果判）。与 e2e（约 5–6 分钟）并行。本地实测：`tests/test_server.py` ×5 共 50 s |
| 演练：偶发失败能被抓到 | ✅ 本地放一个约 1/3 概率失败的临时测试（未提交），脚本在重复中报出失败并返回 1 |
| 规则写进 skill | ✅ `dev-workflow`：重复中失败即竞态，合并前修；并发代码的测试要把交错顺序写死。端到端的重复不在本次范围（共享工作区让部分 spec 不能重复跑） |
| 文档同步 | ✅ ci.yml 头注释、AGENTS.md、D49「运行时机」 |
