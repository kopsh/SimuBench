## UXR 的 isim 块标题说明它只是带宽限制（Issue 1275）

| 完成条件 | 自检 |
| --- | --- |
| UXR1104B、UXR0334B 的 `isim` 标题与描述说明它是 `:CHANnel:ISIM` 命令组下的带宽限制，InfiniiSim 去嵌入未建模 | ✅ 两个定义的 `label` 改为「带宽限制滤波（ISIM 命令组）」，`description` 注明去嵌入面板未建模；`docs/instrument-authoring.md` 的例子同步 |
| 仍与「垂直前端」可区分 | ✅ `tests/test_internals.py` 断言新标题且不同于 `ch1.front` 的标题 |
| `scripts/check.sh` 通过 | ✅ 见 PR |
