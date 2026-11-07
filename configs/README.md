# 配置入口

统一 CLI 使用 `ndpsim-experiment/1` 引用 `ndpsim-memory-run/1` 或 `ndpsim-graph-ssd-run/1`。文件引用相对其声明文件解析，有效配置和实际输入随输出保存。参见 [CLI](../python/CLI.md)。

| 目录 | 用途 |
| --- | --- |
| [devices/](devices/) | SSD 模拟显式加载的外部 MQSim XML 样例 |
| [layouts/](layouts/) | 逻辑对象到 LBA 的元数据布局样例 |
| [regression/](regression/) | CTest 和独立 MQSim 对拍配置 |
| [benchmarks/](benchmarks/) | 原始数据、离线图构建及查询分割锁 |
| [native/](native/)、[archive/](archive/)、[experiments/v0.2/](experiments/v0.2/) | 历史输入和实验记录 |

旧 `execution_engine` 与默认配置已退出当前运行入口。历史复现使用对应交付源码和配置。 [V0.4 交付记录](../docs/releases/v0.4.md)
