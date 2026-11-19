## *SAV / *RCL 接 setup_memory（Issue 1573）

| 完成条件 | 自检 |
| --- | --- |
| 34465A 0–4、53220A/53230A 0–4、34970A/DAQ970A 0–5、E36312A 0–9、N6705C 0–9、B2901A/B2912B 0–9、N9042B 0–127、8614 0–9 的 `*SAV`/`*RCL` 由 `setup_memory` 回答；空槽错误码按手册（34465A +810、53220/DAQ +291，其余手册未给码的不报错） | 各型号 `cases.yaml` 的「状态存储」用例；`uv run simubench cases tests/models/<id>/cases.yaml` 全部通过 |
| 覆盖表 `*SAV`/`*RCL` 改为 `implemented`，注明槽范围、空槽行为与 SimuBench JSON 格式不与厂商私有格式一致 | `uv run simubench coverage <id>`；`tests/test_shipped_cases.py`、`tests/test_cases.py` |
| 手册不在库中或语义不同的型号（DSO-X、LeCroy、SMA100B）保持 `not_modeled` 并写明原因，不猜槽数 | 三个型号的覆盖表 note；`tests/test_coverage_gap.py` |
