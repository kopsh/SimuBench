## scope 家族缝封口：71 处 owner 穿透收进家族协议，块可替身单测（Issue 1724）

| 完成条件 | 自检 |
| --- | --- |
| `grep -rn 'host\.owner\.' src/simubench/library/components/scope_*.py` 无结果；acquisition 家族只剩身份一处（recorder 的采集事件主体），无属性链跟在后面 | `grep -rn 'host\.owner' src/simubench/library/components/scope_*.py`（只余 scope_recorder.py 两行） |
| scope_run 不再有 `host._` 跨模块下划线调用；正名成员 mode_is/timing/get_state/channels/averages/display_signal/format_number 落地 | `grep -rn 'host\._' src/simubench/library/components/scope_*.py` 无结果 |
| 守卫测试钉住块只触碰家族协议成员，`host.owner.<成员>` 一律判穿透，旧代码形态下红 | `tests/test_scope_family_seam.py`（把 0e88367e 的 scope_run.py/scope_cursors.py 放回树上，两条守卫红、27+ 条穿透被点名；放回后恢复绿） |
| ≥3 个块有不开真 bench 的替身单测 | `uv run pytest tests/test_scope_blocks.py` 13 passed（run control 与真 Recorder 块同跑、cursors、values；`tests/helpers.py` 的 FakeScopeOwner/FakeScopeHost） |
| 全量金测与现有测试原样存活 | `scripts/check.sh`：5420 passed（快层 392 s）；唯一失败 `test_select_tests::test_shared_code_runs_everything` 为 D49 预算本机固有边缘超限，基线 0e88367e 同样失败（#1716），CI 预算 ×3 不受影响 |
| D93 落 decisions.md；本条目按一条一文件 | `sed -n '/^## D93/,/^## D94/p' docs/decisions.md`（写前 fetch origin/main 确认 D93/D94 未被占用） |

#1540 把 scope 家族切成 13 个块，但四个家族 host 协议（scope_acquisition_host.py、
scope_measurements_host.py、scope_math_host.py、scope_trigger_host.py）里的
`owner: Instrument | None` 是 748 行的具体仪表，块里 71 处 `host.owner.*` 直接穿透协议，
scope_run 另有 23 处 `host._` 跨模块摸下划线。本条把 D62/D78 已验证的缝模式补进家族切分自身：
家族协议补上块真正消费的能力，owner 退出（ acquisition 家族只剩身份一处），块逻辑可替身单测。

### 普查（改前，`grep -o 'host\.owner\.[a-z_]*'` 计 71 处）

| 能力 | 成员 | 处数 | 块 |
| --- | --- | --- | --- |
| 状态写 | `set_state` | 18 | run 1、segments 2、cursors 6、edges 3、kmarkers 2、math_controls 3、math_fft 1 |
| 数值格式化 | `format_number` | 17 | cursors 7、kmarkers 6、readouts 2、results 1、recorder 1 |
| 状态读 | `get_state` | 9 | trigger_sources 3、edges 1、kmarkers 1、values 1、segments 1、math_controls 1、math_fft 1 |
| 状态事件 | `status_event` | 5 | recorder 3、run 2 |
| 待定操作与时钟 | `wait_until` 3、`now_ns` 2、`opc_query`/`wait_pending`/`arm_opc` 各 1 | 8 | run |
| 接线 | `wiring.source` | 3 | capture 1、trigger_sources 2 |
| 声明面 | `specs` 2、`definition.state` 3、`state.get` 1 | 6 | cursors、values、edges（state）、math_controls、math_fft |
| 组件注册表 | `components` 2、`channel_count` 1 | 3 | trigger_sources |
| 警告 | `warn` | 1 | capture |
| 命令格式化（两跳） | `runner.format_setting` | 1 | run |
| 面板身份 | `on_panel` | 1 | run |

scope_run 的 23 处 `host._`（acquisition 协议已声明但仍以下划线名穿透）：
`_is` 21 次调用（run/recorder/capture/segments 共用）、`_st` 2 次、`_timing` 8 次、
`_channels` 1 次；capture 另摸 `_averages`、`_display_signal`，math 两块摸 `_number` 6 次。
`host.owner` 不带后续属性链的用法只剩一处是真用途：recorder 把仪表作为主体发采集事件（身份）。

### 收口

- 四个家族协议各补上表中自己块消费的成员（成员表见各 `*_host.py`）；
  MeasurementsHost 补 `specs`/`state`（edges 对未声明路径读默认值，`get_state` 的
  KeyError 语义替代不了），MathHost 补 `has_state`（definition.state 的声明性成员查询）；
  两跳的 `owner.runner.format_setting` 收进 `AcquisitionHost.format_setting`。
- 正名：`_is`→`mode_is`、`_timing`→`timing`、`_st`→`get_state`、`_channels`→`channels`、
  `_averages`→`averages`、`_display_signal`→`display_signal`、`_number`→`format_number`；
  scope_run 不再有 `host._`。
- `owner` 从 MeasurementsHost/MathHost/TriggerHost 退出；AcquisitionHost 保留身份一处
  （recorder 的采集事件以仪表为主体）。
- trigger_sources 的 `select_taps` 摸的是仪表接线与通道数（`components`/`wiring`/
  `channel_count`）：把这段走线整体移进 `ScopeTrigger.select_taps`（它本就是公开入口，
  server 经它调用），TriggerHost 只补 `get_state`，不把注册表暴露给块。
- scope_pending 的 `Acquisition` 协议与调用跟随 `_is`→`mode_is`；其 `acq.owner.*`
  （block/now_ns/register_pending/cancel_pending）是另一条缝、不在本条 71 处之列，未动。
- specan 家族的 `host.owner.*`（specan_sweep、specan_markers）同病不同条，未动。
