## PR 也跑 Windows 的 Python 快速测试（Issue 1542）

| 完成条件 | 自检 |
| --- | --- |
| PR 的检查里有 `Python (windows-latest)`，合并以它为条件之一 | ✅ `ci.yml` 的 `python` 任务矩阵不再按 ref 选，PR 与自愈复跑也带 `windows-latest`；merge.yml 按整个 CI 运行的结论合并，无需改动。本 PR 自己的检查里即可看到该任务 |
| 文档与 skill 同步 | ✅ ci.yml 头注释、AGENTS.md、`dev-workflow` skill、D49「运行时机」 |
