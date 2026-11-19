## PyVISA 后端拒绝两台仪表共用一个地址（Issue 1597）

| 完成条件 | 自检 |
| --- | --- |
| 构造后端时任一地址形式冲突即报错，并指出两台仪表与冲突的资源名 | ✅ `SimuBenchVisaLibrary._remember` 统一登记规范化地址、网关形式、`served` 与名称别名；`tests/test_pyvisa_buses.py` 四条用例分别覆盖直接地址、网关形式、`served`、名称别名，均断言错误文本含两台仪表名与冲突地址 |
| `set_address` 改成与另一台相同时返回的 `diagnostics` 含 `E-ADDRESS-DUPLICATE` | ✅ `tests/test_bench_api.py`：`POST /api/benches/edit` 返回 200，`/instruments/scope/address` 上的 `E-ADDRESS-DUPLICATE` 指名 `awg`；`web/e2e/edit.spec.ts` 在问题面板看到同一条 |
| 内置示例与 `examples/benches` 全部仍能加载 | ✅ `scripts/check.sh`（`tests/test_validate.py` 校验每个示例测试台无诊断，其余测试台用例全部通过） |
