# 多向量 MaxSim 近存计算仿真实验参数配置 v0.1

- 项目：快速近存计算需求分析
- 日期：2026-11-18
- 接收对象：负责负载生成、语义 IR 映射、MQSim 实验与结果分析的 Agent
- 文档性质：根据本次讨论整理的首轮实验配置与参数扫描规范；不是实机标定结果。
- 主负载：参照 Tomoro ColQwen3-4B 的文档页多向量 MaxSim 精排。
- 单位：KB/MB/GB 与 GB/s 使用十进制；KiB/MiB/GiB 使用二进制；乘加计 2 FLOP。

## 1. 背景、目标与范围

当前已有基于 MQSim 和语义 IR 的近存计算仿真平台，可描述计算、访存和数据依赖，并通过 cost model 表示算子在主机与盘侧的执行时间。当前没有运行完整 vLLM/Qdrant 系统并进行实测的硬件条件。

本实验希望回答：在大规模多向量检索中，将候选文档页的 MaxSim 评分下沉到盘侧 DaVinci NPU，何时可以减少延迟、提高吞吐，以及需要多少有效算力。盘侧可以概念性地以 Cube 执行点积矩阵乘法，以 Vector 执行 max/sum 归约；本阶段不假设具体内核已经实现或达到某个利用率。

研究边界从“查询向量和候选 ID 已经产生”开始，到“精排 Top-K 就绪”结束。查询编码、初召回和后续 LLM 生成暂不进入主实验；因此输出必须标为**精排子系统收益**，不能直接称为端到端 RAG 加速比。

使用相同候选集、相同向量表示和完整 MaxSim 语义比较主机与盘侧执行。不引入动态剪枝、提前终止、评分驱动的追加读取。由此可生成具有真实算子规模与访存依赖的合成 IR，而不必实际执行模型推理或全部点积。

### 参数可信度标记

| 标记 | 含义 |
|---|---|
| 公开依据 | 前序调研中的模型配置、官方文档或示例；示例值不等于生产均值 |
| 仿真假设 | 为探索收益边界选定的受控参数，不能称为真实生产统计 |
| 继承配置 | 从已有平台配置读取并固化，不允许 Agent 静默猜测 |
| 派生值 | 由上述参数计算出的数据量、计算量或资源服务时间 |

本文件沿用前序调研结果及其参考链接，未进行一次新的模型版本审计。开始实施时记录实际参考的模型 revision 和仿真器 commit，避免后续版本变化混入实验。

## 2. 主配置与模型对照

### 2.1 模型及检索参数

| 配置键 | 含义 | 主配置 | 候选扫描/对照 | 来源与约束 |
|---|---|---:|---|---|
| model_reference | 负载参照模型 | TomoroAI/tomoro-colqwen3-embed-4b | ColPali、ColQwen2.5 | 公开依据；仅借用表示形状 |
| embedding_dim | 向量维度 D | 320 | 对照模型为 128 | 公开配置 |
| document_vectors | 每文档页向量数 Nd | 1024 | 512、768、1280 | 仿真假设；固定长度不是均值 |
| query_vectors | 查询向量数 Nq | 32 | 16、64、128 | 仿真假设 |
| candidate_count | 去重后的精排候选数 C | 150 | 50、100、200；500 为压力测试 | 两路各 100 候选，假设重合 50；不是官方默认值 |
| top_k | 返回文档页数 K | 10 | 5、20 | 官方示例参照 |
| storage_dtype | 文档向量存储类型 | FP16 | FP32；压缩表示另设组 | 实验选择；不等同于模型推理 dtype |
| bytes_per_component | 每维字节数 b | 2 | FP32 为 4 | 与 storage_dtype 联动 |
| scoring | 评分语义 | 完整 MaxSim | 首轮固定 | 不使用动态剪枝 |
| rerank_hnsw_m | 精排字段的 HNSW 参数 | 0 | 首轮固定 | 仅说明应用背景；IR 不构建此索引 |

