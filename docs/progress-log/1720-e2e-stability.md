## e2e 抖动治理第三轮：删不稳定用例，#277 组稳定化（Issue 1720）

| 完成条件 | 自检 |
| --- | --- |
| assistant「绕圈停止+压缩留 note」用例删除，剩余两条完整（≥5 断言） | `scripts/check_test_rules.py` |
| #1258 只删「入视口」断言，「不重叠 + 布局落盘」断言原样 | `pnpm exec playwright test e2e/edit.spec.ts` |
| #277 组加 `settledCanvas` 后 5 参数全绿 | `pnpm exec playwright test e2e/layout.spec.ts` |
| D49 增补「先修根因，覆盖薄就删」原则（编辑既有条目，含不采用 e2e 自动重试的备选记录） | `sed -n '/不稳定的用例/p' docs/decisions.md` |
