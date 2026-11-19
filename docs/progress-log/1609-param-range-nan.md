## 元件参数的物理范围不再放过 NaN（Issue 1609）

| 完成条件 | 自检 |
| --- | --- |
| `ParamRange.contains(float("nan"))` 为 False，并有单测 | ✅ `contains` 先判 `value != value`；`tests/test_validate.py::test_a_range_holds_no_nan_and_keeps_infinity_for_an_open_circuit`（`clamp(nan)` 取范围的低端，没有最小值取高端，都没有取 0；`inf` 在允许的范围里仍有效，电阻 `inf` 是开路） |
| issue 里的 bench `simubench validate` 退出码 1，诊断指向 `/parts/n/params/r` | ✅ `r: .nan` 报 `E-PARAM-RANGE`，指针 `/parts/n/params/r`；`tests/test_validate.py` 的错误表加一行 |
| 其他有范围的参数同样拒绝 NaN（参数化测试） | ✅ `test_a_part_parameter_just_outside_its_physical_range_is_an_error` 对每个有范围的 part/accessory 参数的界外值加上 NaN；变异检查：还原修复后 20 个失败，修复后全过 |
| hidden 注入与 drift 不产生 NaN | ✅ hidden 本来就由 `check_value` 的「有限数」检查拒绝 NaN；drift 的 `clamp` 现在对 NaN 也有定义（原先对没有最大值的范围会走到 `assert`） |