MaxSim 定义：

```text
score(q, d) = sum_i max_j dot(q[i], d[j])
V_query = C × Nd × D × b                  # 候选文档向量有效字节数
F_query = 2 × C × Nq × Nd × D             # 点积 FLOP 数
```

注意：

- 1280 是该模型配置中的视觉 token 上限；最终保存的总向量数还可能包含模板文本 token。1024 是用于隔离变量的近似长度假设。
- 首轮查询矩阵也使用 2 Byte/维。累加精度与数值误差不在本轮性能模型内；真实实现仍需核验数值一致性。
- 候选数 50、500 是敏感性/压力情景；若实际采用“两路各 100 且返回满额”的候选生成流程，并集范围只能是 100～200。
- 不为 Nd 指定未经测量的概率分布。第一轮各点固定长度；后续有真实统计后再加入变长分布。
- 额外模板 token 可用 +0、+16、+64 的长度增量做敏感性分析，后两者是新补充的实验假设，不能解释为模型的固定模板长度。

### 2.2 模型对照与派生量

以下统一使用 C=150、Nq=32、FP16：

| 负载配置 | D | Nd | 每文档页有效字节数 | 每查询候选向量量 | 点积计算量 |
|---|---:|---:|---:|---:|---:|
| ColQwen3 主配置 | 320 | 1024 | 655360 B（640 KiB） | 98.304 MB | 3.145728 GFLOP |
| ColQwen2.5 对照 | 128 | 755 | 193280 B | 28.992 MB | 0.927744 GFLOP |
| ColPali 对照 | 128 | 1030 | 263680 B | 39.552 MB | 1.265664 GFLOP |

ColQwen2.5 的 755 来自模型卡示例，不是数据集平均值；ColPali 的 1030 来自前序 Qdrant 示例说明。切换模型对照时同时切换 D 与 Nd，不要只改变其中一项。

## 3. 数据库、访问分布与缓存

| 配置键 | 含义 | 主配置 | 扫描范围 | 说明 |
|---|---|---|---|---|
| corpus_documents | 文档页总数 | 10000 | 100000 | E4 以 10000 页为基线，扫描上限为 100000 页 |
| host_capacity_fraction | 主机向量缓存占语料有效向量字节比例 | 5.24288% | 0%、1.31072%、20.97152% | 不包含初召回索引和对齐填充 |
| cache_policy | 缓存策略 | 按字节容量的文档级 LRU | 首轮固定 | OS 页缓存的简化，并非复现 |
| access_distribution | 文档热度 | Zipf | 均匀、Zipf | 合成分布 |
| zipf_alpha | Zipf 指数 | 0.8 | 0（均匀）、1.2 | 非生产拟合结果 |
| candidate_uniqueness | 查询内去重 | 开启 | 固定 | 每查询恰好 C 个不同 ID |
| inter_query_sampling | 查询间关系 | 独立采样 | 后续增加主题聚集 | 不能据此代表真实查询相关性 |
| rank_to_document | 热度排名到文档 ID | 固定随机置换 | 不同随机种子 | 避免热点与逻辑连续地址绑定 |
| device_extra_cache_bytes | 盘侧额外向量缓存 | 0 | 后续同容量缓存/HBF | 暂存 buffer 不等于缓存 |

默认一万页语料包含 6.5536 GB（约 6.10 GiB）有效向量。生成器用语料有效向量字节数乘以覆盖比例，向下取整到字节。一万页四档为 0、85,899,345、343,597,383、1,374,389,534 B（约 0、82、328、1,311 MiB），主档可容纳 524 个整页。均匀独立访问时可用覆盖比例核对稳态命中率的数量级。Zipf、查询内去重和有限预热会改变实际命中率。

