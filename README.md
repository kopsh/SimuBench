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

## 命令行

```bash
simubench validate examples/benches/*.bench.yaml   # 校验测试台，输出 文件:位置: 代码 说明（建议）
simubench schemas --out schemas                    # 导出 OpenAPI 与 JSON Schema
```

## 让 AI Agent 使用（MCP）

`simubench mcp` 在 stdio 上提供 MCP 服务，并在同一进程中启动 Web Bench（地址输出到 stderr），
Agent 搭建与部署的测试台会实时出现在界面中。需要可选依赖：`uv sync --extra mcp`。

```bash
# Claude Code
claude mcp add simubench -- uv run --directory /path/to/SimuBench simubench mcp --workspace /path/to/benches
```

工具：查询仪表定义与组件、创建/编辑/校验 Bench、由 PyVISA 脚本生成 Bench 草稿、部署（启动会话并返回资源地址）、
发送 SCPI（危险命令需 `confirm=true`）、读取仪表状态与通信记录。推进时钟与读取真实值属于仿真控制，
需以 `--allow-control` 启动，读取真实值还需 Bench 中 `run.reveal_true_values: true`。
