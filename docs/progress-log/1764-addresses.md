## HiSLIP/VXI-11 资源地址一处拼写：pyvisa_backend 手编退役，HISLIP_PORT 孪生常量退休（Issue 1764）

| 完成条件 | 自检 |
| --- | --- |
| `hislip_server.hislip_resource(host, sub, port)`（带端口的服务形式）+ `hislip_resource_default(host, sub)`（无端口的 VISA 默认形式）；`vxi11_server.vxi11_resource(where, sub)`（where 承载 host 与"端口映射器在 111 才省端口"的决定）——两个协议模块各一处构建点 | ✅ 服务器 `addresses()` 改用原语，输出逐字节不变 |
| pyvisa_backend 的手编地址表改 import：`HISLIP_PORT = 4880` 孪生常量退役（改用 hislip_server 的 `DEFAULT_PORT`），10 处 TCPIP0 手拼全部走原语 | ✅ `grep '4880' pyvisa_backend.py` 仅剩模块 docstring 的示例；`tests/test_pyvisa_buses.py` 的 `list_resources`/`canonical` 逐字节钉住，106 过 |
| scpi 的既有先例（`scpi_socket_address` 单源、后端 import）不回退；models/addresses.py 的规范化（比较用）不混入构建职责 | ✅ 两模块各归其位 |
