## 存储清点误判 files 孤儿并删活数据：目录名两套推导归一，`.simubench` 布局名收 workspace_layout 一处（Issue 1759，D95）

| 完成条件 | 自检 |
| --- | --- |
| 缺陷实证复现（开 issue 前完成）：tmp 工作区 `b.bench.yaml` + keysight-33622a，真会话写 `MMEM:DOWN` → `inventory` 报 `files b.bench orphan=True`（bench 文件仍在）→ `clean_orphans` 删掉 → 目录消失。根因=`api.py` `bench_files_dir` 的 `with_suffix("")` 产出 `b.bench`，`storage._live` 以 `stem+BENCH_SUFFIXES` 反查要求 `b`——台架文件必带双后缀，两套手写规则永不相遇，**每个 live 文件夹都判孤儿** | ✅ 复现脚本在 issue #1759 正文；两侧各有测试钉死自己（`test_file_system.py` 真会话钉写侧、`test_storage.py:36` fixture 想象裸 stem），无测试跨界 |
| `library/workspace_layout.py` 立一处推导：`bench_files_folder_rel`（剥最后一个后缀）+ `bench_files_candidates`（加回 `BENCH_FILE_TAILS`）+ 布局名 `STORAGE_ROOT`/`HISTORY_FOLDER`/`ASSISTANT_FOLDER`/`FILES_FOLDER`/`SETTINGS_NAME`；`api.bench_files_dir`（写侧）与 `storage._live`（清点侧）都改走它 | ✅ `tests/test_storage.py::test_the_folder_name_and_its_inverse_round_trip_through_the_bench_suffixes`（round-trip 不变量）；跨界回归 `tests/test_file_system.py::test_the_storage_inventory_counts_a_live_benchs_files_live`（真会话写 → inventory live → clean 不删；旧代码下 `orphan=True`、clean 删掉，实测红） |
| 六处 `.simubench`/kind 子目录名手拼点改 import：storage、server/history、assistant store、assistant config（SETTINGS+LEGACY_OVERRIDES）、benchfiles `_INTERNAL_DIRS`、api | ✅ `grep -rn '"\.simubench"' src --include='*.py'` 仅剩 workspace_layout 的常量定义与 docstring/help 文案、`paths.py` 的用户家目录遗留路径（另一个 root，非工作区布局） |
| 旧盘上已存在的 `b.bench` 形态文件夹恢复为 live，无迁移；`clean_orphans` 默认 kinds（assistant,files）不再删活数据 | ✅ `test_storage.py` fixture 改写侧真实形态（`kept.bench`/`sub/deep.bench`），全文件断言同步；`test_the_command_lists_first_and_deletes_only_with_yes`、`test_doctor_*`、`test_the_api_reports_and_cleans_*` 原样过 |
| D95 落 decisions.md（写入前 fetch origin main 确认编号未被占） | ✅ fetch 后 D94 仍最新；`docs/decisions.md` D95 一条；本条目一文件 |
