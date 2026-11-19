## progress-log 一条一个文件：并行的 PR 不再在日志末尾冲突（Issue 1505）

| 完成条件 | 自检 |
| --- | --- |
| 冲突的来源 | ✅ 近 150 个提交里 `docs/progress-log.md` 被改 31 次（全仓最多），每次都是末尾追加；两个并行 PR 必在末尾冲突，merge.yml 的 merges API 遇 409 只能退回。备选 `merge=union` 在临时仓库实测：两侧各追加一节时 xdiff 提出相同行，结果 `## A` 紧跟 `## B`、两节的表混在一起，弃用 |
| 归档封存 | ✅ `docs/progress-log.md` 开头注明封存，条目原样不动 |
| 新条目目录 | ✅ `docs/progress-log/<issue>-<short-name>.md`，README 说明；本条是第一条 |
| 检查 | ✅ `scripts/check_progress_log.py`：归档节数与 merge base 相等（少=丢历史 #380，多=写进了归档并提示改建文件）、条目文件命名与 `## ` 开头、merge base 上的条目文件不许删除；`tests/test_check_progress_log.py` 两例新测 |
| 写法同步 | ✅ AGENTS.md、progress.md「怎么记录」、dev-workflow / real-model / codebase-map skill、两份 issue 模板、product-plan.md、signal-chain.md、ci.yml 步骤名 |
| check.sh | ✅ 全绿 |
