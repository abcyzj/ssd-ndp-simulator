# 工具入口

采集、校验、拟合、模拟及比较通过 [ndpsim CLI](../python/CLI.md) 执行。下列独立开发工具各有明确用途。

| 工具 | 用途 |
| --- | --- |
| [prepare_sst.py](prepare_sst.py) | 根构建使用的锁定 SST 缓存准备 |
| [verify_mqsim.py](verify_mqsim.py) | 锁定原生 MQSim 与真实 SST 普通 I/O 对拍，需要测试构建 |
| [audit_v045_baseline.py](audit_v045_baseline.py) | 核对文件、CTest 与正式矩阵的迁移清单 |
| [benchmark_data.py](benchmark_data.py)、[benchmark_artifacts.py](benchmark_artifacts.py)、[m0/build_builder.py](m0/build_builder.py) | 原生数据导入、图和真值准备 |
| [verify_m0.py](verify_m0.py) | 数据检查、命令测量和源码身份工具 |

`archive/` 记录对应历史版本的开发流程。旧模拟、兼容矩阵和阶段工具请在对应版本源码上复现。 [发布记录](../docs/README.md#版本基线与历史追溯)
