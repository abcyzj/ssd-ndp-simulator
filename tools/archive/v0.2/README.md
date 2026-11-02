# V0.2 历史工具

这些入口只用于重现 T0/M0 阶段的可行性测量和当时证据，不属于 V0.3 默认工作流。阶段状态不继续生效，Deep10M 不在现行范围。

| 原路径 | 归档入口 | 用途 |
| --- | --- | --- |
| `tools/m0/run_probes.py` | [run_probes.py](run_probes.py) | 离线建图规模/布局初始化样本探针 |
| `tools/m0/summarize.py` | [summarize.py](summarize.py) | 原 T0 证据汇总，包含当时 Deep10M 样本与固定53项CTest检查 |

从仓库根目录执行 `python3 tools/archive/v0.2/run_probes.py --help` 或 `python3 tools/archive/v0.2/summarize.py --help`。根路径、共用模块导入和初始化配置路径已迁移；复跑参数见[M0 历史记录](../../../docs/archive/v0.2/m0-closeout.md)。`run_probes.py` 要求新输出目录；`summarize.py` 会重写指定证据目录的 assertions/ac-matrix，仅在需重建该历史汇总时使用。

`verify_m0.py` 仍被生产准备/运行工具共用，`m0/build_builder.py` 与其 CMake/faiss_probe 仍共同构建正式索引适配器及探针；这些不能作为废弃工具搬走。`verify_t2.py`～`verify_t6.py` 和 `verify_v02.py` 仍供继承回归与基线复现，见[工具索引](../../README.md)。
