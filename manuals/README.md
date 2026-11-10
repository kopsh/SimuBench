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
| DAQ97-90000 | Keysight DAQ970A/DAQ973A Data Acquisition System User's Guide | Edition 3, June 3, 2020 | `keysight-daq970a`（SCPI 命令见 DAQ97-90002） | https://www.batronix.com/files/Keysight/DAQ/DAQ970A-973A-Manual.pdf |
| DAQ97-90002 | Keysight DAQ970A/DAQ973A Data Acquisition System Programming Guide | Edition 3, March 2020 | `keysight-daq970a` | https://www.batronix.com/files/Keysight/DAQ/DAQ970A-973A-Programming.pdf |
| 33500-90901 | Keysight Trueform Series Operating and Service Guide | © 2013–2015（未印版次） | `keysight-33622a` | https://fotronic.asset.akeneo.cloud/pdfs/media/usedequipment_33500b_33600a_series_manual.pdf |
| 1178.3834.02 | R&S SMA100B RF and Microwave Signal Generator User Manual | Version 14（固件 5.40.102.xx） | `rs-sma100b` | https://scdn.rohde-schwarz.com/ur/pws/dl_downloads/pdm/cl_manuals/user_manual/1178_3834_01/SMA100B_UserManual_en_14.pdf |
| 8588A-rpeng0000 | Fluke 8588A/8558A Reference Multimeter and 8½ Digit Multimeter Remote Programmer's Manual | March 2019 | `fluke-8588a` | https://xdevs.com/doc/Fluke/8588A_8558A/8588A___rpeng0000_0.pdf |
| 8588A-pseng0000 | Fluke 8588A Reference Multimeter Product Specifications（量程、测量电流、输入阻抗） | March 2019, Rev. A | `fluke-8588a` | https://docs.ampnuts.ru/eevblog.docs/Fluke/8588A_8558A/8588Apseng0000.pdf |
| N6705C-90001 | Keysight N6705C DC Power Analyzer User's Guide | Edition 3, February 2019 | `keysight-n6705c` | https://tjr-lab.mit.edu/wp-content/uploads/2023/10/Keysight-N6705C-User-Manual-9018-01649.pdf |
| N6700C-90901 | Keysight N6700C Low-Profile Modular Power System Operating and Service Guide（SCPI Programming Reference） | Edition 2, February 2019 | `keysight-n6705c` | https://static.chipdip.ru/lib/051/DOC036051960.pdf |
| 5730A-omeng0100 | Fluke 5730A High Performance Multifunction Calibrator Operators Manual | August 2013 Rev. 1, 6/15 | `fluke-5730a` | https://docs.ampnuts.ru/eevblog.docs/Fluke/5730A/5730A___omeng0100.pdf |
| 5522A-omeng0000 | Fluke 5522A Multi-Product Calibrator Operators Manual（PN 3795084） | January 2011 | `fluke-5522a` | https://docs.ampnuts.ru/eevblog.docs/Fluke/5522A/5522a___omeng0000.pdf |
| 9018-07723 | Keysight Infiniium UXR-Series Oscilloscopes Programmer's Guide | Version 11.60.00000, March 2024 | `keysight-uxr0334b` | https://www.keysight.com/us/en/assets/9018-07723/programming-guides/9018-07723.pdf（取自 https://www.xtest.at/wp-content/uploads/2024/06/9018-07723.pdf） |
| 3123-1313 | Keysight Infiniium UXR-B Series Oscilloscopes Data Sheet（量程、偏置、采样率、触发） | 2024-04-25 | `keysight-uxr0334b` | https://www.keysight.com/us/en/assets/3123-1313/data-sheets/Infiniium-UXR-B-Series-Oscilloscopes.pdf（取自 https://www.xtest.at/wp-content/uploads/2024/06/Infiniium-UXR-B-Series-Oscilloscopes.pdf） |
| N9060-90027 | Keysight X-Series Signal Analyzer Spectrum Analyzer Mode User's & Programmer's Reference（PXA/MXA/EXA/CXA/MXE；N9042B UXA 的 SA 模式命令集相同） | August 2014 | `keysight-n9042b` | https://xdevs.com/doc/HP_Agilent_Keysight/N9060-90027%20X-Series%20Signal%20Analyzer%20Spectrum%20Analyzer%20Mode%20User%27s%20and%20Programmer%27s%20Reference%20%5B3030%5D.pdf |
| 8600-series-programming-manual | B&K Precision 8600 Series Programmable DC Electronic Loads Programming Manual（型号 8600–8616） | v051118（© 2015-2016） | `bk-8614` | https://bkpmedia.s3.us-west-1.amazonaws.com/downloads/programming_manuals/en-us/8600_Series_programming_manual.pdf |
| 8600-series-datasheet | B&K Precision 8600/B Series Programmable DC Electronic Loads Data Sheet（额定值、量程、保护） | 2025 | `bk-8614` | https://bkpmedia.s3.us-west-1.amazonaws.com/downloads/datasheets/en-us/8600_Series_datasheet.pdf |
| maui-remote-control-automation | Teledyne LeCroy MAUI Oscilloscopes Remote Control and Automation Manual | February 2026 | `lecroy-wavepro804hd` | https://cdn.teledynelecroy.com/files/manuals/maui-remote-control-and-automation-manual.pdf |
| waveprohd-datasheet | Teledyne LeCroy WavePro HD High Definition Oscilloscopes (2.5 GHz - 8 GHz) Datasheet（带宽、采样率、存储、垂直/偏置/时基、触发） | `waveprohd--ds-11dec25`, © 2025 | `lecroy-wavepro804hd` | https://cdn.teledynelecroy.com/files/pdf/waveprohd-datasheet.pdf |
