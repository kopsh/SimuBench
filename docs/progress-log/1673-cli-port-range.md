## CLI 端口参数统一校验 0–65535（Issue 1673）

| 完成条件 | 自检 |
| --- | --- |
| `--port`、`--scpi-base-port`、`--hislip-port`、`--vxi11-port`、`--portmapper-port`、`--link-port`（含 `serve`/`launch`/`mcp`/`tui`/`doctor`）越界在启动前退出，退出码 2 | ✅ 新增 `service.port_number` 作为同一个 argparse `type=`（`src/simubench/service.py`，`add_service_options` 与 `cli.py` 的 `--port` 都用它）；`serve --port 99999` 输出 `simubench serve: error: argument --port: 99999 不在 0–65535 内 / not in 0–65535`，退出码 2，不再回绕到 34463 |
| `--port 0` 拒绝 | ✅ `service.web_port_number` 只用于界面端口（`serve`/`launch`/`mcp`/`doctor`）：横幅打印的就是这个端口，`0` 无法显示，给 `1–65535`；SCPI/HiSLIP/VXI-11/link 的 `0` 仍是系统分配（D41、D63），地址表打印实际端口 |
| 端口参数解析有单元测试 | ✅ `tests/test_ops.py::test_a_port_out_of_range_stops_before_the_entry_starts`（11 个入口×选项参数化）、`test_the_web_bench_port_refuses_zero`、`test_port_zero_still_lets_the_os_choose_the_scpi_family`；修复前在旧代码上失败（无 `SystemExit`，直接进服务） |
| 检查通过 | ✅ `scripts/check.sh`；全量 pytest 5398 passed（本沙箱 2 核配额下 221 s，超过 75 s 预算是已有 #1668，待 #1680 定测试分层，不在本 PR 内处理） |
