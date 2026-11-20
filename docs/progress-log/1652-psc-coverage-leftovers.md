## E36312A、SMA100B、SR830、34970A 的 *PSC 覆盖表（Issue 1652）

| 完成条件 | 自检 |
| --- | --- |
| 有手册依据的型号加入 `builtins`、标 `accepted`、带手册用例 | ✅ SMA100B：手册 15.4 Common commands > `*PSC <Action>`（0\|1，未给默认值），`builtins` 加 `*PSC`，覆盖表 `accepted` 且 `affects_data: false`，备注写明默认取 1（D92）；`tests/models/rs-sma100b/cases.yaml` 新增 `*PSC` 用例（默认 1、写 0 读回、`*RST` 不改、写 1 读回） |
| 手册不在库或没有该命令的型号，备注写明原因，不留复制来的 Model work 备注 | ✅ E36312A、34970A：手册不在库中，`*PSC` 是否支持与默认值未核实，保持 `not_modeled`（`gap: model_work`，找到手册后加进 `builtins` 补用例）；34970A 原先没有 `*PSC` 行，补上；E36312A 定义里「手册有、但不回答」的注释改为「手册不在库中，未核实」。SMA100B 其余三行（`*IST`、`*OPT`、`*PRE`）仍是复制来的 Model work 备注，不在本 issue 范围，没动 |
| SR830 | ✅ 无需改动：issue 点名的第 209 行已经是 `accepted`（`affects_data: false`），`*PSC` 由定义自己的命令（`srs-sr830.yaml` 的 `header: "*PSC"`）回答，与 D92 的默认内置各自独立 |
