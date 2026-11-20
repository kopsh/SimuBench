## 对话文件 files 内层字段形状错误也按领域错误答 422（Issue 1746）

| 完成条件 | 自检 |
| --- | --- |
| `files` 每个文件记录的 `before`/`after`/`kinds`/`content` 由 pydantic 模型 `_TurnFilesData` 校验：内层非 mapping（`{"before": 5}`、`{"before": "xy"}`）或值类型不符（`{"kinds": [1]}`）都与 JSON 语法错误一致地抛 `Invalid`，消息带文件名，文件保留不删；不用裸 `except Exception` | ✅ `tests/test_assistant.py::test_a_conversation_file_shaped_wrong_is_a_domain_error_and_is_never_emptied`（#1738 的既有参数化用例追加三例；旧代码下分别红于 `TypeError: 'int' object is not iterable`、`TypeError: cannot convert dictionary update sequence element #0 to a sequence`、`ValueError: dictionary update sequence element #0 has length 1`）；`conversation()` 与 `send()` 两缝都断言 `Invalid` 且文件仍在 |
| 合法对话文件的读写不变 | ✅ `uv run pytest tests/test_assistant.py -q`（44 过）；`scripts/check.sh` 全绿 |
| 一次改在 `TurnFiles.of` 这一处声明形状，不改 API/契约，不写新 D | ✅ `src/simubench/server/assistant/store.py`（`_TurnFilesData` + `TurnFiles.of`）；`docs/decisions.md` 未追加新节 |
