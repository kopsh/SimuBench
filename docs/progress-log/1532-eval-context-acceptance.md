## 求值上下文：#1509 验收里的 grep 守卫与 D67 对齐（Issue 1532）

| 完成条件 | 自检 |
| --- | --- |
| 二选一并写明 | ✅ 选第二条：不加守卫，把 #1509 的验收与 D67 对齐。理由：守卫要禁止的「块读 `session.tune_hz` / `instant_ns`」在保留动态作用域（D67 备选二）之后是安全的读法——两个值都是 `EvalContext` 的字段，两个缓存的键是上下文整体；需要防的「读了键外的环境」由 `tests/test_stream.py` 的字段枚举两例锁定 |
| D67 / progress-log 写出修订 | ✅ decisions.md D67「求值的环境输入进缓存键」一段末尾写明对 #1509 第一条验收的修订与理由；`docs/progress-log/1509-eval-context.md` 加一行「对原验收的修订」 |
| `scripts/check.sh` | ✅ 全绿 |
