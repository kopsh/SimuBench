## 运行中一键切换实时时钟；频谱仪关连续扫描即定格，屏幕给出单次/连续/实时操作（Issue 1660）

| 完成条件 | 自检 |
| --- | --- |
| 运行中切换时钟：不跳、不回退，命令时钟从此刻起计空闲 | ✅ `Clock.set_mode` / `Session.set_clock_mode`；`tests/test_scaled_clock.py`：命令→墙钟→命令时间连续、倍率切换从此刻起算、同模式同倍率不算变化、非法模式与倍率被拒且不变 |
| 切换发 `clock` 事件，回放按它换时钟 | ✅ 事件登记在 `engine/events.py`（`scale` 字段）；`replay.resend` 遇 `clock` 调 `Clock.follow_record`（实时段按步进）；`test_a_record_with_a_live_stretch_replays_at_the_recorded_times` 每条写入都在录制时刻、模式依次为 command/stepped/command；`tests/test_events_contract.py` 登记新类型 |
| `sleep` 空闲档的拦截随模式切换 | ✅ `test_the_sleep_patch_follows_the_command_clock` |
| 脚本连着端口时切换需确认 | ✅ `confirm_mode`（ServedSession → `require_no_clients`）；`tests/test_remote_state.py` 的整台操作清单加 `clock/mode`；`tests/test_mcp.py` 的 force 流程加 `set_clock_mode` |
| HTTP 与 MCP 入口、`SessionInfo.start_clock_mode`、重置回到打开时的模式 | ✅ `POST /api/sessions/{sid}/clock/mode`、MCP `set_clock_mode`；`tests/test_server.py::test_the_running_clock_is_switched_live_and_back_over_the_api`；schemas 与 `web/src/api/generated` 已重新生成；AI 助手工具不含它（`tests/test_assistant.py`） |
| 频谱仪：关连续扫描后迹线与扫描计数停住；`:INIT:IMM` 前进一轮后再停；改设置后保持新设置的迹线；重新开启后继续扫 | ✅ `specan.py`（`watches` 加 `init.continuous`，`HoldState.stop_ns` / `frozen_key`，`shown_sweep`）；`tests/test_keysight_n9320b.py` 流程测试新增一段；频谱相关与型号用例 1208 项通过 |
| Web：顶栏“实时 / 暂停”与倍率；频谱屏幕提示栏“实时扫描”“单次扫描”“开启连续扫描”；事件栏 `clock` 行 | ✅ `TopBar.tsx`、`SpectrumScreen.tsx`、`BottomPanel.tsx`、`clockControl.ts`；`clockControl.test.ts`、`SpectrumScreen.render.test.tsx`；e2e `instruments.spec.ts`（单次扫描停住、单次前进、恢复连续、实时扫描计数增长、暂停回到命令时钟）与 `bench-open.spec.ts` 本地通过；`layout-visual.spec.ts` 在拒用 Inter/JetBrains 字体的 fontconfig 下先确认旧基线通过、改动后仍通过 |
| 文案：Web 中英文与 TUI 共享文案一致；TUI 日志写时钟切换 | ✅ `scripts/check_tui_i18n.py`；`tests/test_tui_units.py::test_a_clock_switch_is_a_log_line_naming_both_clocks`；TUI 收到 `clock` 事件即重读时钟与会话 |
| Hello World 默认动态连续扫频 | ✅ `hello.bench.yaml` 的 `run.clock: wall`（附注释说明如何暂停）；`tests/test_hello_bench.py` 通过；首页的实测预览与截图未重新生成（画面内容不变：同一信号与设置） |
| 文档 | ✅ D12 增加“运行中切换”；architecture.md 5.3、ui-design.md 时钟控制与运行状态 |
