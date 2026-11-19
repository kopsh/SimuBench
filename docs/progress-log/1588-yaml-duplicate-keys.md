## 测试台与定义文件里重复的键报解析错误（Issue 1588）

| 完成条件 | 自检 |
| --- | --- |
| 任何层级的重复键都报错，指出键名和两处行号 | ✅ `YamlLoader.construct_mapping` 在建映射前比对键，抛 `ConstructorError`（`found duplicate key 'a' (first written on line 4) (line 5, column 3)`）；合并键 `<<` 带进来的键可被覆盖。`tests/test_definition_layers.py`：顶层、`instruments`、flow 映射内、经解析缓存的大文本，合并键 |
| JSON 测试台同样 | ✅ `load_json`（`object_pairs_hook`）取代读测试台、定义的 `json.loads`（loader、benchfiles、cli validate/migrate、助手）；`tests/test_definition_layers.py` |
| validate 返回 1，Web/API 能看到 | ✅ `simubench validate` 报 `E-READ` 并退出 1；`tests/test_bench_api.py` 的 #1406 用例加 YAML 与 JSON 的重复键：422，带文件名与行 |
| 现存文件没有重复键 | ✅ 遍历仓库全部 YAML：只有 `keysight-e36312a.yaml` 的 `ISUMmary<ch>:CONDition` 写了两次 `doc`（前一句此前被静默丢掉），已合成一条 |