同样的比例在百万页 FP16 参考语料上对应原来的 0、8、32、128 GiB。[本机一万页 E0 配置](../workloads/maxsim/local-10k.json)使用 8 GiB raw 的模拟盘、单查询和零缓存。[百万页 E0.4 配置](../workloads/maxsim/e0.4.json)作为独立参考，使用 1 TiB raw 的模拟盘和零缓存核验冷读路径。

生成器约定：

1. 使用 p(r) ∝ r^(-alpha) 的热度分布。
2. 查询内采用顺序加权无放回抽样；可通过重复抽样后拒绝重复 ID 实现，但必须记录算法。
3. 独立使用 layout_seed、candidate_seed、arrival_seed；建议首轮使用 3 组种子（11、23、47）。同一组方案共享候选序列、布局和到达序列。
4. 实际命中率由访问序列和缓存容量产生，记录按有效字节加权的命中率。固定命中率仅用于解析敏感性分析，不能与容量驱动 LRU 混为同一实验。
5. 主机基线在缓存 miss 时读取文档，读完后加入 LRU。并发重复 miss 首轮不做跨查询请求合并；所有方案均按相同约定计数，后续可增加合并对照。
6. 冷缓存与预热稳态分别报告。可在不推进计时的缓存重放阶段每 10000 次查询检查一次命中率，连续 3 个窗口差异小于 1 个百分点作为初始预热判据；同时检查占用稳定性。该判据是执行建议，未满足时报告未收敛并延长，不能默认为稳态。

## 4. 执行方案：必须明确缓存如何影响下沉

首轮至少比较以下三种方案：

| 方案 ID | 缓存命中路径 | 缓存未命中路径 | 主要目的 |
|---|---|---|---|
| host | 从主机缓存读取并在主机评分 | SSD 读取→传到主机→评分→填入缓存 | 合理主机基线 |
| host_compute_free | 同 host，但评分时间置零 | 同 host，但评分时间置零 | 隔离存储/搬运瓶颈 |
| device_all | 不使用主机文档向量缓存 | 所有候选在盘侧读取与评分，仅返回分数 | 检验全部下沉及高主机命中率下的退化 |

可增加第四组 hybrid_miss_only：主机缓存命中的文档在主机评分，未命中的文档在盘侧评分，再合并分数。**此方案不能默认持续享有 host 基线的稳态命中率**：盘侧只返回分数时，未命中文档不会自动进入主机向量缓存。

若实施 hybrid_miss_only，必须显式选择并标记缓存更新方式：

- 首轮建议“相同预热快照起点，命中更新 LRU，miss 不回填向量”，独立演化并报告实际命中率。
- 如采用向量回传填充缓存，必须计入向量链路流量，不能同时声称零向量回传。
- 用外部理想命中掩码比较资源上界时，标为解析/理想对照，不作为真实缓存策略结果。

本节补足了前序参数表未指定的路由细节。device_all 是本文件的主下沉方案；hybrid_miss_only 是可选扩展，二者结果不得混用。

## 5. 逻辑布局、I/O 与执行调度

| 配置键 | 主配置 | 扫描范围 | 语义 |
|---|---:|---|---|
| document_alignment_bytes | 4096 | 首轮固定 | 文档起点对齐 |
| logical_read_bytes | 65536（64 KiB） | 4、16、128 KiB | 主机逻辑请求大小，不是 NAND Page |
| read_window_documents_per_query | 16 | 4、64 | 每查询最多同时有多少候选处于读取阶段 |
| global_outstanding_reads | 256 | 64、1024 | 整个设备全局未完成逻辑请求上限 |
| compute_batch_documents | 8 | 1、16 | 单个评分任务最多包含的完整就绪文档数 |
| cross_query_compute_batching | false | 后续 true | 首轮不跨查询合批 |
| input_buffer_bytes | 32 MiB | 8、128 MiB | 每个执行侧的显式暂存总预算 |
| overlap_read_compute | true | 可增加 false 诊断组 | 主机与盘侧采用相同重叠能力假设 |

执行约定：

