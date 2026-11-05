# V04-5a SSD B/C 与敏感性

2026-11-05 完成 [PRD §6.6](prd-v0.4.md#66-ssdcontroller-条件预测)、FR-04-O01～O04、AC-04-10 对应的 O01～O04。前置的静态包、冻结模型、Validator、Level A 和单线程 Level B/C 证据见 [任务清单](implementation-plan-v0.4.md)、[Level A](level-a-v0.4.md)及 [Level B/C](level-bc-v0.4.md)。本机完整证据在 results/v0.4/offload-v045a/，正式四点在 run4/，evidence.json 登记源码、构建、依赖、检查及全部产物 SHA256。

## 执行与成本边界

静态入口显式设置 execution_engine=replay、data_provider=ssd、order_policy=legacy-ssd/1、native_workers=1、max_inflight=1、host.slots=1，并绑定完整语义包和 CPU Profile。Controller 通过 tools/run_config.py 运行。content_backend=mmap 或 memory 只决定功能内容后端，目标数据路径由 data_provider 决定。

- B 从普通 NVMe 读取本次实际向量和邻接。Host 准备和 L2 等待 Runtime 读结果发布，CPU 阶段用显式 Profile 收费。
- C 在 INIT 完成后上传本次 FP32 query，等待 OpenAck。Controller 根据配置容量对当前已核验的向量 ID 生成物理 chunk，经本地读取、缓冲端口、算术和 ScoreResult 返回后，Host 才能拼接及核验批次。Host 不获取完整向量。
- 每个操作核验真实输出、ID 顺序、状态、逻辑访问和 Work，匹配后才开放后继。盘控逐块真实 Work 先汇总，语义 Work 中的 query 读取次数按语义 chunk 归一，物理读取字节另记 replay_controller_work。
- 结果可见时记录查询结束。Close 等待全部已发块与返回排空，CloseAck 后归还接纳槽。值或 Work 失配会停止后继并排空。SST 仍为时间权威。
- SSD 静态回放省略 cpu_profile 时使用旧成本，供继承对拍。零成本依赖门控保留旧任务提交时间，实际控制服务等待前序静态核验通过后开始。内存回放仍要求显式 Profile。

本轮采用以下组件来源和假设。数据交付、CPU 准备、L2 和缓冲端口各有独立计费边界。

| 阶段或组件 | O 组来源与适用条件 | 唯一计费者 |
| --- | --- | --- |
| query 初始化、L2、merge_append、merge_finalize、select、dedup、finish | 复用 tuning32 冻结 v3 模型，绑定原生采集构建、本机条件及特征范围。SSD 返回缓冲场景的迁移为条件假设 | HostCpu；C 的 L2 由 Controller 替代 |
| B score_prepare | 配置假设 ceil(1 + vectors) ns，仅计返回向量的 CPU 准备，范围 0～16 | HostCpu |
| B/C adjacency_prepare | 配置假设 ceil(1 + edges) ns，计实际有效邻接 decode 和行准备，范围 0～64 | HostCpu |
| B/C MARK_SCORED / MARK_EXPANDED | 配置假设 ceil(1 + objects) ns，单列实际 scored / expanded 插入，范围分别 0～64、0～1 | HostCpu |
| NAND、FTL、普通读取、普通 DMA | ann-2g-v1，确定性预装载、SIMPLE / TURNED_OFF、保留 CMT | MQSim |
| query / Score / 结果 / Close 传输 | 既有共享 PCIe 封包与链路 | MQSim PCIe |
| C 本地向量读取 | 既有 Controller 本地 route | MQSim |
| C 缓冲和算术 | 容量 16,384 B、端口 16 B/ns、1 slot、launch 1 ns、算术 8/16/32 flop/ns | BufferModel / Controller |

配置 CPU 准备假设来自既有 Host control_setup=1、control_rate=1，每返回对象、边或插入 ID 使用一个控制单位。CPU Profile 的这四项明确记录 samples=0、error=null，仅允许在 ssd_return_buffer 的准备或 state 阶段使用。Python 与 C++ 均拒绝在驻留阶段或 L2 等其他阶段使用该来源标记。其余模型保持原训练来源、误差字段、特征范围及失败关闭规则。

A 的 v4 merge_append 实测表绑定驻留条件，O 组采用冻结 v3 的预测模型，并披露迁移假设。既有 evaluation128 暴露记录继续保留，评价数据未回流拟合。本次没有重新拟合或使用 QPS、墙钟残差补成本。SSD、PCIe、Controller 及 SSD 返回缓冲 CPU 准备均为 configured_unvalidated。

## 四点结果

完整 SIFT1M 为 1,000,000×128，使用锁定 NSG 图、固定 evaluation128、L/E=64/128、k=10、B=1、语义 chunk=16。四点共用 V04-4b 冻结后采集的同一完整包，SHA256 为 726c7f6b9d98e94bab88e0a487a5122e00eb51aeecb85312d9cd29386b19ff68。每点执行 71,324 个操作，独立核对全部 128 查询。

| 配置 | placement / 算力 | QPS | P50 ms | P95 ms | QPS / B |
| --- | --- | ---: | ---: | ---: | ---: |
| O01 | Host 参照 | 6.0491 | 168.577 | 238.199 | 1.0000 |
| O02 | Controller 0.5×，8 flop/ns | 9.3774 | 107.546 | 153.930 | 1.5502 |
| O03 | Controller 1×，16 flop/ns | 9.3799 | 107.521 | 153.887 | 1.5506 |
| O04 | Controller 2×，32 flop/ns | 9.3811 | 107.508 | 153.865 | 1.5508 |

四点结果、每批决策、逻辑访问与语义 Work 相同。C 三点的实际 chunk 均为 16，每点 18,878 块，Host 完整向量 D2H 为 0。盘控算力从 0.5× 增至 2×，QPS 从 9.3774 变为 9.3811，这组参数下算术速率变化对端到端指标的影响较小。报告保留全部点。

| 资源 | O01 | O02 | O03 | O04 |
| --- | ---: | ---: | ---: | ---: |
| Host 总 CPU ns | 2,387,462,109 | 2,256,715,758 | 2,256,715,758 | 2,256,715,758 |
| Controller 算术 ns | 0 | 11,290,958 | 5,654,918 | 2,836,898 |
| 普通 DMA B | 124,751,872 | 4,516,352 | 4,516,352 | 4,516,352 |
| 本地向量 sector B | 0 | 120,235,520 | 120,235,520 | 120,235,520 |
| PCIe wire B | 209,544,160 | 15,222,880 | 15,222,880 | 15,222,880 |
| 缓冲峰值 B | 0 | 16,144 | 16,144 | 16,144 |
| credit 等待 ns | 0 | 6,044,363,290 | 6,041,193,370 | 6,039,608,410 |

等待、服务和累计资源忙时有重叠，不能相加为端到端延迟。C 的 Close 排空在最后结果后另需 38 ns。各点保留 Host 依赖/排队、盘控服务/排队、本地读取、链路、缓冲、逐查询和初态记录。

## 运行与证据

运行 tools/evaluate_v04_offload.py，使用 --prepared、--capture、--profile、--fit-report、--build 和新的 --output。正式条件预测另传 --level-a 两点报告及 --level-bc A01/A02 报告。实际完整命令保存在 results/v0.4/offload-v045a/run.sh，输入路径沿用 [Level B/C](level-bc-v0.4.md) 的准备数据、主配置采集包和冻结 v3 模型。

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 -B tools/evaluate_v04_offload.py \
  --prepared results/v0.2-t6/preparation/prepared \
  --capture results/v0.4/level-a-v044b/v4-collection-host/evaluation-l64-e128-capture \
  --profile results/v0.4/cpu-fit-v043b/split-merge-v3/frozen/cpu-profile.json \
  --fit-report results/v0.4/cpu-fit-v043b/split-merge-v3/frozen/fit-report.json \
  --level-a results/v0.4/level-a-v044b/v4-two-point/level-a.json \
  --level-bc results/v0.4/level-bc-v044c/reports/A01/level-bc.json results/v0.4/level-bc-v044c/reports/A02/level-bc.json \
  --build build/delivery --output NEW_OUTPUT_DIRECTORY
```

输出目录必须不存在。入口先核验准备数据、冻结拟合身份、CPU 前提、RAM、磁盘和输出预算，再保存固定四点计划。省略 Level A/B/C 前提参数时，结果明确标记 exploratory_cpu_prerequisite_unverified。完整报告为 offload.json/md，配置、派生 SSD Profile、进程收据和逐点审计一并保存。

run4/ 是四配置各一次正式模拟及独立资源核验。checks/ 保存根配置、构建、CTest 和定向测试。independent_cpu.py 与 independent-cpu.json 另以独立 Python 搜索/计数公式逐操作重算值、Work 和逐阶段 CPU 需求，并保存全量日志内对应 detail8 的核验摘要。未增加新的完整规模配置。

定向夹具覆盖 Host/Controller 与 direct、动态 IR 的逐项查询时序、请求、消息、credit、控制/L2 服务账本对拍，比较所有服务提交/就绪/开始/结束时间。容量 600 B 的夹具将语义 chunk=2 映射为盘控物理 chunk=1，核对实际 Work 及结果。错误准备条件、重封包后的错误输出/Work、盘控坏返回和旧成本坏返回均拒绝，失败样本保留并核验排空。

开发样本保留在 run1/～run3/。其中包括缺失配置成本来源字段、将有界协议 tombstone 误判为活动泄漏，以及构建在开发期间改变导致的身份拒绝。run4/ 的四点使用同一最终模拟库。

本机 Apple M4 / 16 GiB。SST 子进程墙钟约 18.05～25.43 s，峰值 RSS 约 804～819 MiB，外层运行进程峰值低于 4 GiB 预算。墙钟仅用于效率与预算记录。开始、结束 swap 用量均为 558.25 MiB，未建立逐阶段缺页或物理 CPU 频率/绑核控制。

AC-04-10 按条件预测口径通过。适用范围为本次记录的数据、单接纳槽、模型边界及硬件配置。旧成本定向对拍和本轮根 CTest 支持局部兼容判断，完整 R 组与继承验收仍由 V04-5b 关闭，V0.4 总交付仍由 V04-5c 汇总。
