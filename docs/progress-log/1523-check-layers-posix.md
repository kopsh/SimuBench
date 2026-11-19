## `check_layers.py` 报告的路径在 Windows 上也用 `/`（Issue 1523）

| 完成条件 | 自检 |
| --- | --- |
| main 变绿 | ✅ main 的 Windows 任务红在 `tests/test_check_layers.py` 3 例：脚本用 `str(path.relative_to(root))`，Windows 上输出 `\`；改为 `.as_posix()`，各平台输出一致，单测不改 |
