# V0.1 架构与设计决定

平台为单进程、单 SST Component、单 rank/线程、单 SSD；每次实验新进程。主机控制简化图搜索，真实平方 L2 结果决定后续邻接和向量访问。当前运行默认值见[搜索](search.md)，范围外能力及问题见[限制](model-limitations.md)。这里描述现行结构；原源码调研和阶段取舍见[历史规划](archive/v0.1/implementation-plan.md)。

## 模块与接口

| 模块 | 责任与源码 |
| --- | --- |
| SST 接线 | [graph_host.cc](../src/sst/graph_host.cc) 连接配置、查询接纳、搜索、资源和结果输出；Python 运行器准备输入，不计算模拟性能 |
| 时间桥 | [event_bridge.h](../src/backend/event_bridge.h) 在 SST now 驱动 MQSim 当前时间片；稳定四 phase/delta、generation 与停滞诊断 |
| 内容与布局 | [content.h](../src/content/content.h) 保存不可变真实字节、对象到 LBA extent、位置句柄及请求关联；512 B sector 对齐，跨页交后端拆分；PPA 仅观测 |
| 只读后端 | [ordinary_device.h](../src/backend/ordinary_device.h)、[external_flow.h](../src/backend/external_flow.h) 管理普通 NVMe/DMA/CQ；[local_read.h](../src/backend/local_read.h) 管理本地读请求/事务汇总 |
| 可见性运行时 | [runtime.h](../src/runtime/runtime.h) 私有功能结果、服务/返回门控与异步 continuation；算法不接触 ContentStore 原始字节 |
| 算法与主机成本 | [graph.h](../src/search/graph.h)、[operators.h](../src/operators/operators.h)、[host_cpu.h](../src/model/host_cpu.h)：固定批次图搜索、真实 L2 和候选比较、共享 FIFO/slots |
| 传输与盘控 | [messages.h](../src/transport/messages.h)、[pcie.h](../src/transport/pcie.h)、[controller.h](../src/model/controller.h)：显式消息编码、共享 MQSim 链路、独立缓冲 credit 与评分服务 |
| 观测与结果 | [observation.h](../src/backend/observation.h)、[experiment_output.py](../tools/experiment_output.py)：被动事件、区间并集、能力声明和 JSON/CSV；统计不依赖可选日志 |

## 保留的决定与原因

- SST 是唯一时间权威。MQSim 队列保留原有事件顺序，只在 SST 到达后推进；等待使用 continuation，避免一个查询阻塞其他查询。Backend、ResourceCompleteRelease、Publish、ContinueSubmit 四 phase 与 delta 回绕避免人工增加 1 ns。
- 内容、地址和驻留分开：宿主持有真实数据不授予模拟算法免费读取权。普通读等所有 extents 的数据及 CQ 完成；本地读等请求全部事务/cache 服务与安全清理，再做 staging。结果在服务和必要传输完成前不可见。
- MQSim 唯一计费 NAND/FTL/ONFI/cache/CMT/PCIe；平台只新增主机计算/控制及盘控计算/独立缓冲。主机距离 rate 包含加载，盘控 rate 仅含算术；不得重复收相同资源的费用。
- 主机模式向量走普通完整回传；盘控模式向量走本地读、缓冲与 L2，再返回得分。query、IDs、邻接、ACK、控制消息和普通 SQ/CQ/doorbell 均计费，不能用“向量零 D2H”代替完整流量账本。
- 固定语义批次、确定性 chunk、FP32 输入/固定顺序 FP64 累加及距离/ID 排序，使到达顺序不改变算法语义。容差 abs 1e-9 + rel 1e-7 只用于核对，不用于同分判定。
- 计算采用非流水 FIFO/slots 和 setup + ceil(work/rate)。L2 工作量 3bd FLOP；控制工作量为 checks×check_units + comparisons×comparison_units。输入合法性和整数溢出必须检查；参数均未校准。原生 PCIe 取整保留，不统一改成新服务的 ceil。
- 确定性预装载写入与只读测量分开，预装载排空后记录 origin，MQSim 时间不重置。仅 SIMPLE/TURNED_OFF 数据缓存配置经过限定验收，保留 CMT；不宣称稳态真实 SSD 或任意缓存兼容。
- 盘控 query 常驻与工作块 credit 分开，固定分块并保留至少一个最大工作块的前进空间；细节和公式见[盘控资源](controller.md)。接纳/时延/Close 排空定义见[输出](output.md)。

## 已替代或尚未承诺的方案

- 构建时 MQSim patch 栈已被原位子模块开发取代；原生 oracle 仍从锁定原生 commit 独立导出。维护身份见[源码约定](mqsim-source-management.md)。
- 先跑 M0.2 才能构建平台的流程已被根 CMake 自动准备锁定 SST、共享缓存的流程取代。M0.2 工具仍承担原生 oracle，不是平台构建前置。
- 历史 PoC/Planned 能力表已被实际接口、当前 capabilities 输出与[模型限制](model-limitations.md)取代。语义 read/invoke 表不是稳定插件 ABI；扩展应使用[实际 C++ 入口](extensions.md)。
- 历史顺序主机图示例不是当前全部支持范围：已接入主机/盘控、有限并发与可选日志。旧运行器保留用于回归，日常入口见[README](../README.md)。
- 完整 DiskANN、多 SSD、自主盘内搜索、共享 DRAM 精细竞争、任意在途取消恢复仍未承诺。V0.2 增量范围和执行顺序见 [PRD](prd-v0.2.md) 与 [实施计划](v0.2-implementation-plan.md)；本页继续描述现行实现，新设计经验证后再同步，不能将历史演进建议自动转为需求。
