# 定标演示：用 Fluke 5522A 校准 Keysight 34465A

这套演示说明 SimuBench 的核心用法（D4）：定标脚本在生产现场运行之前，先在虚拟测试台上跑一遍。
脚本只用 PyVISA 与两台真实型号仪表的标准命令；虚拟台知道隐藏的真值，所以跑完后可以逐点核对
脚本的判定是否正确。

- 测试台：`examples/demo/calibration.bench.yaml`（5522A 输出接 34465A 输入；万用表带按量程的
  隐藏增益与零点误差；测试台登记了两台仪表的校准台账）。
- 脚本：`examples/demo/calibrate_34465a.py`（不 import SimuBench）。
- 报告映射：`examples/demo/verdict-dc.yaml`、`verdict-ac.yaml`（`simubench verdicts` 用）。
- 故障版本：`examples/demo/calibration-fault.bench.yaml`（参考源在第 2 s 断开 30 s 虚拟时间）。
- 自动测试：`acceptance/test_calibration_demo.py`。

## 1. 准备

```bash
uv sync
uv run simubench assets examples/demo/calibration.bench.yaml --format json > /tmp/assets.json
```

台账里有参考源的校准有效期与万用表的到期日；脚本在测量前检查：参考源必须在用、未过期。

## 2. 运行脚本（直流）

脚本用 `@simubench` 进程内后端时，每次调用打开一个新的虚拟测试台（新的运行记录）：

```bash
SIMUBENCH_RECORD_DIR=/tmp/demo-runs \
  uv run python examples/demo/calibrate_34465a.py \
  --bench examples/demo/calibration.bench.yaml \
  --assets /tmp/assets.json --function DC --report /tmp/report-dc.csv
```

接真机（或 `simubench serve` 的 SOCKET / HiSLIP 地址）时去掉 `--bench`，用 `--cal` / `--dut`
传 VISA 地址；脚本本身不改：

```bash
python examples/demo/calibrate_34465a.py \
  --cal TCPIP0::192.168.1.31::INSTR --dut TCPIP0::192.168.1.41::INSTR \
  --assets /tmp/assets.json --function DC --report /tmp/report-dc.csv
```

预期输出（隐藏误差：1 V 量程 +250 ppm、零点 +300 µV 等，见测试台）：

```
34465A MY57440001: calibration due in 16 days (bench calendar)
phase       nominal         reading         error       limit  verdict
as-found          0          0.0003     3.000e-04   5.000e-06  FAIL
as-found          1         1.00055     5.500e-04   4.000e-05  FAIL
...
as-found        100        100.0023     2.300e-03   4.000e-03  PASS
as-left          10              10     0.000e+00   4.000e-04  PASS
as-left         100             100     0.000e+00   4.000e-03  PASS
as-found failures: 5; as-left failures: 0; report: /tmp/report-dc.csv
```

流程：读 `*IDN?` 校验型号与台账；as-found 在 0、±1、±10、100 V 各点测量并按 1 年指标
判定（保护带：w 取限值的 20 %，误差在 |误差| ≤ 限值 − w 时合格，超出限值 + w 时不合格，两者之间不作符合性声明；实际定标中 w 应由扩展不确定度 U 得出）；按手册解锁、逐量程
调零与调增益、`CAL:STOR`、重新上锁；as-left 重复测量。`CAL` 是 set 命令，不应答；脚本用
`:SYSTem:ERRor?` 检查校准是否被接受。

交流点单独跑一次（真值核对按功能各查一份报告）：

```bash
... --function AC --report /tmp/report-ac.csv
```

预期 as-found 与 as-left 都是 `N/A`：交流 1 V 点的隐藏误差 8.0e-4 落在保护带内（限值 9.0e-4，w = 1.8e-4：合格上限 7.2e-4，不合格下限 1.08e-3），演示“不作符合性声明”；`simubench verdicts` 把它核对为 `withheld`。

## 3. 用真值核对脚本的判定

