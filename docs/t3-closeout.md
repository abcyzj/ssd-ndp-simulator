# T3 / M1 闭环贯通收口

2026-10-31（Asia/Shanghai）。T0 数据/建图/预算锁、T1 schema 与 T2 图/真值依赖已核对。开工工作区干净；MQSim/SST HEAD 分别为 `8f7ddf6d8cafdb7c783b3fcfd4693f15c6c5854b` / `ffd71527bc26a1f2a4fb685fdeb2ca0799997d78`，未改子模块、初始化依赖、提交或推送。T3 完成，M1 小规模闭环出口完成，本轮不开始 T4 或其他下一任务。

实现独立 dataset-v2/index-v1/truth-v2 的 SST 接线、experiment-v2 配置、入口/完整身份验证、搜索/控制成本/布局/数值版本元数据、原始 query ID 和双质量指标。K≥k 与 ID-only 不再被旧 truth 解析器拒绝。缺少显式 v2 数据、错入口、错身份/映射、未知版本和损坏产物在 SST 前拒绝，不回退生成合成数据。现有 GraphSearch、异步服务可见性、读取路径、计费和 v1 默认保持原行为；不是 NSG 搜索复现。接口与复跑见 [闭环说明](t3-closed-loop.md)。

`compare_placements.py` 新增 v2 输入解码/身份检查，保留独立逐批距离、候选、扩展、top-k、完成因果和流量账本断言。`verify_t3.py` 保留可重复运行的真实小图与内容扰动验收，失败 manifest 不标成功；新增 CTest `simulation_input_schema`。

本轮实际执行以下命令，均退出 0：

```sh
cmake -S . -B build/delivery -DBUILD_TESTING=ON
cmake --build build/delivery -j2
ctest --test-dir build/delivery --output-on-failure
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tests/test_simulation_input.py
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/verify_t3.py \
  --prepared results/v0.2-t2/real-final --build build/delivery --output results/v0.2-t3/verified
git diff --check
```

根配置复用有效 SST 缓存，不是干净 SST 重建。最终 CTest **56/56**，新增工具 **2 类测试**通过。既有 CTest 包含 v1 两放置、并发/输出、quick acceptance 等回归；不称完整 AC。构建仍有既有上游编译警告和重复静态库链接警告，不影响退出码。常规日志保存于 `results/v0.2-t3/t3-{configure,build,ctest-final,unit}.log`。

最终真实输入复用 T2 的 SIFT 10k base（原 ID `[1,0,2,...,9999]`）、32 条预留诊断 query 原始行 0..31、锁定 Faiss NSG 固定槽图和匹配独立 FP64 真值；未使用正式调参/评价查询。入口 3732，k=10、L=32、B=1、E=64、chunk=16、并发=1、native-small。原始输入两放置均 Recall@10=1.0、1-recall@10=1.0，1,236 次逐批决策一致；距离按 abs 1e-9 + rel 1e-7 核对。

原输入主机向量 D2H=9,919,488 B，盘控向量 D2H=0 B；盘控本地向量读取=9,919,488 B，必要普通读取=616,448 B，平台消息 payload=548,424 B，wire=693,744 B。普通 NVMe 命令/CQ/数据账本独立核对通过，credit 全部归零，最终内容/运行时结果无泄漏。

受控扰动只交换局部 ID 9494 与 3748 的实际向量，保留图、查询和 ID，显式记录 counterfactual，重绑身份并重算真值。首个入口扩展保持一致，下一次扩展与后续实际读取改变；扰动输入两放置也通过完整逐批/距离/账本对照。该图不声称是扰动后重建的高质量 NSG。

最终证据在忽略目录 `results/v0.2-t3/verified/`：manifest、assertions、AC 映射、有效配置、逐条命令/退出码/墙钟/RSS/日志、两份输入各两放置的结果与详细事件，以及扰动前后决策。先前 `real/`、`final/` 是整理过程中的运行，交接以 `verified/` 为准。新检出不自带这些本机产物，须先按 T2 复建再运行 T3。

限制：仅为小规模 M1 / AC-02-05 验收，未执行完整 V0.1 AC、原生 MQSim oracle、ASan、完整 SIFT1M/Deep10M 两放置矩阵或 Deep 正式建图。官方 ID-only 沿用 T2 校验与共用质量接口，本轮未用完整 base 官方真值跑 SST。内容仍全量驻留，多类记录仍随历史事件增长；native-small-only、预装载、RSS 完成后检查和磁盘快照限制仍在。硬件参数均 configured_unvalidated，不声称绝对性能已校准或盘控一定更快。T4～T8 清单保持未完成。

最终阶段资源（wait4 子进程峰值，macOS bytes 转 MiB；不是整进程树同时驻留总和）：

| 阶段 | 墙钟 s | 峰值 RSS MiB | 退出码 |
| --- | ---: | ---: | ---: |
| 真实输入两放置与对照 | 33.970 | 718.3 | 0 |
| 扰动独立真值 | 1.777 | 30.5 | 0 |
| 扰动输入两放置与对照 | 29.893 | 951.9 | 0 |

本轮最终证据目录文件大小快照 296,018,604 B，不是磁盘峰值。最终四个放置 manifest 与阶段 manifest 中记录的实现源码哈希均已与当前工作区复核一致。
