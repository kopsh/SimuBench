## 帧环境调谐回归测试：ambient 内外同帧（Issue 1486）

| 完成条件 | 自检 |
| --- | --- |
| 实质的落地 | ✅ 已在 main：EvalContext 整体进键、adc 的 ambient 回退删除、decisions 条目（#1509/#1527，其条目引本 issue）——本分支不再重复 |
| 本条目的贡献 | ✅ 回归测试 `test_specan_sampled.py::test_a_frame_does_not_read_the_session_ambient_tuning`：真 specan 链的 adc 对同一无 tune 请求，环境内外直调 `frames` 数据逐位相同——把「块只读请求、不读调用方环境」的契约定成行为（main 的既有测试未锁这一条；热跑 1.1s） |
| check.sh | ✅ 全绿 |
