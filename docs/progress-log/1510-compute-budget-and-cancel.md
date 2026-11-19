## 动作的计算预算与取消：重计算不再冻住整个会话（Issue 1510）

| 完成条件 | 自检 |
| --- | --- |
| 设计定案 | ✅ decisions.md D88：预算与取消只在检查点（每算一帧之前）生效；预算默认关（真实时间天然依赖机器快慢，与 D6/D12 冲突，不开就没有这个不确定性）；结局写进运行记录；回放不计时、按记录强制同一个动作以同样的原因结束。备选（默认开宽松预算、点数上限、锁外计算）与取舍写在 D88 里 |
| 预算 | ✅ `engine/budget.py` 的 `ComputeBudget`；`Session(compute_budget_s=)`、`open_bench(compute_budget_s=)`、`SessionManager(compute_budget_s=)`、`simubench serve --compute-budget SECONDS`；在 `_begin_action` 与 `view()` 武装，裸调用没有预算；超出按 `ComputeAborted`（`ModelLimit` 子类，−221）拒绝，会话照常可用 |
| 取消 | ✅ `Session.cancel()` 任意线程、不拿会话锁；`SessionManager.stop()` 在关闭前调用，关闭不再等长读取算完（`tests/test_access_registry.py` 锁顺序） |
| 记录与回放 | ✅ 动作里的中止发 `abort` 事件（reason / at / budget_s），视图不发（D55）；`replay.resend` 读到它就让下一个动作在第一个检查点以同样的原因结束，回放的实时预算与取消不生效；错误文字不含预算秒数，所以回放的 `:SYSTem:ERRor?` 逐字节相同 |
| 可复现的长读取用例 | ✅ `tests/test_compute_budget.py` 8 例：预算在两帧之间结束且会话照常读（30 块的链，只算了一部分）、无预算不受限、另一线程的取消在整段读取时长的 80% 之内结束、视图的中止无事件、裸调用无预算、`ComputeBudget` 的入参检查，以及基于 SCPI 的回放：预算取 `1e-9` 秒（第一个检查点必已超时，不靠计时）的运行回放逐字节一致，**删掉 `abort` 事件后回放对不上**（证明是记录在起作用） |
| 踩到的坑 | ✅ `ComputeAborted` 先写成 `@dataclass(frozen=True)`，穿过 `Session.view()` 时 `contextlib` 给它赋 `__traceback__` 被拒（`FrozenInstanceError`）；改成不装饰、用 `object.__setattr__` 存原因，理由写在类的文档里与 D88 |
| 生成物 | ✅ 事件种类多 `abort`：`schemas/events.schema.json`、`schemas/openapi.json`、`web/src/api/generated/events.ts`、`openapi.ts` 再生；`tests/test_events_contract.py` 的种类清单加一项；Web 里没有按事件种类穷举的代码 |
| 范围 | ✅ 检查点在 `EvalCache.frames`：覆盖经过帧的读取（深记录分块测量、采样式扫描）；不经过帧的解析求值（频谱仪谱线扫描）没有检查点，写在 D88 的影响里。锁外计算没做（D88 备选三），不是本 issue 的验收 |
