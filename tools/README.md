# 工具入口

从仓库根目录执行。V0.3 尚未实现 IR，以下均为仍需维护的直接执行、数据准备及继承回归工具；阶段编号保留兼容，不表示当前开发任务。

| 日常与准备工具 | 用途 |
| --- | --- |
| `run_experiment.py` | 复用已有构建，只运行实验；见[README](../README.md) |
| `run.py` | 根构建、CTest、两放置运行 |
| `prepare_sst.py` | 根 CMake 调用的锁定 SST 准备/缓存 |
| `run_config.py`、`run_graph.py`、`run_pair.py` | 配置执行、共用 SST 图搜索与两放置运行 |
| `ann_data.py`、`experiment_output.py` | 合成输入、独立真值与统计导出 |
| `benchmark_data.py` | 四种 benchmark 格式导入/check；[数据说明](../docs/data-v2.md) |
| `benchmark_artifacts.py` | build/check-index/exact/official/check-truth；[图与真值](../docs/index-truth-v2.md) |
| `m0/build_builder.py`、`m0/CMakeLists.txt` | 锁定离线 Faiss、正式索引适配器及 receipt；仍是生产准备依赖 |
| `simulation_input.py` | experiment-v2 与数据/图/真值身份校验；[仿真输入](../docs/simulation-input.md) |
| `device_profiles.py` | 根 CMake 与运行器共用的设备生成器；[内容/profile](../docs/content-profiles.md) |
| `verify_m0.py`、`source_snapshot.py` | 共用完整原始文件校验、哈希、进程测量及源码身份；被运行/准备工具导入，不是废弃阶段脚本 |
| `scan_compute_ratio.py` | 可选算力敏感性扫描；参数与历史结果见[归档实验](../docs/archive/v0.2/t6-compute-ratio.md)，不属于固定验收矩阵 |

## 继承回归与基线复现

| 入口 | 覆盖与说明 |
| --- | --- |
| `compare_placements.py` | 独立搜索、两放置逐批决策与资源账本核对 |
| `verify_concurrency.py` | 并发、接纳与输出回归 |
| `verify_acceptance.py` | 完整 V0.1 AC-01～11；`--quick` 仅为 CTest 小矩阵 |
| `verify_contract.py` | 独立 ABI、语义账本与负例核查 |
| `verify_t2.py` | SIFT 10k 重排子集、32 条诊断查询的图/精确真值及完整 base 官方真值导入 |
| `verify_t3.py` | 真实小 SIFT 两放置、内容扰动与逐批账本；[接口](../docs/simulation-input.md) |
| `verify_t4.py` | memory/mmap、2/16GiB profile、跨页与 credit；[接口](../docs/content-profiles.md) |
| `verify_t5.py` | Q=32/128/512 三次重复、日志等价、回收与预算拒绝；[接口](../docs/observation-lifecycle.md) |
| `verify_t6.py` | 完整 SIFT1M 准备、固定26次矩阵及严格复用；[入口](../docs/sift1m.md) |
| `verify_v02.py --all` | V0.2 完整基线复现；[前置准备与命令](../docs/archive/v0.2/delivery.md) |

完整方法见[测试说明](../docs/testing.md)。所有有效测试和断言保留；上述工具通过不能直接声明 V0.3 通过。`verify_t6.py --prepare-only` 与 `--prepared` 分离准备/仿真，`--reuse-runs` 必须逐项验证身份；旧结果不能冒充本轮 direct 冻结或 IR 结果。

## 历史工具

[V0.2 工具归档](archive/v0.2/README.md)保存一次性 M0 可行性探针与旧证据汇总；[V0.1 工具归档](archive/v0.1/README.md)保存早期阶段脚本，不作为默认开发入口。现行工具不导入这些归档脚本。

`run_graph.py` / `run_pair.py` 的低层 CLI 默认 smoke 仍引用 V0.1 归档配置以保持行为；CTest 使用显式 regression 配置，日常入口使用 experiments 配置。输出目录与输入准备规则以各工具 `--help` 和接口文档为准，不覆盖历史证据。

## 本机 HostCPU 粗测

从根目录执行：

```sh
cmake -S . -B build/delivery -DBUILD_TESTING=ON
cmake --build build/delivery --target bench_host_cpu -j2
build/delivery/bench_host_cpu
```

固定随机种子 42、128 维 FP32、batch=1/16/32/128；直接运行现有距离和 candidates 算子，控制保留 top-10。每项预热 100 次后测 7 组、每组至少 20 ms，输出每调用耗时中位数及默认模型预测（距离 setup=3/rate=4，控制 setup=1/rate=1、两种权重均为1）。`measured_over_model` 大于1表示本机实现慢于模型。校验和防止结果被消除。

这是单线程、重复访问相同输入的缓存热场景，包含解码、分配、ID检查、打包及测量循环开销；不是 SIMD 上限、随机冷内存、SST查询端到端或完整AC验收。请使用优化构建且关闭 NDPSIM_SLOW_FUNCTIONAL；记录机器型号、编译器、构建选项和负载状态再比较，不自动回填或宣称硬件参数已校准。
