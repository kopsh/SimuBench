## 校验的绑定参数求值一处推导：sense 检查走通道组件读初值（Issue 1487）

| 项 | 结果 |
| --- | --- |
| 共享推导一处 | ✅ validate.py 私有 `_bound_path`/`_bound_value`：binding_of → 路径（通道相对 `ch.x` 按所在通道展开，与运行时 `resolve_path` 同语义）→ 台架初值（缺省落 spec default）→ `map_setting`；`_component_param`/`_input_impedance.value()`/`_impedance_hints` 三处手写全部退役 |
| sense 检查到通道 | ✅ `_check_sense` 逐通道走 `channel_components`：真实型号的远端检测全声明在通道组件上（b2912b `$ch.sense.remote`、n6705c/e36312a `{from: $ch.sense, map: …}`），原先只走 `components`、通道分支根本到不了 |
| 新用例 | ✅ `validation/instruments/sb-smu-4`（sb-smu-2 通道链加 `remote_sense: $ch.sense.remote`）+ `tests/test_four_wire.py` 通道绑定 sense 用例：W-SENSE-UNCONNECTED 指向 `/instruments/<名>/state/ch1.sense.remote`、逐通道各判各的初值 |
| 语料核对 | ✅ 68 个入库 bench 在 base（7da2dc86）与分支逐字节 diff：61 clean、7 个原有 W-IMPEDANCE-MISMATCH 原样，零翻红；`_impedance_hints` 不再对非通道端口猜通道 1 |
| 测试档位 | ✅ 慢机上三次全量三次换着踩 D49 预算线的扫描用例：与本分支撞车的 #1519（fixture 预热用例索引，main 侧方案）为准，本分支撤扫描档拆分、保留 `expected_folders` 进程内缓存（`problems` 每次重扫、断言不变）——wheel 检查的重扫成本收单次 |
| 文档 | ✅ decisions.md D86；progress-log 本节 |
| check.sh | ✅ 全绿（pytest 4859+ 量级、web 379、ty 零新增、schemas 零 diff；本机 x64 ~5.5 分钟，预算按 CI 机器） |
