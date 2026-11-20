## state 事件的缓存合并收成 api 层被测纯函数 withStateEvent（Issue 1730）

| 完成条件 | 自检 |
| --- | --- |
| 「一条 state 事件怎样落进 SessionInfo」单点：`withStateEvent(held, ev)`（`web/src/api/sessionValues.ts`，与 `withValues` 并排、共用 `same()`），未命中事件缺 instrument/path、仪表不在缓存、path 不在该仪表——一律原引用返回；命中且值未变也原引用返回（同 `withValues` 的引用保持语义），命中且值变了只新建该变量对象，兄弟变量、兄弟仪表与其余字段引用原样 | `web/src/api/sessionValues.test.ts`（`a state event laid onto the held session`，#1730）：命中只换 value 字段 + 兄弟/他仪表/会话引用保持、仪表与 path 未命中原引用（含事件缺 instrument/path）、值未变原引用 |
| WorkbenchPage 内联补丁消失，只挂回调：`onState` 一行委托 `withStateEvent`，页面不再持有合并逻辑 | `grep -n "i.state.map" web/src/features/workbench/WorkbenchPage.tsx` 为空；e2e 由 CI 跑（本机不跑） |
| 纯重构不写新 D：合并逻辑从页面搬到 api 层无行为变化，未过「真实备选 + 跨 feature 影响」两标准 | 决策条目数不变 |
| check.sh 绿（除已知存量） | web 链路全绿：vitest 40 文件 416 例（基线 413 + 本条 3）、`tsc -b --noEmit`、`eslint .`、`gen:api --check`；`scripts/check.sh` 仅 `test_select_tests.py::test_shared_code_runs_everything` 因本机并行负载超 0.1s CPU 预算（存量 #1716，CI 预算 ×3 单跑即绿），其余 4370 例与后续各步全过，本 PR 不修 |
