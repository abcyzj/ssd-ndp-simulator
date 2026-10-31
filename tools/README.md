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

## V0.2 T4

`device_profiles.py` 从 `configs/profiles/devices.json` 与原 native XML 生成正式 profile，根 CMake 自动调用，不需手动准备。`verify_t4.py --prepared <T2产物目录> --build build/delivery --output <新目录>` 验证实际容量/越界、mmap 与 memory、真实小 SIFT 两放置、跨页及 credit；接口和限制见 [T4](../docs/t4-content-profiles.md)。它不开始 T5 或全规模运行。

## T5 观测与生命周期

`verify_t5.py --build build/delivery --output results/v0.2-t5/new-evidence` 运行固定小 base 的 Q=32/128/512、两放置各三次重复、detail/statistics 等价与输出预算拒绝；可加 `--prepared results/v0.2-t2/real-final` 做真实小图两放置独立逐批对照。不是完整规模或交付验收。接口见 [T5](../docs/t5-observation-lifecycle.md)。


## T6 完整 SIFT1M

`verify_t6.py`提供完整base准备、锁定图/官方真值、预注册26次闭环矩阵、预算、重复/并发/detail与质量匹配报告。`--prepare-only`与`--prepared`分离准备/仿真；`--reuse-runs`逐项验证成功独立放置后重新汇总，不能按目录存在跳过检查。配置为`configs/experiments/v0.2/sift1m.json`；接口见 [SIFT1M](../docs/t6-sift1m.md)，实际证据与限制见 [T6收口](../docs/t6-closeout.md)。止于T6，不自动启动T7。

`scan_compute_ratio.py`复用T6准备产物和已有构建，固定Host rate=4，在主配置128/256、评价128条、并发1下扫描盘控rate=1/2/4/8/16。只改变算术rate，保留加载/缓冲计费差异；结果与复跑命令见[算力敏感性实验](../docs/t6-compute-ratio.md)。

可用`--control-rate 4 --controller-rates 4`运行控制rate=4、两放置距离rate均为4的补充情景；默认仍为控制rate=1和五档盘控rate。所有rate须为正整数，扫描点不得重复，输出目录须不存在。


## T8 / M4 交付

`verify_v02.py --all` 统一当前根构建/完整V0.1回归、T3/T4/T5复验、SIFT1M矩阵、主配置两放置新复现和适用AC/manifest。要求锁定Python、`--prepared`完整T6准备、`--small-prepared`T2真实小图/精确真值、`--source-dir`原始SIFT；可选`--reuse-runs`逐项严格核验26次成功运行，否则重跑矩阵。输出须新目录；缺项或失败非零退出且不生成成功结论。完整命令与证据见 [交付说明](../docs/delivery.md)、[V0.2交付记录](../docs/releases/v0.2.md)。不支持规划中未实现的`--stage`模式，不启动后续任务。
