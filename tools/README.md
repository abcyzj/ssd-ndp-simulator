# 工具入口

日常实验用 `run_experiment.py`；构建、CTest 与两放置交付用 `run.py`。均从仓库根目录执行。使用方法见[项目说明](../README.md)。

| 持续维护的工具 | 用途 |
| --- | --- |
| `prepare_sst.py` | 根 CMake 调用的锁定 SST 准备/缓存 |
| `ann_data.py`、`experiment_output.py` | 真实输入、独立真值、统计导出 |
| `run_graph.py` | 共用 SST 运行实现及主机图回归入口（原 run_m3_1.py） |
| `run_pair.py`、`compare_placements.py` | 两放置回归运行和独立账本核对（原 run_m3_4.py、compare_m3_4.py） |
| `run_config.py` | 按配置运行两种 placement（原 run_m4_1.py），被交付和验收调用 |
| `verify_concurrency.py` | 并发、接纳与输出回归（原 verify_m4_1.py） |
| `verify_acceptance.py` | 完整 AC-01～11；`--quick` 仍供 CTest 调用（原 verify_m4_2.py） |
| `verify_contract.py` | 独立 ABI、语义账本与负例核查（原 verify_m0_4.py） |
| `source_snapshot.py` | 共享 MQSim 源码身份记录，原 verify_m1_1.py 中的函数原样提取 |
| `verify_m0.py`、`m0/build_builder.py`、`m0/run_probes.py`、`m0/summarize.py` | V0.2 T0 的原始文件完整校验、当前源码身份、独立进程资源记录、锁定离线 Faiss 构建、建图/初始化探针及证据核对；不是正式数据转换或规模查询验收，见 [M0 说明](../docs/m0-closeout.md) |

[阶段复现脚本](archive/v0.1/README.md)保留历史验证逻辑，可按需执行，不是默认开发任务。当前工具不导入归档脚本；测试源码仍在 tests/，CTest 名称、断言与执行范围保留。旧路径已迁移，不留重名副本；旧命令请按本页映射更新。run_graph.py / run_pair.py 的低层 CLI 默认 smoke 仍引用归档配置以保持原行为，CTest 使用显式 regression 配置；日常 run.py / run_experiment.py 默认使用 experiments 配置。

## V0.2 数据准备（T1）

`benchmark_data.py import spec.json --output <新目录>` 与 `check <目录>`：四种 benchmark 格式转 dataset-v2，使用与限制见 [数据说明](../docs/data-v2.md)。图/真值绑定见 T2，模拟接线见 [T3](../docs/t3-closed-loop.md)。

## T2 离线图与真值

`benchmark_artifacts.py` 提供 build/check-index/exact/official/check-truth；`verify_t2.py` 实跑锁定 SIFT 10k 重排子集建图、32 条诊断 query 精确真值及完整 base 官方 ID-only 导入。`m0/build_builder.py` 现同时构建正式离线适配器并输出 receipt；不链接正式 SST。接口、复跑与限制见 [图与真值](../docs/index-truth-v2.md)，实际证据见 [T2 收口](../docs/t2-closeout.md)。

## T3 真实小数据闭环

`simulation_input.py` 校验 experiment-v2 与独立数据/图/真值身份；`verify_t3.py` 运行真实 SIFT 两放置、独立逐批/账本对照和内容扰动。接口见 [T3 闭环](../docs/t3-closed-loop.md)，不启动下一任务。
