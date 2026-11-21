## agent 工具入参真单源：assistant 的 create_bench 由契约模型镜像，bench schema 字符串一处（Issue 1770）

| 完成条件 | 自检 |
| --- | --- |
| spike 结论：pydantic-ai 能吃镜像注入——它读 `get_type_hints(fn, include_extras=True)` + `inspect.signature`，装饰器设 `__signature__` 与 `__annotations__` 即得扁平 schema 且调用穿透（构造模型给函数体）；描述经 `Annotated[type, FieldInfo]` 保留（**`rebuild_annotation()` 只给裸类型，schema 会丢 description**——探针两次实证） | ✅ spike 探针 + 真模块 schema dump：path/name/description 三字段带描述、required 正确 |
| `contracts/tools.py` 立 `mirrored_params`（两缝共享的镜像参数表）；`mcp_tools.context.tool_input` 改用它（删内联构造）；assistant 立 `tool_input` 装饰器（ctx 参数 + 镜像，`functools.wraps` 后设签名与注解），`create_bench` 改走 `T.CreateBench`——手抄的三个字段与 description 退役 | ✅ `tests/test_contract_models.py::test_the_assistant_tool_schema_comes_from_the_contract_model`（工具 schema 的 properties（去 pydantic 自动 title）与 required == 模型自己的 json schema；改模型漏改 assistant 时测试红） |
| bench 文件 schema 字符串一处：`models/bench.py` 立 `BENCH_SCHEMA`（Literal 保持原文——ty 不收 Final 作 Literal 实参，#1650），两个 agent 工具体（assistant `create_bench`、MCP `create_bench`）改用 | ✅ grep 两缝无裸字面量；其余五处拼写（cases/manager/scriptscan/migrate/edit）不在本 issue 范围，留注释说明 |
| 既有测试存活 | ✅ `tests/test_assistant.py tests/test_mcp.py tests/test_contract_models.py` 98 过 |
