# 低频校准工作台

示波器校准仪（源）接参考示波器（表），供既有的低频校准主机程序不改源码直接运行。说明见 [docs/lowfreq-cal.md](../../docs/lowfreq-cal.md)。

```bash
simubench serve examples/lowfreq-cal/lowfreq_cal.bench.yaml     # 打印两台仪器的 SOCKET 与 HiSLIP 地址
python examples/lowfreq-cal/lowfreq_cal_demo.py --calibrator TCPIP0::127.0.0.1::5025::SOCKET \
    --scope TCPIP0::127.0.0.1::5026::SOCKET
```

部署前把 `lowfreq_cal.bench.yaml` 中两台仪器 `idn` 的型号段改成主机程序校验的型号串。

两份仪器定义按主机程序的接口契约编写，不是按型号手册实现的真实型号（D42）；文中的厂商名只用于指明命令方言，SimuBench 与该厂商没有关联。