- 每文档的向量逻辑连续；分配跨度为 align_up(有效字节数, 4 KiB)。请求按 logical_read_bytes 拆分，尾部按 4 KiB 补齐，填充字节不参与点积。
- 逻辑地址到 NAND 物理位置的转换由后端 FTL/布局模型处理；逻辑连续不保证物理连续。
- 主配置每文档 10 个 64 KiB 请求；完全未命中时每查询 1500 个逻辑读取请求。
- 保留候选 ID 顺序，首轮不额外按 LBA 排序。跨查询请求仲裁采用轮转作为建议默认，并记录实际实现。
- 计算引擎空闲时立即取同一查询已就绪的 1～8 个文档，不等待凑满 8 个。必须统计实际平均批大小。
- 文档全部就绪后才能进入本轮评分批次；首轮不做文档内的读算流式融合。可并行读取后续文档。
- 每侧暂存预算按正在读取和等待/执行评分的有效输入统一记账；启动读取前预留文档空间，评分完成后释放。缓存命中也应遵守明确的输入占用规则。
- 主机和盘侧各一个抽象评分引擎；其有效吞吐已代表该侧汇总能力。多引擎实验必须另行声明。
- 输入暂存不足、读窗口耗尽或全局队列满时必须反压。不能默认无限 buffer。

## 6. 硬件、cost model 与结果传输

| 参数 | 主配置/处理方式 | 扫描或对照 | 来源 |
|---|---|---|---|
| NAND 几何、时序、FTL | 继承现有 MQSim 配置 | 硬件架构扩展另设组 | 继承配置 |
| 内部有效读取带宽 | 由 MQSim 输出 | 100 GB/s 仅为目标架构/解析情景 | 不能作为无条件常数 |
| SSD—主机有效链路带宽 | 7 GB/s | 14、28 GB/s | 仿真假设 |
| host_effective_tflops | 1 | 0.25、4、16；另有计算趋零组 | 等效评分吞吐 |
| device_effective_tflops | 1 | 0.25、4、16 | 等效评分吞吐 |
| batch_launch_us | 每批 10 μs | 0、50 μs | 两侧相同的初始假设 |
| offload_command_us | 每查询 20 μs | 0、100 μs | 下沉命令的额外固定开销 |
| HBF | 关闭 | 后续独立容量/带宽实验 | 不混入主配置 |
| 读介质类型 | 现有 NAND 后端 | 新介质必须另建硬件 profile | 不仅修改一个带宽数字 |

等效计算时间：

```text
T_batch = batch_launch_us + F_batch / P_effective
```

P_effective 使用点积 FLOP 归一化整个 MaxSim 内核耗时，**包含归约影响**，不是峰值算力。若已有平台将矩阵乘法与 max/sum 分开建模，应改用分项模式并关闭等效内核耗时，不能双重计算。

主配置评分计算量与文档字节数之比约为 32 FLOP/Byte。在 100 GB/s 实际输入流量下，评分内核需要约 3.2 TFLOP/s 等效吞吐，另有启动与命令开销。1 TFLOP/s 主配置显然不能无条件消费 100 GB/s 输入，这是待观察的算力瓶颈。

补充协议假设（用于使 IR 可执行，不声称等同于 Qdrant 协议）：

| 项目 | 默认值 |
|---|---|
| Query 数据类型 | FP16 |
| 文档 ID | 8 Byte |
| 返回分数 | FP32，4 Byte |
| 下沉返回策略 | 返回全部 C 个候选的 ID+分数，在主机执行同一 Top-K 汇总 |
| 每查询下发有效负载 | Nq×D×2 + C×8；主配置 21680 B |
| 每查询返回有效负载 | C×(8+4)；主配置 1800 B |

命令固定开销单独计入；上述字节量未包含协议头。可以使用有效带宽吸收协议效率，但不能再重复扣减。首轮不声称模拟了完整 NVMe 命令栈。盘侧局部 Top-K 可作为后续扩展。