```bash
record=$(ls -t /tmp/demo-runs/run-*.jsonl | head -1)
uv run simubench verdicts "$record" /tmp/report-dc.csv --map examples/demo/verdict-dc.yaml
uv run simubench verdicts /tmp/demo-runs-ac/run-*.jsonl /tmp/report-ac.csv --map examples/demo/verdict-ac.yaml
```

输出逐点给出脚本的判定与真值：`correct`（正确）、`false_accept`（误判合格）、`false_reject`
（误判不合格）、`withheld`（不作声明）。DC 报告 12/12 `correct`（其中 100 V 点是 as-found 合格、其余 as-found 超差，as-left 全部合格）；AC 报告两点都是 `withheld`。
“真值”是测试台求解出的实际输入（参考源 5 mΩ 输出阻抗、万用表 10 MΩ 输入的分压也计入）。

`simubench verdicts` 是从运行记录离线重放的：不改变会话的保密规则，也不需要脚本配合。

## 4. 故障演示

用故障版本运行同一个脚本：

```bash
uv run python examples/demo/calibrate_34465a.py \
  --bench examples/demo/calibration-fault.bench.yaml \
  --assets /tmp/assets.json --function DC --report /tmp/fault.csv
```

参考源在第 2 秒断开，脚本在随后的命令上得到 `VI_ERROR_CONN_LOST`，不会把连接问题写成
“量程超差”，也不会输出一份看似正常的报告。界面上的事件时间线能看到断开与恢复。

## 5. 在界面上看什么

```bash
uv run simubench serve --workspace examples/demo
```

打开 `calibration.bench.yaml`：画布上 5522A 的 OUT 接到 34465A 的 IN。运行脚本时：

- **画布**：连线电平随校准点变化；34465A 节点显示当前读数；
- **34465A 面板**：功能、量程、NPLC 与读数；调整时 `CAL` 状态位与错误队列；
- **事件**：每条 SCPI、校准锁定/解锁与错误；故障演示中参考源断开的那一段；
- **运行记录**：打开本次记录，时间线里逐条回放，能与脚本输出逐点对照。

## 6. 讲解要点

- 脚本只换连接方式，不换代码（D5）：真机、SOCKET、HiSLIP、进程内后端都跑同一份；
- as-found 的读数由隐藏误差决定，脚本不知道真值也能照常判定；跑完后平台能指出哪一条判定错；
- 保护带（ILAC-G8）是脚本自己的规则，平台只核对“脚本的声明与真值是否一致”；
- 交流与直流是两种功能，报告分开核对（`simubench verdicts` 的列映射一次一种功能）；
- 故障版本表明：虚拟台能在不损坏任何东西的前提下演练“参考源失效”；
- as-left 的误差恰好为 0：34465A 定义没有读数噪声，所以调整后完美到 0；真实的 as-left 会
  带读数噪声与参考源的不确定度（模型边界见覆盖表）。

## 7. 已知限制

- 真值核对按运行记录逐点匹配读数，因此 `--bench` 的进程内后端每次调用开新会话最适合演示；
  接 `serve` 时多次调用的读数会在同一份记录里，报告需要按记录顺序覆盖全部读数。
- 直流调整的参数（`AT3446XA` 安全码、逐量程调零/调增益）取自 34465A 服务指南的流程；模型
  的校准存储按（功能，实际量程）保存系数，其余校准步骤（如交流调整、输入阻抗校准）不在
  演示范围内，覆盖表里如实标注。
- 5522A 的 ≥33 V 设定会回到待机，脚本每次改设定后重新 `OPER`（手册第 3/4 章）。

## 8. 自动测试

`acceptance/test_calibration_demo.py` 运行整套流程：DC 的 as-found 6 点全部超差、调整后
as-left 全部合格，两份报告的 `simubench verdicts` 均为全部正确；交流点两次都在限值内；
参考源断开时脚本报连接错误；台账过期时脚本拒绝运行。
