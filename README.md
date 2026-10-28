# SimuBench

面向研发、测试与校准场景的虚拟仪器仿真平台：在软件中构建、运行、复现与共享测试台（Bench）。

- 产品与决策：[docs/decisions.md](docs/decisions.md)
- 架构：[docs/architecture.md](docs/architecture.md)
- 开发计划与进度：[docs/roadmap.md](docs/roadmap.md)、[docs/progress.md](docs/progress.md)

## 开发

```bash
uv sync --all-extras    # 安装 Python 依赖（含 MCP）
uv run pytest           # 运行测试
uv run simubench serve examples/benches/awg_rc_scope.bench.yaml   # 启动本地服务
cd web && pnpm install && pnpm dev                                 # 启动界面开发服务器
```

协作开发（任何 AI agent 或人）：任务是 GitHub Issue，规则见 [AGENTS.md](AGENTS.md)；PR 通过 CI 后自动合并进 `main`。

## 命令行

```bash
simubench validate examples/benches/*.bench.yaml   # 校验测试台，输出 文件:位置: 代码 说明（建议）
simubench schemas --out schemas                    # 导出 OpenAPI 与 JSON Schema
```

## 安装（不需要源码与 Node）

```bash
./scripts/build-wheel.sh                         # 构建带界面的 wheel：dist/simubench-<版本>-py3-none-any.whl
pip install dist/simubench-*.whl                 # 需要 AI Agent 时：pip install "dist/simubench-<版本>-py3-none-any.whl[mcp]"
simubench doctor                                 # 检查安装
simubench serve --workspace 我的测试台目录 --open
```

### 从脚本连接

会话中的每台仪表同时以两种方式可达（启动时输出地址表）：

```python
import pyvisa

# raw socket：每台仪表一个端口
scope = pyvisa.ResourceManager().open_resource("TCPIP0::127.0.0.1::5026::SOCKET")
# HiSLIP：共用一个端口（默认 4880），按子地址区分仪表（D41），消息边界、设备清除、
# 读状态字节与 SRQ 与真实 HiSLIP 仪表一致；NI-VISA、Keysight IO Libraries 无需修改即可连
scope = pyvisa.ResourceManager().open_resource("TCPIP0::127.0.0.1::hislip1,4880::INSTR")
```

安全设计与自检见 [docs/security.md](docs/security.md)。

## 在 pytest 中使用

安装后自动注册 pytest 插件。每个测试得到一台刚上电的测试台：

```python
import pytest

@pytest.mark.bench("benches/awg_rc_scope.bench.yaml", seed=7)
def test_gain(instruments, bench_session):
    scope = instruments["scope"]            # 也可用真实地址 "TCPIP0::192.168.1.21::5025::SOCKET"
    assert scope.query("*IDN?").startswith("SIMUBENCH")
    bench_session.advance(0.01)             # 真机做不到的事：推进虚拟时间、注入故障
```

`pytest --simubench-bench <bench> --simubench-record-dir runs` 为没有标记的测试指定测试台，并为每个测试写运行记录；失败的测试在报告中给出记录与 `simubench replay` 命令。

## 运行记录与回放

`simubench serve --record-dir runs` 为每个会话写一份运行记录（JSON Lines）。CI 中失败的一次运行可以在本地复现：

```bash
simubench replay runs/run-20261027T120000-abcd1234.jsonl   # 逐条比较回复，第一处不同给出命令与时刻
```

示波器的波形数据块在记录中保存为可重新生成的配方与哈希，需要原始字节时加 `--record-raw`（D36）。

## 运维与诊断

```bash
simubench doctor --workspace benches     # 健康检查，失败时退出码 1
simubench version --full                 # 版本清单：依赖、仪表定义及其哈希
simubench diagnose --out diag.zip --record-dir runs   # 诊断包（不含访问令牌）
simubench serve --log-format json --log-file simubench.log
```

界面中的“设置 → 关于与诊断”显示同样的内容，并可导出诊断包（D38）。

## 让 AI Agent 使用（MCP）

`simubench mcp` 在 stdio 上提供 MCP 服务，并在同一进程中启动 Web Bench（地址输出到 stderr），
Agent 搭建与部署的测试台会实时出现在界面中。需要可选依赖：`uv sync --extra mcp`。

```bash
# Claude Code
claude mcp add simubench -- uv run --directory /path/to/SimuBench simubench mcp --workspace /path/to/benches
```

工具：查询仪表定义与组件、创建/编辑/校验 Bench、查看 Bench 的版本历史、由 PyVISA 脚本生成 Bench 草稿、部署（启动会话并返回资源地址）、
发送 SCPI（危险命令需 `confirm=true`）、读取仪表状态与通信记录。推进时钟与读取真实值属于仿真控制，
需以 `--allow-control` 启动，读取真实值还需 Bench 中 `run.reveal_true_values: true`。
Agent 保存的每个版本都记录为 Agent 所写，工程师在界面的“版本历史”中逐项审阅并可恢复（D35）。
