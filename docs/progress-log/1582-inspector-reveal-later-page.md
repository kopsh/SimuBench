## 检查器：翻页后才挂载的分组也能被定位展开（Issue 1582）

| 完成条件 | 自检 |
| --- | --- |
| 定位请求打开第二页及以后的分组 | ✅ `Section` 以 `defaultOpen || reveal != null` 初始化（`Inspector.tsx`）：挂载时已带定位请求的分组直接展开，字段随之挂载 |
| 组件级回归 | ✅ `Inspector.test.tsx`：合成 50 个 10 项分组（无 summary），定位第 42 个分组内的字段、第 2 页编号族的第 24 个实例、测量槽行：控件可见、所在分组 `aria-expanded=true`、无 `revealMissing` 提示 |
