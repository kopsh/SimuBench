# 厂商手册

真实型号（D42）所依据的厂商手册，按文档编号分目录：`<编号>/<编号>.pdf`（Git LFS）与
`<编号>/<编号>.txt`（`pdftotext -layout` 的文本版，普通 git，可直接 grep；页码以 PDF 为准）。
克隆后取 PDF：`git lfs pull --include "manuals/**"`。手册版权归各厂商所有，此处仅供开发时查阅；
不随 wheel 发布。

新手册入库：放进 `<编号>/`，同一命令生成文本版，在下表加一行（定义的 `reference` 写同一编号与版本）。

| 文档编号 | 标题 | 版本 | 定义 | 来源 |
| --- | --- | --- | --- | --- |
| 53220-90001 | Keysight 53220A/53230A 350 MHz Universal Frequency Counter/Timer User's Guide | Edition 3, September 1, 2017 | `keysight-53220a`、`keysight-53230a` | https://www.keysight.com.cn/cn/zh/assets/9018-03351/user-manuals/9018-03351.pdf |
| B2910-90130 | Keysight B2900B/BL Series Precision Source/Measure Unit SCPI Command Reference | Edition 2, May 2023 | `keysight-b2912b` | https://transcat.ca/media/pdf/keysight-B2900B-B-programming-guide.pdf |
| 9-01715-903 | SR865A 4 MHz DSP Lock-in Amplifier Operation Manual | Revision 2.11 | `srs-sr865a` | https://www.thinksrs.com/downloads/pdfs/manuals/SR865Am.pdf |
| DAQ97-90000 | Keysight DAQ970A/DAQ973A Data Acquisition System User's Guide | Edition 3, June 3, 2020 | `keysight-daq970a`（#332，SCPI 命令在另一份 Programming Guide） | https://www.batronix.com/files/Keysight/DAQ/DAQ970A-973A-Manual.pdf |
