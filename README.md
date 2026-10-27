# SimuBench

面向研发、测试与校准场景的虚拟仪器仿真平台：在软件中构建、运行、复现与共享测试台（Bench）。

- 产品与决策：[docs/decisions.md](docs/decisions.md)
- 架构：[docs/architecture.md](docs/architecture.md)
- 开发计划与进度：[docs/roadmap.md](docs/roadmap.md)、[docs/progress.md](docs/progress.md)

## 开发

```bash
uv sync                 # 安装 Python 依赖
uv run pytest           # 运行测试
uv run simubench serve examples/benches/awg_rc_scope.bench.yaml   # 启动本地服务
cd web && pnpm install && pnpm dev                                 # 启动界面开发服务器
```
