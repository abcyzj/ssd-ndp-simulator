# P19 正式 SSD 账本与条件预测

日期为 2026-11-07（Asia/Shanghai）。P19 四点正式账本及两组条件比较均通过，AC06 证据留至发布汇总。

依据 [PRD FR05、FR07、AC-045-06](prd-v0.4.5.md)、[正式矩阵 §6](implementation-plan-v0.4.5.md#6-正式验收矩阵与采集日程)及 [P01 冻结调度](contract-v0.4.5.md#6-配置权威和正式调度冻结)。本机证据目录为 `results/v0.4.5/p19/run1/`。

## 输入和配置

重新核对 P13、P15、P16 工程记录、P16 独立 MQSim 对拍、P18 正式包证据的 403／5,283／65／234／2,149 项产物 hash。两个点复用 P18 的完整 SIFT1M、重建 NSG 和 128 个原始 evaluation 查询，保持矩阵顺序、k=10、B=1 和语义 chunk=16。重新核对两个点的原始 ID、采集语义配置、prepared 内容 hash 及独立语义 receipt。运行只需要 IR 元数据和配置快照。

四点共用 `ann-2g-v1.xml`、4 KiB 对齐的 packed Layout、同一份 Profile 和资源参数。Layout 命名空间为 2,000,000 sectors，设备预装载窗口按实际 extent 末端向设备页对齐。offered=0、FIFO，workers／Host slots／admission=1/1/1。Controller 容量 16,384 B、query bound 512 B、block bound 8,192 B、staging 16 B/ns、compute slots=1。配置的完整向量块最多 15 项，实际物理调用按各自 Work 估算。每个 B/C 对的原始运行配置只在 `placement` 和 `context` 上有差异。

## 配置参数与验证实现

Profile 采用 `source=configured`，以 `parameter_source` 声明情景出处。C++ 与 Python 均验证公式身份、参数、Work／单位、特征域及来源互斥。报告分列配置参数、拟合预测和逐调用表。驻留 CPU accuracy 层拒绝配置参数来源。SSD 的两个准备上下文各自匹配合法规则，Controller 分块使用每块的实际 Work。所用情景继承历史 SIFT1M 配置的 Host L2 和 Controller L2 速率，另外显式声明准备、状态和其他 Host 阶段的系数。情景从未使用 evaluation128 的耗时，也未复用驻留模型或表。

| 阶段 | setup（ns） | rate（加权单位/ns） | 权重 |
| --- | ---: | ---: | --- |
| INIT | 1 | 16 | copied_bytes × 1 |
| MARK 状态 | 1 | 1 | objects × 1 |
| score_prepare | 1 | 16 | vectors × 4、copied_bytes × 1、decoded_bytes × 1 |
| score_l2（Host／Controller） | 3／1 | 4／16 | flops × 1 |
| merge_append | 1 | 16 | copied_bytes × 1、released_vectors × 16 |
| merge_finalize、dedup | 1 | 1 | comparisons × 1 |
| select | 1 | 1 | candidate_checks × 1 |
| adjacency_prepare | 1 | 16 | copied_bytes × 1、decoded_bytes × 1 |
| JOIN | 0 | 1 | 无 |
| FINISH | 1 | 1 | objects × 1 |

服务按 `setup + ceil(加权特征和 / rate)` 计算。上述 Host 需求描述数据就绪后的服务，NAND、PCIe 和 Controller staging 各由对应资源单独计费。上下文投影详见 [P13](graph-ssd-v0.4.5.md)。

独立 Validator 读完整 trace，逐个核对 PCIe 包的入队、开始与到达，以及 wire 取整和方向，随后归并字节。保留在途包和已完成 ID 以拒绝缺项和重复，同时增量计算原有规范化轨迹摘要。图账本沿 L2 的向量 DATA 依赖定位具体 prepare 阶段，再独立推导 sector 和 LBA、块 Work、服务、读写流量、credit 生命周期及排空。原始轨迹完整保留。验证器仍驻留阶段、非 PCIe 记录和已完成包 ID，内存开销仍随规模增长。

## 正式运行结果

| 配置 | L/E | 延迟 P50（ms） | 延迟 P95（ms） | 模拟 QPS | 普通读 | 本地读 | 完整向量 D2H（B） |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| B01 Host | 32/64 | 99.870 | 142.444 | 9.946 | 148,358 | 0 | 73,426,944 |
| C01 Controller | 32/64 | 66.090 | 93.702 | 15.035 | 5,101 | 143,258 | 0 |
| B02 Host | 64/128 | 201.409 | 297.985 | 5.012 | 245,554 | 0 | 121,077,248 |
| C02 Controller | 64/128 | 142.316 | 217.230 | 6.968 | 9,245 | 236,311 | 0 |

吞吐用 128 除以首接纳至最后可见的模拟窗口，延迟用各查询的可见时间减接纳时间。Controller／Host 吞吐比分别为 **1.512×** 和 **1.390×**。两个 Controller 点分别执行 17,959 和 29,646 个物理块，buffer 峰值均为 9,088 B。四点最终 buffer、任务、pin 和 session 归零。分层流量、逐调用服务、等待、窗口和排空保存在四份原始报告及独立账本。条件预测仅适用于该 XML、资源、Layout、成本参数和两条固定轨迹。SSD、PCIe 和 Controller 均为 `configured_unvalidated`，驻留 CPU 精度证据另见 [P18](resident-accuracy-v0.4.5.md)。

## 运行与证据

`schedule.json` 保存配置 hash 和预算，`formal-inputs.json` 保存复核的输入及身份，`implementation-identity.json` 保存构建、实际源码和依赖身份。运行通过锁定 Python 调用统一入口，具体 argv、cwd、退出码及资源实测见每个目录的 `invocation.json`、`command.json` 和 `status.json`。

```sh
ndpsim simulate --config <B01/C01/B02/C02-experiment.json> --build build/delivery --output <新目录>
ndpsim verify --spec <对应的正式 simulate 层 spec> --output <新目录>
ndpsim compare --spec <同一点的 B/C 比较 spec> --output <新目录>
```

首次预检实测 16 GiB 物理 RAM、约 8.48 GiB 可用估计、约 86.3 GiB 磁盘可用。预留 20 GiB 磁盘余量，每次输出上限 2 GiB、限时 7,200 秒。B02 实际轨迹 5,417,264 条，超过首版核验配置的 400 万条记录上限。扩大记录预算后保留全部事件。首轮 B01 和 B02 核验退出 2，原因是独立账本按算子键存 prepare，遇到同算子多个语义块误判重复。修复后用新目录对四份原 hash 绑定轨迹重新核验。最终四份独立账本的验证器峰值 RSS 为约 1.79～3.39 GiB，均低于 4 GiB 上限。预算为实测退出门控与轮询检查。

根构建成功。完整 CTest 两次均为 140/140，最后修正多语义块绑定和普通读汇总后，重跑 `layered_validator_v045|graph_ssd_v045`，2/2 通过。定向 ASan `cost_model_v045|graph_ssd_v045` 2/2，P00 门控 3/3，P01 契约 11/11，迁移审计覆盖 417 个文件及 140 项 CTest。`git diff --check` 通过。

保留了两类调试证据。多语义块夹具首版 chunk=2 未触发多 prepare 断言，最终改为 chunk=1 并确认同一算子实际含多个 prepare。Controller 的首轮通过报告里，局部 item 计数覆盖了普通读汇总字段。独立普通读断言已通过，修正字段后在新目录复验，并逐项比较独立账本与运行报告的普通读、本地读和向量 D2H 计数。
