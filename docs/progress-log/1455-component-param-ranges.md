## 元件参数的物理范围：校验、故障注入与漂移都不再把求解器推到异常（Issue 1455）

| 完成条件 | 自检 |
| --- | --- |
| 元件类型能声明参数范围，校验、检查器、MCP 共用 | ✅ `Component.param_ranges`（`ParamRange`：最小/最大，端点可开，如电阻 `r > 0`）；组件目录的 `ParamInfo` 带 `minimum`/`maximum`/`min_open`/`max_open`（`schemas/` 与 `web/src/api/generated` 已重新生成）；`tests/test_component_catalog.py` |
| `validate` 对越界参数报 `E-PARAM-RANGE`，指向 `/parts/<id>/params/<name>` | ✅ `neg.bench.yaml`（`r: -5`、`r: 0`）校验退出码 1，两条诊断分别指向两个元件；`tests/test_validate.py`：电阻 ≤0、电容<0、容差 −100 % 各一例 |
| hidden 注入越界被拒绝 | ✅ `check_value` 在类型检查后查范围：`hidden r=0`、`r=-5` 抛 `ValueError`（HTTP 422 / MCP ToolError），覆盖值不写入；`tests/test_faults.py` |
| drift 在运行中越界不让求解抛异常 | ✅ `_recompute` 把值夹到范围边界（开区间端点取内侧 1e-9）；`drift_per_s=-1` 推过零后 `r` 在 (0, 1e-6]，读数照常，无 -300 |
| 求解期遇到非法参数，错误指出是哪个元件 | ✅ `solver.circuit()` 给 `stamp` 的 `ValueError` 加元件 uid（`rc: resistance must be positive`），不再是笼统的内部错误；直接写覆盖值绕过所有校验的情形有测试 |
| 其他带物理量参数的部件同样声明范围并有参数化测试 | ✅ 九个 part/accessory 类型全部声明（resistor、load_resistor、rc_lowpass、passive_probe、lead、coax_cable、unity_buffer、dc_supply、optical_attenuator）；`test_a_part_parameter_just_outside_its_physical_range_is_an_error` 对每个有范围的参数在界外一步报错、默认值在界内 |
