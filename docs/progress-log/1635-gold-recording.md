## 金测录制统一入口与一种行形状（Issue 1635）

| 完成条件 | 自检 |
| --- | --- |
| 一条命令可重录任一金测文件，重录后现有比较全部通过 | ✅ `uv run python tests/record_gold.py [名字…]`（名字省略 = 全部八个：eload/dmm/smu/lockin/rfgen/specan/specan-models/scope-stream）；本机全量重录 6.3 s 后 40 个金测断言全过。逐字等价核对：eload/dmm/smu 重录输出与原内联表逐 step 相等（26/5/34/15/34 步）；lockin 重录与 HEAD **字节相同**；specan 仅 6 个 trace step 加 `"instrument"`、只写命令的 `"reply": null` 收敛为省略键；rfgen 仅 30 个数字在末位漂移（旧文件录于别的平台，比较本就是 rtol 1e-9）；scope/specan-models 行→step 逐行核对 digest/ns/reply 全等 |
| 七个 migration 测试文件不再有手工贴 repr 行的路径 | ✅ `require_gold` 空表改为失败并指向录制入口（原 repr 循环删除）；三个内联表（eload 31 行、dmm 49 行、smu 34 行数据）迁入 `tests/data/{eload,dmm,smu}_gold.json`；lockin/rfgen/specan 主金测从无录制路径到走 `record_gold_step`；scope/specan-models 的 `__main__` 并入入口 |
| 不新建平行比较机件 | ✅ 录制走 helpers 现有通道：`record_steps`（原 `record_rows`，输出改 step 形状）、新增 `record_gold_step` 作为 `run_gold_step` 的录制双生，两者共用 `_gold_lines`/`_gold_trace` 取值核心——录到的就是判到的；step 类别（命令/时钟推进/信号线/扫迹/屏幕）收进 helpers，`run_gold_step` 不再「the caller adds the step kinds specific to its model」 |
| 行形状收敛为一种，判定语义逐字等价，现有断言零删改 | ✅ 全部金测表为同一 step 字典形状：`{"instrument","cmd","ns"[,"reply"|"digest"]}` / `{"advance_s"[,"ns"]}` / `{"signal","lines"}` / `{"trace","instrument","values"}` / `{"screen",…}`；reply 槽只在命令有应答时出现（lockin/rfgen 的守卫断言 `s.get("reply", "")` 依赖此约定，故零改动）；断言全部原样保留，仅 specan-models 的序列预检从 `row[0]` 改为等价的 `_label(step)`（dict 无位置 0） |
| progress-log 一条一文件 | ✅ 本文件 |

备注：金测整体迁 run record 格式（与 D36 回放合流）本轮不做，issue 明确留待单独裁决；本 issue 不写 decisions 条目（测试基建，不满足两标准）。
