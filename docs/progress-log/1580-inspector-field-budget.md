## 检查器：挂载的字段数不随定义增长（Issue 1580）

| 完成条件 | 自检 |
| --- | --- |
| 每张字段列表最多挂载一页 | ✅ `Paged` / `FieldList`（`Inspector.tsx`），`FIELD_PAGE` = 40，「再显示 N 项」；`model.test.ts`：`fieldsShown` 按整页显示，定位的设置所在页会翻开 |
| 分组内行、分组列表也翻页 | ✅ `GroupRows`：字段、编号族、测量槽合成一张列表；`SettingsBlock` 的分组列表经 `Paged` |
| 默认展开有行数预算 | ✅ `groupsOpenByDefault`；`model.test.ts`：50 个 10 项短组只开前 4 个，顶层已占满一页时一个都不开 |
| 内置型号回归 | ✅ `e2e/workbench.spec.ts`（UXR1104B）：展开「全部设置」后控件 ≤ 150 且出现「再显示」；`workbench.spec.ts`、`edit.spec.ts`、`panel.spec.ts` 63 条通过 |
| 用户自定义仪表实测 | ✅ 合成定义（Playwright，两种模式）：无 summary 的 2000 项选中 2003 → 42 个控件；展开「全部设置」2003 → 43；再开 2000 项分组 4003 → 83；500 个短组展开后 43；均无长任务 |
