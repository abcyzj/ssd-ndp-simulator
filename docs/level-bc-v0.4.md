# V04-4c 单线程 Level B/C 与静态语义

依据 [PRD §6.4、§6.5、§8](prd-v0.4.md)、[实施清单](implementation-plan-v0.4.md)和[验收矩阵](validation-matrix-v0.4.md)。V04-1c、2b、3b、3c、3d、4a 已提供参考、采集、模型及执行基础，V04-4b 的两点 Level A 已通过。整机评价使用完整 SIFT1M 和锁定 NSG 图、evaluation128、k=10、B=1、语义 chunk=16。

## 输入与判定

- A01 为 L/E=32/64，A02 为 64/128。两点均用完整静态包、1 worker、1 CPU slot、1 接纳槽、Host placement 和 `host_memory`，并明确启用 `replay`、`worker-fifo/1` 与按点绑定的 v4 Profile。
- 从 V04-4b 复用模型冻结后采集的两个完整包和各三次独立制表测量。重新核验输入、构建、采集和模型身份，逐记录比对原生分片与封包，重建 `merge_append` 表并核对每次实际计费。该阶段使用 evaluation128 实测输入。此前同批评价结果已暴露，预测阶段仍只使用 tuning32 冻结拟合。
- 原生 `benchmark` 模式只在每条查询外层读线程 CPU 时钟，无逐阶段探针和轨迹。每点至少三次独立进程，外层 CPU 时间和延迟均来自这些运行。另用原生 `uninstrumented` 核对输出与 Work。该模式关闭外层 CPU 计时，可用于语义对照。
- 每点两次同配置 SST 回放，比较真实事件、结果、查询时间、资源账本和模拟统计。排除仅用于宿主诊断的墙钟字段及私有快照路径，另报宿主墙钟和峰值 RSS。
- 独立 Python oracle 为两点全 128 查询重算距离、排名、扩展、读取和 Work。A01 的 direct/动态 ir 参考覆盖冻结 detail8，A02 覆盖全 128 查询。A02 另核对同一 detail8 伴随参考与全量参考的轨迹。
- 每项统计分别取三次原生运行的中位数。Level B 使用 P50 ≤15%、P95 ≤20% 的延迟相对误差。Level C 使用 QPS ≤10%、CPU 总量 ≤15%，另报归一占用差。逐查询 CSV 给出 CPU 和延迟的三次原生测量、中位数及相对模拟值的有符号差。证据失配与精度超限分别记为 `evidence_failed` 与 `precision_failed`，均退出 1。

## 入口

原生基准用绑定的仅原生可执行文件运行 `tools/run_native.py --mode benchmark --split evaluation128 --workers 1 --concurrency 1`，关闭详细轨迹。另以 `--mode uninstrumented` 保存纯语义对照。模拟用 [驻留回放入口](replay-v0.4.md)，开启 `event_log`。direct 和动态 ir 的 Host 参考使用相同模拟构建，开启 `semantic_log`，沿用旧成本。

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 -B tools/evaluate_v04_level_bc.py \
  --start-manifest START_MANIFEST --fit-report FROZEN_FIT_REPORT \
  --capture CAPTURE --profile BOUND_V4_PROFILE --samples TABLE_SAMPLE_1 \
  --merge-append-measurement SAMPLE1/measurement.json SAMPLE2/measurement.json SAMPLE3/measurement.json \
  --native BENCHMARK1 BENCHMARK2 BENCHMARK3 --uninstrumented UNINSTRUMENTED \
  --replays REPLAY1 REPLAY2 --direct DIRECT --ir DYNAMIC_IR \
  --output NEW_REPORT_DIR
```

A02 加 `--detail8-references DETAIL_DIRECT DETAIL_DYNAMIC_IR`。报告目录保存 `level-bc.json`、`level-bc.md`、`queries.csv` 和 `detail8.json`。语义证据中包含完整输入、配置、命令、构建与哈希。逐次原生产物保留 QPS、延迟、CPU 总量和资源测量，模拟资源记录保留在对应运行目录。

## 本机验证

2026-11-05 在 Apple M4、16 GiB、macOS arm64 上完成两个正式配置。6 次 `benchmark` 与 6 次无外层 CPU 计时的语义对照均退出 0。正式驻留回放共 4 次，A01 和 A02 各自两次的事件、结果、模拟统计和账本一致。Host 参考共 6 次，包括 A01 detail8、A02 全量与 A02 detail8 各一对 direct/动态 ir，全部退出 0。

| 点 | 全包操作 / 依赖 | 逻辑读取 / SSD 请求 | P50 误差 | P95 误差 | QPS 误差 | CPU 总量误差 | 占用差 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A01 · 32/64 | 39,258 / 92,820 | 146,723 / 0 | 0.99% | 1.94% | 0.40% | 0.29% | +0.11 个百分点 |
| A02 · 64/128 | 71,324 / 168,975 | 243,784 / 0 | 0.64% | 1.04% | 0.58% | 0.54% | +0.04 个百分点 |

两点正式报告均退出 0。全量查询的真实值、逻辑访问、Work、终止和 top-k 核对通过。AC-04-05/06 通过，AC-04-02 的 Host 静态语义及 AC-04-08 的驻留执行部分已验证。A01 SST 子进程峰值 RSS 约 789 MiB，A02 约 794 MiB，四次 SST 子进程墙钟为 11.62、11.74、18.91、17.13 秒。精度判定使用本次 SST 模拟时间。

根 CMake 配置、`-j2` 构建、CTest 91/91、定向 Validator 复跑、Python 语法和 `git diff --check` 均退出 0。定向夹具拒绝重登记的错误模拟统计，拒绝向量变更后的旧包，并通过重采回放与原生、direct、动态 ir 和独立 oracle 的核对。首轮正式回放在读取主机身份的沙箱预检处失败，已保留日志，授权后在新目录完成全部四次回放。早期用关闭 CPU 计时的模式尝试精度验证也保留了失败报告。改动集中于离线核验与测试，本轮未触发 52 条深度回归，完整继承验收另有出口。

本机证据目录为 `results/v0.4/level-bc-v044c/`。正式评价只适用于记录的 Mac、构建、输入和单 worker 驻留条件。频率和核绑定未控制。SSD、PCIe 和 Controller 继续为 `configured_unvalidated`。AC-04-08 的静态 SSD 返回与两种 placement 的严格时序、资源对拍属于 V04-5a。多 worker、继承矩阵和 V0.4 总交付各自判定。
