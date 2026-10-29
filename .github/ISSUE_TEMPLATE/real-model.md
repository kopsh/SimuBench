---
name: 真实型号
about: 新增或扩展一个真实型号的仪表定义（D42）
title: "真实型号：<厂商> <型号>（<范围>）"
---

## 目标

新增仪表定义 `<厂商>-<型号>`。行为以 *<手册名称>* 为准，在 `reference` 字段写明所用手册的版本。为真机写的脚本，凡是用到本 issue 范围内的功能，不改代码就能运行。

先读 `docs/real-models.md`（D42）与 `.claude/skills/real-model/SKILL.md`。

**本 issue 只写定义、覆盖表、用例与测试台，不新增或修改 `src/simubench/library/components/` 中的代码**（`tests/test_model_components.py` 检查）。平台缺能力时，在 #86 下开能力 issue，并在这里注明被它阻塞。

**依赖的能力 issue**：<列出，或“无”>

## 范围

### 实现（按手册，每条都要有用例）
- 

### 接受（解析并保存，不影响仿真）
- 

### 未实现（列入覆盖表，回复 `-113 … not modeled by SimuBench`）
- 

## 交付

- [ ] `src/simubench/library/instruments/<id>.yaml` 与 `<id>.coverage.yaml`
- [ ] `tests/models/<id>/cases.yaml`：覆盖全部“实现”和“接受”的命令
- [ ] 示例测试台 `examples/benches/<id>.bench.yaml`（只用真实型号），以及按真机写法的脚本，在测试中运行
- [ ] 界面用现有的类别模板或通用布局，不写型号专用的界面代码
- [ ] `docs/progress.md` 记录自检结果

## 验收标准

1. 全部手册用例通过，覆盖表一致性检查通过。
2. `src/simubench/library/components/` 没有改动；`tests/test_model_components.py` 通过。
3. `scripts/check.sh` 通过。

按 `AGENTS.md` 的流程：先在本 issue 留言领取，分支名 `issue-<编号>-<型号>`，PR 正文写 `Closes #<编号>`。