主机 DRAM 读取、内部总线/DRAM、Top-K 及公共软件开销：优先复用现有平台模型并记录。若当前缺少，应显式记为零开销/无限带宽上界假设，而不能声称为真实硬件预测。尤其要记录等效计算吞吐是否已包含本地内存开销，避免漏计或重复计入。Top-K 若未建模，主机与盘侧均采用相同的零开销假设。

**启动前必须核验：**

1. MQSim 逻辑容量必须容纳完整语料及对齐开销，每个文档使用独立地址范围。
2. 记录原始容量、逻辑容量和 OP，以及 channel/die/plane/page、读时延、总线带宽等配置。
3. 下沉路径仍经过真实介质读取、内部总线和缓冲；仅取消不需要的文档向量对外传输。
4. 如 MQSim 已包含 PCIe/主机传输时间，语义 IR 桥接层不得重复添加；明确资源与时间由哪层负责。
5. 100 GB/s 必须由具体通道、介质、队列和内部路径配置支持；否则仅绘制解析参考线。

## 7. 到达模型、并发与扫描顺序

### 7.1 两类实验分开运行

| 类型 | 主配置 | 扫描 | 用途 |
|---|---|---|---|
| 封闭循环 | 16 个在途查询 | 1、4、64 | 测延迟与饱和吞吐；一个完成后补一个 |
| 开放到达 | 泊松到达，100 QPS | 10、30、300、1000 QPS | 测入口排队、稳定区间和尾延迟 |

这些是负载探测点，不是业务流量预测。在饱和区间加密扫描；若仍未饱和则向上扩展。开放模式若有准入上限，统计入口等待，不能只报告进入设备后的时间。

若不满足稳定性，报告积压增长、到达/完成数及过载状态，不将短窗口的完成请求延迟当成稳态 P99。各方案用相同到达序列比较固定压力延迟，再分别测可持续吞吐。

### 7.2 推荐实验批次

| 批次 | 变量 | 固定项/目标 |
|---|---|---|
| E0 正确性与数量级 | 缓存 0、单查询；host/device_all | 校验请求数、字节数、FLOP、依赖及反压 |
| E1 主收益边界 | 缓存覆盖率 0%/1.31072%/5.24288%/20.97152% × 均匀/Zipf 0.8/Zipf 1.2 × 封闭并发 1/4/16/64 | 按每点的有效向量字节数换算容量 |
| E2 到达压力 | 开放 QPS 10/30/100/300/1000，临界点加密 | 选 E1 中有收益、临界、无收益场景 |
| E3 带宽与算力 | 外部 7/14/28 GB/s；主机与盘侧算力分别 0.25/1/4/16 | 先单因素扫描，再做临界区域组合 |
| E4 表示与候选 | 两种对照模型、C、Nq、Nd、FP32 及 1 万页与 10 万页语料对照 | 各维度单独扫描，10 万页为语料上限 |
| E5 执行敏感性 | I/O 粒度、窗口、QD、buffer、批次和固定开销 | 检查调度/资源限制 |
| E6 可选扩展 | hybrid、主题聚集、压缩、HBF、新介质 | 各自独立定义，不能混入主结论 |

不运行全部维度的笛卡尔积。device_all 不使用主机向量缓存，因此纯 device_all 的重复缓存容量点可以复用同一结果；报告中仍要保留与不同 host 基线的配对关系。

建议测量阶段至少完成 10000 次查询，每个关键点 3 个种子；这是初始运行预算，不保证 P99 精度。尾延迟变化较大时延长样本量并报告不确定性。预热不计入主测量窗口，冷启动结果单列。仿真过载场景设置显式事件/时间预算，记录未完成请求，不静默丢弃。

## 8. 参考配置骨架

以下 YAML 是交接配置示意，**不是现有平台 API 的既定字段**。实施 Agent 应映射到项目实际 schema。null 项需要从项目读取；无法解析时应在运行前明确列出缺项，不静默填成某个“典型 SSD”。

