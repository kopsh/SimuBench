## 默认内置命令：*PSC、:SYSTem:VERSion?（Issue 1559）

| 完成条件 | 自检 |
| --- | --- |
| `*PSC` / `*PSC?` 默认内置，`*RST` 不清 | ✅ `tests/test_builtin_commands.py`：读回、`ON`/`OFF`/数字、缺参数 -109、`*RST` 后不变；`tests/test_command_equivalence.py` 每个内置型号的路由与 `classify` 一致 |
| `:SYSTem:VERSion?` 默认回 `1999.0` | ✅ `tests/test_builtin_commands.py` |
| 受 `builtins` 名单限制 | ✅ `tests/test_builtin_commands.py`：名单里没有就 -113（`builtin_excluded`） |
| 覆盖表 | ✅ fluke-8588a、keysight-34465a 的 `*PSC` 改为 accepted（上电值手册未给，取 1），各加一条手册用例；DAQ970A 继承 34970A 的名单，不回答，表不动 |
| `:SYSTem:PRESet` 不做默认 | issue 草稿里有这一条；B2900 的 Preset 与 `*RST` 不同（连前面板显示也预置），没有手册依据的型号不猜，保持型号自己定义 |
