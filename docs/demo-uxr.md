# UXR1104B 演示：自动设置、测量、FFT 与波形读取

这套演示用一份 PyVISA 脚本操作 Keysight Infiniium UXR1104B（110 GHz、256 GSa/s）：只用编程手册
（11.80.00012）的命令，不 import SimuBench；同一份脚本接真机、接 `simubench serve` 的地址或用进程内
后端，都不改一行（D5）。

- 测试台：`examples/demo/uxr.bench.yaml`。两台 Keysight 33622A 的四路输出接到 UXR 的 CH1–CH4：
  10 MHz 正弦与 1 MHz 方波（400 mVpp），500 kHz 三角波（300 mVpp），2 MHz 脉冲（0 V 到 200 mV、
  占空比 20 %）。UXR 是 50 Ω 输入，发生器的负载设定都是 50 Ω。
- 脚本：`examples/demo/uxr_demo.py`。
- 自动测试：`acceptance/test_uxr_demo.py`。

## 1. 运行脚本

进程内后端（每次运行打开一个新的虚拟测试台）：

```bash
uv run python examples/demo/uxr_demo.py scope --bench examples/demo/uxr.bench.yaml --csv /tmp/ch4.csv
```

接真机时把仪器名换成 VISA 地址、去掉 `--bench`：

```bash
python examples/demo/uxr_demo.py TCPIP0::192.168.1.61::hislip0::INSTR
```

预期输出（种子固定，逐位复现）：

```
1. Keysight Technologies,UXR1104B,MY64310101,11.80.00012
2. :AUToscale -> 50 ns/div, trigger CHAN4
   CH1: 100 mV/div, offset +0.0 mV
   CH2: 100 mV/div, offset +0.4 mV
   CH3: 100 mV/div, offset +0.4 mV
   CH4: 50 mV/div, offset +99.6 mV
3. 1 µs/div, 20000 points, :DIGitize
4.      freq        amplitude  AC rms     rise       duty
   CH1  10 MHz     386.7 mV   142.5 mV   28.41 ns   51.2 %
   CH2  1 MHz      401.6 mV   199.6 mV   3.769 ns   50.0 %
   CH3  501.4 kHz  336.7 mV   86.69 mV   974.4 ns   49.9 %
   CH4  2 MHz      200 mV     78.08 mV   9.622 ns   20.0 %
5. FFT of CH2 (peaks above -30 dBm)
   976.6 kHz  -2.1 dBm
   3.052 MHz  -12.4 dBm  -10.3 dB
   5.005 MHz  -15.9 dBm  -13.8 dB
6. CH4: 20000 points, -11.7 mV to 210.5 mV
   written to /tmp/ch4.csv
7. :SYSTem:ERRor? 0, no errors
```

步骤：

1. `*IDN?`、`*RST`、`*CLS`、`:SYSTem:HEADer OFF`。
2. `:AUToscale`：示波器找出有信号的通道，按峰峰值设档位（最多占 6 格，取 1-2-5 序列的下一档），
   偏置对准波形中点；时基显示最低编号通道（CH1，10 MHz）的 3 个周期；边沿触发从 CH4 往下找第一个
   能触发的通道。
3. 时基改为 1 µs/div（屏幕 10 µs，最慢的三角波 5 个周期）、20 000 点、`:DIGitize`。自动设置留下的
   50 ns/div 只装得下方波的半个周期：在那个时基上，CH2–CH4 的频率与占空比会是无效值 9.99999E+37。
4. 每个通道：频率、幅度（顶–底）、整周期的交流 RMS（`:MEASure:VRMS? CYCLe,AC,…`）、10–90 % 上升时间、
   占空比。
5. `:FUNCtion1:FFTMagnitude CHANnel2`：方波的奇次谐波。`:MEASure:FFT:FREQuency? FUNCtion1,<n>,-30`
   按手册把 -30 dBm 以上的峰从低频到高频编号；不给门限时噪声峰也会被编进去。
6. `:WAVeform:SOURce CHANnel4`、WORD、LSBFirst，按 `:WAVeform:PREamble?` 换算成时间与电压。
7. `:SYSTem:ERRor?` 读到 0。

## 2. 在界面上看脚本运行

```bash
uv run simubench serve examples/demo/uxr.bench.yaml
```

服务启动时打印各仪表的 SOCKET / HiSLIP 地址和一行 `PYVISA_LIBRARY=127.0.0.1:<port>@simubench`。
设了这个变量，脚本里的真实地址不用改，命令就进入正在界面上显示的测试台：

```bash
PYVISA_LIBRARY=127.0.0.1:<port>@simubench \
  uv run python examples/demo/uxr_demo.py TCPIP0::192.168.1.61::hislip0::INSTR
```

- **画布**：四根连线上的波形缩略图与 RMS；示波器节点显示时基、触发源和每个通道的档位与偏置；
- **示波器屏幕**：自动设置后的四个通道叠在一起（OVERlay），下方测量表与脚本输出一致；
- **通信**：每条 SCPI 与应答，包括 40 008 字节的 `:WAVeform:DATA?` 块与最后的 `:SYSTem:ERRor?` → 0。

## 3. 讲解要点

- 只换连接方式不换代码：`--bench`、`PYVISA_LIBRARY` 和真机地址跑的是同一份脚本；
- 测量值来自信号：方波的交流 RMS 是幅度的一半，三角波是 A/(2√3)，脉冲是 A·√(d(1−d))；FFT 的
  3、5 次谐波比基波低约 9.5 dB 与 14 dB；
- 噪声按数据手册：100 mV/div 时 6.1 mV rms（UXR1104B 列），所以峰峰值比幅度大几十毫伏，三角波的
  幅度比 300 mV 大；
- 编程手册的行为照做：缺参数的 `:MEASure:VRMS? CHANnel1` 报 -224 且不应答；测不了的量返回
  9.99999E+37；FFT 峰按门限编号。

## 4. 已知限制

- 自动设置的时基跟随最低编号的通道（SimuBench 的简化，见 `scope_autoscale` 组件说明）；真机的选择
  规则手册没有写全。
- `:AUToscale:PLACement STACk` 把每个通道压进屏幕的四分之一，档位变成 500 mV/div，噪声随之变大，
  测量明显变差；演示用默认的 OVERlay。
- FFT 取记录里 2 的幂个点（20 000 点取 16 384 点，2 GSa/s 下频点间隔 122 kHz），所以 1 MHz 的峰
  显示为 976.6 kHz。
- 发生器的方波由有限条谱线合成，边沿前后有约 9 % 的吉布斯振铃（真机 33622A 的过冲小于 2 %）：
  方波的峰峰值读数因此偏大。

## 5. 自动测试

`acceptance/test_uxr_demo.py` 运行一次脚本（约 2 s），检查：无错误；自动设置找到四路信号、档位与
触发源；频率、交流 RMS、占空比、上升时间与发生器设定一致；FFT 的三个峰在 1、3、5 MHz，相对电平
符合 1/3、1/5；读回的脉冲高低电平与占空比。
