## InternalsView 分家：编排面板与图各一个入口，#1174 形状前置（Issue 1732）

| 完成条件 | 自检 |
| --- | --- |
| 面板与图各一个模块、各一个公共入口；`InstrumentAcceptance` 改从面板模块导入 | `grep -n "InternalsPanel" web/src/features/instrument/InstrumentAcceptance.tsx`（`@/features/workbench/internals/InternalsPanel`，`:345` 用法不变）；`grep -rn "from \"./internals/InternalsView\"" web/src` 只剩 WorkbenchPage 的 `InternalsView` |
| layout/probe/trace 测试、stories、e2e 原样存活；无行为变化 | 三个测试文件与 `Internals.stories.tsx`、e2e 零改动（stories 只改 import 行指向 `./InternalsPanel`；e2e 按 testid 寻址）；`cd web && node_modules/.bin/vitest run` 39 文件 406 passed；tsc、eslint 全绿 |
| 两文件均 < ~1000 行 | `wc -l`：`InternalsPanel.tsx` 986、`InternalsView.tsx` 92（支撑文件 `Graph.tsx` 737，均低于预算） |
| #1174 的说明跟着代码走、语义不丢 | `grep -n "1174" web/src/features/workbench/internals/InternalsPanel.tsx`——全屏状态原子旁原样两行（`fixed inset-0 z-50`、Esc、将来编辑器共用一树两形），overlay 全屏行为本身不在本条实现 |
| `scripts/check.sh` 全绿（看退出码） | 退出码 0：ruff/format/ty（463 条诊断无新增）、pytest 4360 passed、tui-i18n/test-rules/layers/skills/progress-log/schemas、web check:api/typecheck/lint/vitest 全过。首跑曾红 `test_select_tests.py::test_shared_code_runs_everything`（0.131s CPU > 0.1s，D49 预算线），重跑全绿——本机并行负载下的已知存量边缘（#1716），与本条（纯 web 文件搬移）无关 |
| 纯重构不写 D；skill 不留陈旧路径 | `grep -n "1732" docs/decisions.md` 无新条目；`.claude/skills/web-ui/SKILL.md` 的 internals 条目改列新模块（`scripts/check_skills.py` 过） |

写前 fetch origin/main（ebbc581b，#1739）：新增提交动的是 server/assistant 与 screens/ 绘图骨架、
progress-log 各自新文件，与本条零交集。

### 分家与裁决

原 `InternalsView.tsx`（1791 行）背着两个公共入口服务两个消费者：`:91` `InternalsView`（工作台，
有 inspector、可 locate）与 `:180` `InternalsPanel`（验收页 `InstrumentAcceptance.tsx:25` 跨 feature
导入，无 inspector、探针由 renderProbe 注入）。本条按消费者分家：

- `InternalsPanel.tsx`（986 行，公共入口 `InternalsPanel`，另导出其 prop 契约 `ProbeSlot` 与
  stories 用的 `ComponentDetails`）：编排器一族——工具条（搜索/通道/图例/总览/全屏）、约 10 个
  状态原子（selected/expanded/probe/tune/query/pinned/hover/fullscreen/overview/overflows）、
  ComponentDetails 一族（ControlSection/Readings/RefList/FocusRow…）。
- `InternalsView.tsx`（92 行，公共入口 `InternalsView`）：工作台的活入口——internals 查询、
  加载/错误态、探针接线（`useInternalProbes` 缩略图、`useSettled` 防抖）。WorkbenchPage 的导入
  路径不变。
- `Graph.tsx`（737 行）：ReactFlow 画布——`Graph`、`FlowEdge`/`RefEdge`、四个节点组件、
  可读开局缩放常量，只被 `InternalsPanel.tsx` 导入。

**偏离裁决（issue 草图说「两个模块」，实际落了三个文件）**：组合链
`InternalsView ⊃ LiveInternals ⊃ InternalsPanel ⊃ Graph` 是全序——issue 的字面分组把链的两端
（InternalsView、Graph）放一个文件、中间（InternalsPanel）放另一个，两模块必然互相 import 成环；
「备形状」不该把环留给 #1174 的编辑器。故按依赖方向落三层（view → panel → graph），`Graph.tsx`
是同目录既有支撑模块模式的延续（`ProbeView.tsx`、`probe.ts`、`layout.ts`、`trace.ts`）。
两个面向消费者的模块各一个公共入口、两文件均低于 1000 行的验收不变。commit 顺序相应为
先搬图（叶）后搬面板，避免中间 commit 出现临时环。
`blockLabel`（两族都用）归 `lib/component.ts`——它包着的 `typeName`/`internalType` 本就在那里，
两模块都向下导入，不复制、不成环。
