## Web 画布适应全部节点、REST 元件参数遵守 D9、检查器参数带单位（Issue 1674、1675、1676）

| 完成条件 | 自检 |
| --- | --- |
| 1400×900、1280×800、1100×800、1920×1080 打开 `siglent_scope_rc` 初始视图含全部节点（#1674） | ✅ `web/e2e/layout.spec.ts`「siglent_scope_rc opens with every node in view」四个窗口；去掉修复后 1400、1280 两个失败 |
| `run.reveal_true_values` 关闭时 REST 元件 `params` 里 `hidden:`、故障、`set_parameter` 写入的值回退为名义值，打开后为真值（#1675） | ✅ `tests/test_deviation.py::test_the_session_view_hides_explicit_hidden_part_values_unless_revealed`（`r` 与 `tol_c` 两条路径） |
| 检查器与画布卡片共用 `formatPartParam`：单位取自目录 `units`，`tol_*` 按百分数，无单位参数不加 SI 前缀（#1676） | ✅ `web/src/features/workbench/model.test.ts`「formatPartParam」；`pnpm typecheck && pnpm lint && pnpm test` |
