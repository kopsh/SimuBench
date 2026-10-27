# 参考产品分析：EdgeSim 与 Liquid Instruments

本文整理 SimuBench 的两个主要参考对象，提炼可借鉴的设计，并说明 SimuBench 的差异化定位。

- EdgeSim：Galois Labs 的开源仪器测试台仿真器（Apache-2.0），参考其文章 [EdgeSim: An Open Source Bench Simulator](https://galoislabs.ai/blog/edgesim-open-source-bench-simulator)（2026-10-08，基于 EdgeSim 0.2.0）。
- Liquid Instruments（Moku 系列）：基于 FPGA 的软件定义仪器，参考其公开产品与白皮书资料。

---

## 1. EdgeSim

### 1.1 定位

模拟仪器接入模拟测试台，让 PyVISA 脚本、pytest、Agent 等上层程序无法区分其与真实硬件。它解决的核心问题是：测试代码不再等待“一台仪器、一块板子、一个人”的实体测试台。

EdgeSim 列出的价值点：

| 价值 | 说明 |
| --- | --- |
| 测试代码进 CI | 仪器相关代码可在托管 Runner 上运行，硬件任务只保留真正需要物理的部分 |
| 按计划注入故障 | 例如在指定虚拟时刻触发电源过压保护，安全态测试只需几行代码 |
| Agent 安全试错 | 虚拟仪器量程设错没有代价，可验证 Agent 的安全防护层 |
| 方案预演 | 测试计划在上电前先在虚拟台上跑一遍 |

### 1.2 关键设计

**仪器 Profile：一个文件，三种用途。** 每台仪器由一个 YAML Profile 描述，它同时是：

1. 驱动：带单位和限值的类型化命令，越界在发出前即被拦截；
2. 仿真规格：`sim:` 规则声明行为，例如过压后输出锁存关闭并压入错误码；
3. Agent 可导航的仪器地图。

真实仪器的 Profile 直接以虚拟方式运行，即“仿真孪生”（simulated twin）。

**保真度阶梯（Fidelity Ladder）。** 行为按可信程度分级，且每个返回结果都带有等级：

| 等级 | 含义 |
| --- | --- |
| `stub` | 类型化默认值，不代表行为，lint 会告警 |
| `property` | 命令即属性，读写自洽 |
| `declared` | 由 Profile 的 `sim:` 规则声明的行为 |
| behavior pack | 用 Python 行为包建模 Profile 无法表达的特性，通过 entry point 注册，引擎无需修改 |

**测试台拓扑与求解。** Bench 是节点与连线组成的拓扑文件。连线归并成网络（net），由准静态求解器求解每个网络，因此“电源接 1 kΩ 电阻，万用表读到 5 V”是电路求解结果，而不是预设回答。

**World / step / Transition。** 一个 World 拥有一个测试台：虚拟时钟、仪器、网络、带种子的噪声和 Trace。`step()` 是改变状态的唯一入口，返回 Transition：状态增量、保真度等级、状态码。

**Trace。** 每个动作产生一条类型化记录（状态增量、观测值、保真度），真实硬件写相同的 schema，来源标记为 `sim` / `real` / `replay`。

**多种接入方式，一份测试台文件。**

| 接入方式 | 用途 |
| --- | --- |
| 进程内 API | 测试、Notebook、脚本 |
| PyVISA 后端 | 现有 PyVISA 脚本零修改运行 |
| SCPI TCP Socket | 任意语言接入 |
| pytest 插件 | 每个测试一个带种子、步进时钟的测试台 |
| Agent / MCP | 以对待真实测试台的方式提供给 Agent |
| 终端控制台（TUI） | 每台仪器一张卡片，实时波形、键盘改值与注入故障 |

### 1.3 路线图

- M1（0.2.0）：9 类仪器（电源、万用表、函数发生器、示波器、频谱仪、射频信号源、锁相放大器、SMU、电子负载）、上述接入方式与控制台。
- M2：批量采集流式传输、真实会话录制回放用作回归、Parquet Trace、容器镜像与云端托管、Modbus TCP 与串口设备、批量创建带种子的测试台、高保真行为包。
- M3：CAN / I2C / SPI / OPC UA，电路瞬态耦合，在 `step()` 背后接入学习型世界模型。

### 1.4 明确的边界

- 单仪器少量命令的场景，mock 或 PyVISA-sim 已足够；EdgeSim 的价值在跨仪器的共享网络、耦合状态、计划故障和电路求解。
- 准静态求解只覆盖工作点，不覆盖瞬态，电路本身仍应使用 SPICE。
- 校准、探头、夹具接触、热行为和真实瞬态仍需要实体测试台。虚拟通过只证明测试代码、序列与限值正确，不证明被测板正确。

---

## 2. Liquid Instruments（Moku）

### 2.1 定位

Moku 系列用一台 FPGA 硬件承载十余种软件定义仪器（示波器、频谱分析仪、锁相放大器、PID 控制器、波形发生器等），可按需快速切换。

### 2.2 关键设计

- **仪器即软件模块**：同一平台通过重配置变成不同仪器，仪器能力由软件决定。
- **多仪器模式**：高端型号利用 FPGA 部分重配置，同时运行多台仪器（最多 4 台），仪器之间可内部级联。由于共处同一芯片，不存在时钟同步和非确定性通信延迟问题。
- **用户自定义 DSP**：用户可在浏览器中编写、编译并部署自己的信号处理算法，无需 FPGA 工具链。
- **混合前端**：多路 ADC 按频段融合信号，兼顾低频与高频噪声性能（属于硬件层面，SimuBench 不涉及）。

参考资料：

- [Liquid Instruments Moku transforms into multiple advanced scientific instruments with Xilinx's adaptive SoCs](https://liquidinstruments.com/white-papers/liquid-instruments-moku-transforms-into-multiple-advanced-scientific-instruments-with-xilinxs-adaptive-socs/)
- [MathWorks: Liquid Instruments integrates and dynamically reconfigures multiple instruments](https://uk.mathworks.com/company/user_stories/liquid-instruments-integrates-and-dynamically-reconfigures-multiple-instruments-with-zynq-ultrascale-mpsoc.html)
- [Liquid Instruments products](https://liquidinstruments.com/products/)

---

## 3. 对 SimuBench 的启示

| 来源 | 借鉴点 | 在 SimuBench 中的落地 |
| --- | --- | --- |
| EdgeSim | 声明式仪器 Profile（驱动 + 仿真规格 + 地图） | 仪器定义采用可读、可 diff 的声明文件，命令带类型、单位与限值 |
| EdgeSim | 保真度分级并随结果返回 | 每个测量结果标注保真度，让“能力边界清晰”可被程序读取 |
| EdgeSim | 拓扑 → 网络 → 求解器 | 画布连线即拓扑，由求解器得出各端口的电气量 |
| EdgeSim | 单一 `step()` 入口、虚拟时钟、种子噪声 | 运行可确定、可复现，适合 CI 与问题重现 |
| EdgeSim | 统一 Trace schema，区分 sim / real / replay | 运行记录可分析、可回放，可与真实设备记录对比 |
| EdgeSim | PyVISA / SCPI Socket / pytest / Agent 多入口 | 兼容现有自动化测试程序，零修改或少修改迁移 |
| EdgeSim | 可编程的故障注入 | 场景化调整仪表行为和故障条件 |
| Liquid | 仪器即软件、可重配置 | 仪表由软件模块定义，可扩展新仪表类型 |
| Liquid | 仪器内部级联、共享时钟 | 多仪表协同共享同一虚拟时基，信号在仪表间传递无同步问题 |
| Liquid | 用户自定义 DSP 模块 | 仪表由内部功能器件（源、滤波、放大、采样、检波、测量等）组合而成，用户可替换或新增器件 |

## 4. SimuBench 的差异化

1. **可视化画布**：EdgeSim 以文件和终端为主，SimuBench 以可视化画布构建测试台，连接仪表端口、配件与 DUT。
2. **仪表内部可组合**：EdgeSim 的仪器行为来自 Profile 规则和行为包；SimuBench 借鉴 Liquid 的模块化思路，仪表本身是功能器件图，信号在器件间生成、传输、采集与测量，测量结果由配置和输入共同决定。
3. **团队协作与资产管理**：测试台的保存、复制、共享、版本管理与协作是一等能力，而不仅是本地文件。
4. **纯软件**：不依赖 FPGA 或任何专用硬件，可在本地、云端与 CI 中运行。

## 5. 待决问题

- 首批支持的仪表类别与对应的命令集（SCPI 子集）。
- 求解器范围：仅准静态工作点，还是包含时域信号路径（示波器、频谱仪需要波形）以及两者如何协同。
- 仪器定义格式：直接兼容已有 Profile 生态，还是自定义格式并提供转换。
- 首批接入方式的优先级：PyVISA 后端、SCPI Socket、进程内 API、pytest 插件。
