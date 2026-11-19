## JSON 测试台的重复键报错指向第二次写的位置（Issue 1596）

| 完成条件 | 自检 |
| --- | --- |
| JSON 测试台/定义中重复键的报错带正确的行、列（至少第二处的位置） | ✅ `library/loader.py`：`json` 的 `object_pairs_hook` 拿不到位置，发现重复时（错误路径）再扫一遍原文 `first_duplicate_key`（跟着字符串与转义、嵌套，每个对象里 `{` 或 `,` 之后的字符串是键），取文本顺序里第一个重复的键，报错位置是它第二次写的偏移；信息像 YAML 的一样写出首次写在第几行：`duplicate key 'a' (first written on line 5): line 6 column 5 (char 114)`（issue 里是 `line 1 column 1 (char 0)`） |
| `simubench validate` 与 422 用例断言行号不是 1 | ✅ `simubench validate` 对 issue 的 `dup.bench.json` 报 `line 6 column 5`；`tests/test_server.py::test_a_json_bench_with_a_key_written_twice_is_422_pointing_at_the_second_writing`：`GET /api/benches/file` 回 422，detail 含首次行 5 与 `line 6 column 5` |
| 单元测试断言多行文件里位置落在重复键所在行 | ✅ `tests/test_definition_layers.py`：位置 (6, 5) 与「第一次写在第 4 行」；内外层嵌套时报文本顺序里第一个重复；字符串值、转义的引号、别的对象里同名的键都不算重复 |
