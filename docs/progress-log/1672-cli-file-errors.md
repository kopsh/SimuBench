## assets / replay / serve 读不到文件时一行报错（Issue 1672）

| 完成条件 | 自检 |
| --- | --- |
| `assets`、`replay` 对 `OSError` 与解析失败输出 `<文件>: <原因>`，退出码 1（与 `validate` 一致），不打印 traceback | ✅ `src/simubench/cli.py`：`_file_error`（原因里已有的文件名不重复）+ `_assets` / `_replay` 捕获 `(OSError, ValueError)` 返回 1；`replay` 原来对坏记录返回 2，本次统一为 1 |
| `serve` 在启动 uvicorn 前检查测试台文件，读不到就一行报错退出 1，不再进入 lifespan 失败路径 | ✅ `_serve` 在 `build_manager` 之前 `load_bench`，捕获 `(OSError, ValueError)`；`simubench serve /nonexistent.bench.yaml` 与 `simubench serve README.md` 均一行 stderr、退出 1 |
| 各命令退出码写进 `--help` | ✅ `serve` / `assets` / `replay` / `cases` 的 help 文本写明 1 与 2 的含义 |
| 每条命令补 CLI 测试（不存在的文件、不是 YAML 的文件） | ✅ `tests/test_ops.py::test_serve_names_a_bench_file_it_cannot_read`（serve）；`tests/test_calibration.py::test_assets_names_a_bench_file_it_cannot_read`（assets）；`tests/test_replay.py::test_cli_replay` 增补两条（replay，原 2 的期望改为 1） |
| `scripts/check.sh` 通过 | ✅ ruff / format / ty、pytest 5385 passed、check_tui_i18n / test_rules / layers / skills / progress_log、schemas 无 diff、web 406 passed |
| 75 秒预算 | ⚠️ 本沙箱 3 核下 pytest 213 s，属已在 #1668 → #1680 记录的预算与机型问题，与本次修复无关 |
