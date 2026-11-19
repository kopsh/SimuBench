## *SAV/*RCL 接入后的覆盖表遗留（Issue 1603）

| 完成条件 | 自检 |
| --- | --- |
| 覆盖表不再引用已存在的能力作为阻塞原因 | ✅ N6705C `OUTPut:PON:STATe`：去掉 `blocked_by: setup-memory`，改 `gap: model_work`，说明缺的是上电恢复语义；53220A/53230A 的 `MMEMory:LOAD:STATe` / `STORe:STATe`：阻塞原因改为 `file-system`（状态文件要经仪器文件系统存取）；LeCroy 的 `RECALL_PANEL` / `STORE_PANEL` / `PANEL_SETUP`：与同文件 `*SAV`/`*RCL` 一致，写「Setup 0 是出厂默认面板、Setup 1–x 依已存面板数，不是固定槽范围，不猜槽数」，`gap: model_work`。其余型号里仍写 `setup-memory` 的行（命名状态、上电恢复方式等）issue 没有点名，未逐行核实，没有改 |
| 无手册依据的空槽行为不写成既定事实 | ✅ 34970A：空槽错误码 291 注明取自 DAQ970A 手册（DAQ97-90002），34970A 手册不在库中，错误码与行为未核实；E36312A：注明手册不在库中，空槽错误码与行为未核实；其余写了「空位置不报错」的型号（B2901A/B2912B、N6705C、BK8614、N9042B）手册只说不能调出、未给空槽行为，统一改成「空槽行为手册未给出、未核实（模型暂为不报错、设置不变）」。只改说明，不改行为 |