```yaml
experiment:
  name: colqwen3_maxsim_nmc_v01
  scope: reranking_only
  schemes: [host, host_compute_free, device_all]
  seed_set: [11, 23, 47]
  measured_queries_initial: 10000

workload:
  model_reference: TomoroAI/tomoro-colqwen3-embed-4b
  embedding_dim: 320
  document_vectors: 1024
  query_vectors: 32
  candidate_count: 150
  top_k: 10
  storage_dtype: fp16
  bytes_per_component: 2
  scoring: full_maxsim
  corpus_documents: 10000
  distribution: zipf
  zipf_alpha: 0.8
  unique_candidates: true
  randomize_rank_to_document: true

cache:
  host_capacity_fraction: 0.0524288
  policy: document_lru_bytes
  device_extra_capacity_bytes: 0
  host_fill_on_miss: true
  cross_query_miss_coalescing: false

layout:
  contiguous_document: true
  alignment_bytes: 4096
  logical_read_bytes: 65536
  sort_candidates_by_lba: false

execution:
  read_window_documents_per_query: 16
  global_outstanding_reads: 256
  compute_batch_documents: 8
  wait_for_full_batch: false
  cross_query_compute_batching: false
  input_buffer_bytes_per_side: 33554432
  overlap_read_compute: true
  scoring_engines_per_side: 1

compute:
  mode: equivalent_full_maxsim
  host_effective_tflops: 1.0
  device_effective_tflops: 1.0
  batch_launch_us: 10
  offload_command_us: 20
  host_compute_free_zeros_launch: true

protocol:
  query_component_bytes: 2
  document_id_bytes: 8
  score_bytes: 4
  return_mode: all_candidate_scores

hardware:
  mqsim_config_path: null
  mqsim_config_hash: null
  backend_commit: null
  external_effective_GBps: 7
  internal_bandwidth_mode: simulated
  target_internal_GBps_reference_only: 100
  hbf_enabled: false
  local_memory_cost_model: inherit_or_explicit_upper_bound
  topk_cost_model: inherit_or_explicit_zero

load:
  mode: closed_loop
  closed_loop_concurrency: 16
  open_loop_arrival: poisson
  open_loop_qps: 100  # 仅 mode=open_loop 时启用
```

## 9. 输出指标、核验与结论口径

### 必须输出的指标

| 类别 | 指标 |
|---|---|
| 性能 | 完成 QPS、平均/P50/P95/P99 精排延迟、入口等待、过载/积压 |
| 访问 | 实际候选数与长度、按字节命中率、逻辑请求数、有效读取字节 |
| 数据路径 | 实际 NAND 读取字节、内部搬运字节、SSD→主机/主机→SSD 字节、读取放大 |
| 计算 | host/device FLOP、等效评分时间、启动次数、实际批大小、引擎忙碌比例 |
| 资源 | 通道/Die/Plane 利用与排队、buffer 高水位、反压时间、实际 QD |
| 对照 | 同到达压力下延迟比、分别稳定运行时吞吐比、瓶颈归因 |
| 可复现性 | 全量展开配置、种子、代码 commit、后端配置哈希、预热和测量窗口 |

若某项后端暂不支持，明确标为不可观测，不用估算值伪装实测。

### 主配置数量级核验

| 项目 | 理论参考 |
|---|---:|
| 单查询有效文档向量量 | 98.304 MB |
| 单查询点积计算量 | 3.145728 GFLOP |
| 完全未命中时逻辑读取请求 | 1500 个 |
| 7 GB/s 链路传输资源服务时间 | 14.043 ms |
| 14 GB/s 链路传输资源服务时间 | 7.022 ms |
| 100 GB/s 内部读取参考服务时间 | 0.983 ms |
| 1 TFLOP/s 等效评分服务时间 | 3.146 ms，未计批启动 |

