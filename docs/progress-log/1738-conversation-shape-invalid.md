## 对话文件是合法 JSON 但形状错误时按领域错误答 422（Issue 1738）

| 完成条件 | 自检 |
| --- | --- |
| `store.load` 在解析后先做显式形状检查：顶层须是 mapping，`turns`/`messages` 须是列表，`files` 须是 mapping 且每个值须是 mapping；不符者与 JSON 语法错误一致地抛 `Invalid`，消息带文件名，文件保留不删；不用裸 `except Exception`，也不把 `TypeError`/`AttributeError` 混进捕获元组 | ✅ `tests/test_assistant.py::test_a_conversation_file_shaped_wrong_is_a_domain_error_and_is_never_emptied`（参数化四种内容：`{"files": {"a": 5}}`、`{"files": [1]}`、`{"turns": 3}`、`{"messages": 3}`；旧代码下前三例分别红于 `AttributeError: 'int' object has no attribute 'get'`、`AttributeError: 'list' object has no attribute 'items'`、`TypeError: 'int' object is not iterable`）；同一用例断言 `conversation()` 与 `send()` 两缝都答 `Invalid` 且文件仍在 |
| 既有对话文件行为不变：语法错误仍带解析位置、`#1725` 的整链路用例照常通过 | ✅ `uv run pytest tests/test_assistant.py -q`（38 过） |
| 不改 API/契约，一次改在 `store.load` 这一处声明行为 | ✅ `scripts/check.sh`（Python 5435 过、web 413 过；本次改动在各改动模型无关的共享代码 `src/simubench/server/`，`select_tests.py` 选全量，全量墙钟 215 s 属已知 #1668 基线，未新增慢用例） |
| 不写新 D：`#1725`「损坏文件按领域错误」的直接补全 | ✅ `docs/decisions.md` 未追加新节 |
