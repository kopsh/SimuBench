# HANDOFF — SimuBench TUI（issue 173 总 issue）实施进度

日期：2026-11-01。接手者请先读 AGENTS.md、docs/tui-screens.md（唯一实现规格）、docs/decisions.md 的 D18/D49。

## 总体进度

| 子 issue | 内容 | 状态 |
| --- | --- | --- |
| #174 | 错误事件六分类（engine+schema+MCP+Web） | ✅ PR #182 合并 |
| #175 | T1 骨架（simubench[tui]、tui 命令、总览卡片、通信记录、断线重连、来源"终端"） | ✅ PR #202 合并（Windows 修复 #203/#205） |
| #176 | T2 仪表屏幕/示波器屏幕/原因对话框/找到跳闸 | ✅ PR #216 + 竞态修复 #217 合并 |
| #177 | T3 网络屏幕/故障注入/时钟推进 | ✅ PR #218 合并 |
| #178 | T4 可复现截图、演示录制、运行记录、文档 | 🔨 **进行中**，分支 `issue-178-tui-t4`，worktree `/Users/songhao/code/github/SimuBench-t177` |

main 七个 CI 任务全绿（含新 main-only 任务 "Slow tests (Textual boot, load-sensitive)"）。

## D49 测试规则（用户明确要求，已写入 decisions.md 并机械强制）

1. **单元层**：纯逻辑、mock/注入、不 import Textual/不起服务器；单个 `call` ≤100ms（conftest 对 `unit` 标记强制失败）。
2. **启动级**（`tui_boot` 标记）与 **e2e**（`tests/test_*e2e*.py`）：main 合并后 CI 跑；e2e 断言 ≥5（AST 脚本 `scripts/check_test_rules.py` 强制）；服务器夹具必须 module/session 作用域。
3. 快速门禁：`check.sh` 跑 `-m "not tui_boot and not load_sensitive"`（~60s）；CI PR 同；main 后跑慢层。

## #178 已完成（未提交/已提交在分支上，最新提交 5573bf8 + 之后的 i18n/CSS 修正）

- `screens/runs.py`：运行记录屏幕（列表/摘要/回放第一处不同文案），纯函数 `run_rows`/`first_mismatch_text` 可单元测。
- `app.py`：`R` 键分流——file_changed 时 R=重建，否则 R=打开运行记录；注册 "runs" 屏幕。
- `client.py`：`runs`/`run_detail`/`replay` 方法。
- `script.py`：步骤引擎（Steps.load/validate/split_key/run_steps/external_scpi/_write_frames）。步骤动作 = key/scpi/advance/caption/highlight/screenshot，`wait` 为修饰符；scpi 以 raw socket 发出（来源显示"脚本"）。
- `launch.py`：`--script/--out/--record` 无头路径（`_run_script`）。
- `widgets.py`：`CaptionBar`（演示字幕，dock 在底栏上方，`-show` 显隐）；`BottomBar.flash`。
- `instrument.py`：`ControlButton.-highlighted` 琥珀边框。
- i18n：runs 组 + keys.summary/replay（zh/en，check_tui_i18n 绿）。
- 单元测试 `tests/test_tui_t4_units.py`：8 项全绿（0.3s）。

## #178 当前卡点（接手先做）

`tests/test_tui_t4.py` 两项 tui_boot 失败：

1. `test_runs_screen_lists_and_replays`：**StylesheetParseError**，错误位置指向 `src/simubench/tui/widgets.py`（CaptionBar 的 DEFAULT_CSS）。最可疑：`#caption-right { float: right; ... }` —— **Textual CSS 不支持 `float`**。修法：去掉 float，改用 Horizontal 里两个 Static（左 caption 右 `key ▸ enter`），或直接把 right 拼进同一 Text（`set_caption` 已经拼了 `    ` + right，可以直接删掉 `#caption-right` 规则——它没有被任何 widget 用到）。用 `uv run pytest tests/test_tui_t4.py::test_runs_screen_lists_and_replays -vv` 看 "Error at ..." 完整行确认。
2. `test_scripted_screenshots_are_byte_identical`：修复 1 后重跑；如仍红，检查 `run_steps` 里的 `pilot.press`（app._press_keys 已弃用）与 `export_screenshot` 的确定性（两次运行逐字节比较断言在测试里）。

## #178 未开始

- `--record` 的帧输出验证（现在只有实现，没测试）。
- 演示文件 `docs/tui/find-the-trip.steps.yaml`（smu 台 + 预设跳闸故障或 POST /faults；步骤参考 issue 与 tui-design.md §6 的 YAML 示例），生成截图进 `docs/tui/`。
- README 与用户文档的 TUI 章节（引 `simubench[tui]`、两种启动方式、按键表引 tui-screens.md）。
- runs 屏幕快照（zh/en × 100×32/80×24，参照 test_tui_snapshots.py 里 nets 的写法；服务器夹具必须 module 作用域 + 固定端口，规则检查会抓函数级服务器夹具）。
- 全量 `scripts/check.sh` 绿 → push → PR（标题示例 "TUI T4：可复现截图、演示录制、运行记录与文档（#178）"）→ 合并 → main 慢层确认。
- 收尾：`docs/progress.md` 加 T4 验收表（仿已有条目）；issue 173/178 各留一条收尾评论。

## 测试书写规约（踩过的坑，务必遵守）

- **compositor 部分帧竞态**：flat() 读的是实时合成条带，可能读到"半更新"帧。所有屏幕断言用 wait_for/wait_flat（test_tui_t2.py 顶部有实现），把同一帧要出现的全部针放一次调用；绝不 pause(固定值)+assert。
- **焦点丢失**：overview 卡片网格会在数据刷新时重建并丢焦点。聚焦卡片一律用 `tests/test_tui_t2.py::focus_card`（带重试按数字键）。
- **Textual 陷阱**：`Widget._render` 是框架内部方法，不要覆写（BottomBar 踩过，改名 `_repaint`）；Textual CSS 无 `float`；`ctrl+a` 在 Input 里是行首不是全选；RadioSet 不收 (label, value) 元组，用 `RadioButton(label, name=k)`，取值用 `pressed_button.name`；SpectralLine 字段是 `freq_hz`/`amplitude`。
- **夹具数据**：`tests/data/tui/unit_*.json` 是从真实会话导出的载荷（FakeClient 读它）；断言里的格式化数值（如 "697.07 mV"）一律 `fmt(fixture值)` 现算，不要手写。
- 服务器/会话：单元测试零网络（`tests/tui_mock.py::FakeClient + unit_app`）；真服务器测试进 e2e 文件、module 作用域；快照服务器固定 SCPI/HiSLIP 端口（检查规则要求可复现）。
- 本机：uv 命令一律 `UV_DEFAULT_INDEX=https://pypi.org/simple` 前缀；pnpm 加 `--registry=https://registry.npmmirror.com --trust-lockfile`；`test_performance.py` 已是 `load_sensitive`（忙碌机器上会红，单独跑恒绿，不用管）。

## 验收标准（#178，来自 issue）

- CI 重新生成文档截图逐字节一致（演示步骤两次运行 SVG 相同的测试即其代理，已在 test_tui_t4.py）。
- 演示脚本同时产生运行记录，截图中的状态可在记录中复核（演示跑在带 `--record-dir` 的会话上，或在 steps 的 scpi 用外部客户端时记录可见——按规格 §6 末条）。
- `scripts/check.sh` 全绿。