上述服务时间既不能直接相加当成流水线总延迟，也不能把最大值当成有限窗口下的准确查询延迟。由 IR 依赖、资源仲裁和实际重叠决定最终时间。

吞吐解析上界用于定位瓶颈：

```text
QPS_host <= min(
  B_internal / V_internal_host,
  B_external / V_external_host,
  P_host / F_query
)

QPS_device_all <= min(
  B_internal / V_internal_device,
  B_external / V_query_and_results,
  P_device / F_query
)
```

其中读取量必须反映各自缓存策略和读取放大；带宽应使用相应访问模式下的有效能力。该简式省略启动、IOPS、队列、内部共享带宽等约束，不能替代仿真。

### 结论应回答

1. 主机缓存覆盖、候选规模与局部性变化时，哪些区域有收益、无收益或退化？
2. 收益来自减少外部搬运、补充算力，还是额外缓存容量？
3. 单查询延迟收益与饱和吞吐收益是否一致？
4. 为达到目标收益，需要多少盘侧有效算力；何时再加算力无效？
5. NAND/内部资源已经饱和时，下沉为什么不能继续提升吞吐？
6. 假设改变后结论是否仍成立，尤其是较小模型表示和合理主机加速基线？

不得把合成分布称为工业统计；不得在没有质量实验时宣称压缩表示保持同等 Recall/NDCG。对同一表示和相同候选集的执行位置比较，可先研究性能边界。没有业务 SLO 时报告 QPS–延迟曲线，不擅自命名“满足生产要求的 QPS”。

## 10. 实施 Agent 交付清单

- 可复用的参数化候选序列与布局生成器，不要求创建数百 GB 的真实向量文件；性能模式可用元数据和逻辑地址描述，但仿真逻辑容量必须一致。
- 将 host、host_compute_free、device_all 三种路径映射到现有语义 IR，保留请求依赖、批处理、资源上限与反压。
- 一份固化的主配置及按 E0～E5 分阶段运行的扫描清单；无需第一轮实现 E6。
- 字节数、请求数、FLOP 守恒核验，以及缓存 0、全命中、单查询、有限 buffer 等边界检查。
- 结果表与收益边界图，附所有简化假设、缺失模型和不可观测指标。
- 不引入未经说明的“典型 NAND 时延”“真实缓存命中率”或“DaVinci 利用率”。

## 11. 前序调研参考资料

以下链接是参数来源追溯入口；本文件只将明确的模型形状/官方示例作为依据，访问分布和硬件性能仍是仿真假设。

1. [Tomoro ColQwen3-4B 配置](https://huggingface.co/TomoroAI/tomoro-colqwen3-embed-4b/blob/main/config.json)：320 维表示及视觉 token 限制。
2. [Tomoro ColQwen3 处理器](https://huggingface.co/TomoroAI/tomoro-colqwen3-embed-4b/blob/main/processing_colqwen3.py)：图像与模板文本处理。
3. [vLLM specific pooling models](https://docs.vllm.ai/en/latest/models/pooling_models/specific_models/)：模型支持背景。
4. [Qdrant PDF retrieval at scale](https://qdrant.tech/documentation/tutorials-search-engineering/pdf-retrieval-at-scale/)：两路候选、多向量精排与 Top-K 示例。
5. [Qdrant Vectors](https://qdrant.tech/documentation/manage-data/vectors/)：多向量、MaxSim 和存储类型。
6. [ColQwen2.5-v0.2 模型卡](https://huggingface.co/vidore/colqwen2.5-v0.2)：755×128 文档表示示例。
7. [Qdrant PDF retrieval 原教程路径](https://qdrant.tech/documentation/advanced-tutorials/pdf-retrieval-at-scale/)：前序 ColPali 1030 向量示例入口，可能重定向。
8. [MQSim](https://github.com/CMU-SAFARI/MQSim)：SSD 几何、时序和队列建模配置。

