# 图搜索与数据格式

使用[统一运行入口](../README.md)与 `configs/experiments/`；两放置保持同一搜索语义。默认 N=2048、d=32、R=16、Q=32、seed=42、k=10、L=32、B=1、E=64、chunk=16、max_inflight=1。默认图用于闭环验证，不保证高 Recall。

## 搜索语义与可见性

`src/search/graph.{h,cc}` 为回调状态机，只接收 read 能力、异步运行时和主机模型，不持有 ContentStore 或 MQSim 对象。其语义是：

1. 评分固定入口，等其主机得分完成。
2. 将已评分集合按 `(FP64 distance, uint32 ID)` 排序，候选取前 L。
3. 在候选内按顺序选至多 B 个未扩展节点，且累计扩展数不超过 E；选择本身作为主机控制服务，在结果可见后标记 expanded 并提交邻接读取。
4. 整批邻接到达后，在主机控制任务内按 ID 去重、排除所有已经评分的邻居；按升序 ID 分为至多 chunk 个向量的评分块。
5. 主机模式经普通读取与 HostCpu 评分；盘控模式经本地读取、独立缓冲、Controller 计算及共享 PCIe 返回。整批得分在主机可见后，运行候选合并控制服务，再进入下一轮。
6. 无可扩展候选或达到预算时，返回所有已评分节点中的 top-k，数量不足则标记 insufficient。

visited 为所有已提交评分的 ID，expanded 为已选择扩展的 ID；环、重复边与跨批重复邻居均不会重复评分。为使全体已评分 top-k 与候选宽度独立，保留已评分得分，候选仅由其中最好的 L 项构成。没有向量/邻接跨批或跨查询主机缓存。即使 k=L，终止也取决于实际结果与预算，未预先生成完整访问序列。

FP32 输入，平方 L2 使用固定维度顺序的 FP64 减/乘/加，沿用 M2.3 的禁用 fast-math/FMA 规则。整批等待使请求完成先后不改变搜索语义；同分严格按 ID 决定。主机距离工作量沿用 M2.3；排序/合并由 candidates 算子计量。选择阶段每个前 L 项记一次 candidate_check；邻接阶段每个边 ID 记一次 check，每次显式已评分 ID 相等判断、插入位置比较及最终去重相等判断记一次 comparison。set 查找、序列化/复制、研究日志等不再另计机器指令；check 是含集合维护的综合成本抽象。上述服务共享 HostCpu slots，公式仍为 setup + ceil(work/rate)，全部参数为 configured_unvalidated。

`src/sst/graph_host.cc` 与 `src/backend/ordinary_device.h` 连接真实 SST SelfLink/EventBridge 和 MQSim ExternalFlow。测量前按连续页地址写入整个布局并排空后端，记录 origin；MQSim 时间不重置。每个应用对象读取通过 Layout/ReadAssociation 绑定实际 request ID/LBA/sector 数。DMA 与 CQ 完成经 ExternalFlow 校验，全部 extents 完成后才发布内容驻留、启动 read 的运行时完成，再让依赖的距离/控制任务前进。功能内容由不可变 ContentStore 保存；MQSim 不存真实字节，此语义沿用 M2.1，不将物理地址当成内容身份。

普通存储、PCIe 数据和队列开销由 MQSim 唯一计费，没有平台链路延迟叠加；query 初始 HostMemory，候选、visited、expanded 和得分留在主机。读取功能字节、sector 字节分别统计。向量与邻接布局独立按 512 B 对齐，C++ 使用 M2.1 ann_layout，运行器保存 layout.json；跨 sector/page 原语仍有既有回归，本任务默认数据不跨页。盘控分块与资源生命周期见[盘控说明](controller.md)。

## 数据、真值与输出

二进制均 little-endian。vectors.f32 为 N×d 个 IEEE FP32；queries.f32 为 Q×d 个 FP32；adjacency.u32 为 N 条固定 R+1 个 uint32，首项 degree，后续 degree 个 ID 有效，其余为零 padding。ContentStore 中邻接仅保留有效 `4+4*degree` 字节。dataset.json 记录格式、N/d/R/Q、seed、生成器与三个文件 SHA256。

默认生成器使用 Python Random(seed=42) 的整数网格值除以 128，值可由 FP32 精确表示；每个节点有固定有向环边，再加入确定性 PRNG 邻居并排序。该图用于测试平台闭环，没有建立高质量 ANNS 索引的目标。

`tools/ann_data.py truth` 独立解码 FP32，以 Python binary64 按维度顺序穷举每个查询与每个向量的距离，按距离/ID 排序取 k；不调用 C++ 算子或近似搜索。truth.json 绑定向量与查询哈希和 k，包含每查询精确 ID/距离。Recall@k 为每查询 `|返回 ID ∩ 精确 top-k ID| / k` 的均值，不足 k 仍以 k 为分母；同分按 ID 决定精确集合，不采用距离阈值扩张集合。

结果、可选事件日志、并发与时延口径见[输出说明](output.md)。
