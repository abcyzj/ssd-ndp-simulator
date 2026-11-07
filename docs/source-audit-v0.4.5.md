# V0.4.5 源码核查与迁移依据

日期：2026-11-05（Asia/Shanghai）。依据 [PRD](prd-v0.4.5.md)，对应[实施计划](implementation-plan-v0.4.5.md)。本轮为定向静态源码核查；没有配置／编译、执行 CTest、运行原生基准、采集全量 IR 或开展性能验收。以下“可复用”是结构判断，不代表改造后已验证。

## 1. 核查身份和证据

- 主仓 HEAD：`8cf48fc6f3f3bb2a54edd2cea9eeb6fdfb40d6ba`。开始时已有 README、AGENTS、文档索引修改及未跟踪的新 PRD；均为前序需求文档，未覆盖。
- MQSim fork：`8f7ddf6d8cafdb7c783b3fcfd4693f15c6c5854b`；独立原生基线：`51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16`。
- SST Core：`ffd71527bc26a1f2a4fb685fdeb2ca0799997d78`。两个子模块状态干净，本轮未修改。
- MQSim 相对原生 diff SHA256：`f38fc06069ee252ebbf83cdc2e566768d74a255fbb2ff1980ba76b0f2a7ec044`。
- 主仓执行相关文件清单摘要：`59baa98e16c530f314e06c54b018788b64e358932eae4c1c10b2336747be3473`。
- 本机证据：`results/v0.4.5/planning-20261105/run1/`，含 `snapshot.json`、`commands.json`、18 条可重放身份／定向查询的 stdout、stderr、退出码和 `test-tool-inventory.json`。快照 SHA256：`fadc430254a009e50988c8850319af0d1564abdc67e72dd50ff3bc0db178e44b`。

清单覆盖主仓已跟踪及未忽略未跟踪的 src/tools/tests/configs/schemas/examples 普通文件和根 CMake／锁文件／gitmodules；摘要为相对路径到文件 SHA256 的排序、无空白 JSON 的 SHA256。子模块分别记录实际逐文件 hash。文档不混入执行源码摘要；最终文档检查另记，不改变初始快照。此前交付的通过数字不作为本轮运行证据。

## 2. 源码发现与实施影响

| ID | 核查结论及源码定位 | 对 V0.4.5 的影响 |
| --- | --- | --- |
| SA-01 | [根 CMake](../CMakeLists.txt)第 23～74 行在 SST 准备前建立 content → operators → algorithm → native，并以 native-only return 分流；native 的源码身份包含根 CMake。 | 已有无模拟依赖基础，但不是独立子工程。拆出功能模块、hash／公共类型和显式 SDK 依赖；新构建身份必然变化，不能沿用旧 Profile 身份。 |
| SA-02 | [原生 Recorder](../workloads/graph-search/src/search/native_observation.h)的 `record` 写操作、IDs、count、size、摘要与七项 Work；[封包器](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/tools/native_observation.py)的 `graph` 按图搜索语法补依赖；[契约校验器](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/tools/v04_contract.py)的 `QueryPreflight.verify` 固定 INIT／SCORE／SELECT 循环。 | 当前采集 SDK 并不通用。需要显式提交依赖／端口的 SDK，图搜索适配器保留语义组织责任，通用包层不推测业务依赖。 |
| SA-03 | [Replay](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/src/search/replay.cc)的 `service`、`score_chunk`、`advance` 仍调用 L2、merge、select、dedup；其 [头文件](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/src/search/replay.h)持有 rank、scored、expanded、真实向量和邻接。 | 不能通过跳过 verify 或给 read 返回空数组实现 timing-only。应新增通用静态执行器，迁移事件与资源边界，最终删除旧 Replay。 |
| SA-04 | [Runtime](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/src/runtime/runtime.h)的 Operation 必须提供 evaluate；[实现](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/src/runtime/runtime.cc) `start` 产生 Bytes，返回大小取 `output->size()`，可见性和引用回收围绕结果对象。 | 抽出完成令牌、输出描述／字节数与状态机；返回流量不得依赖伪 payload 分配。保留 phase、服务／返回门控、引用与失败排空语义。 |
| SA-05 | [HostCpu](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/src/model/host_cpu.cc)在 evaluate 后取得 Work 并检查输出字节，再估计 Profile；[CpuStage](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/src/model/cpu_profile.h)的特征回调接收真实输入与 Result。 | 成本估计要改为消费采集的阶段特征和 Work；FIFO/slots 可复用但不可继续隐式执行功能算子。 |
| SA-06 | [CPU Profile](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/src/model/cpu_profile.cc)硬编码图搜索 op/stage、DEDUP 的 GBDT 位置、merge_append 表及图维度；[Replay](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/src/search/replay.cc)额外派生 old_scores、scored、edges、分段键等。 | 新 IR 不能只有旧聚合 Work。须采全阶段调用、模型特征、稳定 chunk/segment 键及可分解工作量；通用模型算法与图搜索适用规则分离。 |
| SA-07 | [Content](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/src/content/content.h)中的 Kind 仅 Query/Vector/Adjacency，Layout 构造和成员依赖 ContentStore；[GraphHost](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/src/sst/graph_host.cc)加载／扫描 mmap 或 memory 内容后才建 Layout、Handles。 | 新建对象元数据目录及无内容 Layout；数据集导入属于原生侧。模拟侧不再通过 ContentStore 验证内容，仍校验对象范围、LBA、容量和访问长度。 |
| SA-08 | [Controller](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/src/model/controller.cc) `chunk` 通过 handles 取向量并调用 squared_l2；Session 保存查询字节；计算公式、buffer、local read、消息协议同处一类。 | 拆出 FIFO、credit、local-read 适配与通用执行计划；L2 特有的分块／大小／计算公式放到扩展。只拆 Host 会留下 Controller 的功能执行。 |
| SA-09 | [消息](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/src/transport/messages.h)直接包含 query_values、scores；[PCIe transport](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/src/transport/pcie.h)先 encode 完整字节，按 bytes.size 计费，再 decode 交付。 | 即便计算已删，传输仍有 payload 依赖。需要元数据消息及显式 payload/wire 大小规则，保持 MQSim PCIe 原有取整、方向、共享链路和完成事件。不能用零填充向量假装去 payload。 |
| SA-10 | [OrdinaryDevice](../src/backend/ordinary_device.h)读取生成 `profile.h` 中 device_profile；[生成器](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/tools/device_profiles.py)从 devices.json 和 XML 生成几何与嵌入 XML；[根 CMake](../CMakeLists.txt)配置期执行生成器。 | 外置 XML 涉及运行时解析、几何一致性、partition 和预装载；不仅是 CLI 新增一个文件参数。生命周期测试也使用嵌入基线，须同步改造。 |
| SA-11 | [EventBridge](../src/backend/event_bridge.h)已有 MQSim／PlatformOnly 两种模式和 Backend/Release/Publish/Continue 四 phase；[普通读](../src/backend/ordinary_device.h)、[本地读](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/src/backend/local_read.h)已有独立设备服务。 | 优先复用时间桥和请求完成机制，不创建第二套调度时钟。内存模式可保持不创建设备；编译链接中含 MQSim 不等于运行时发生 SSD 服务。 |
| SA-12 | [replay_input](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/tools/replay_input.py)检查真实文件路径／大小，validate_package 校验内容，并用当前执行宿主 sysctl 绑定 CPU；[run_graph](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/tools/run_graph.py)准备 dataset、truth 和搜索参数。 | 新入口完全去除 payload／truth 的运行前提；分清模拟器宿主环境、被建模目标硬件和测量来源。允许在另一宿主上重放目标 Profile，但不因此宣称该宿主获得校准。 |
| SA-13 | [拟合器](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/tools/fit_v04_cpu_profile.py) `read_training` 要求 V04-0、V04-2c 任务状态、固定 POINTS 和原路径；[merge 拟合](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/tools/fit_v04_merge_profile.py)模型算法可提取；[制表](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/tools/bind_v04_merge_append.py)依赖固定查询清单。 | 拆通用 Measurement／训练 manifest／算法与图搜索 recipe；不是单独移动现有脚本。保留质量、分组留出和来源检查，但去除历史阶段成功作为输入前置。 |
| SA-14 | [Validator](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/tools/validate_v04.py)从独立 oracle 重建 Work／阶段，比较 replay_verified 的真实值；[总入口](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/tools/verify_v04.py)固定历史 evidence-root、native binary、冻结 hash，调用旧回归。 | 独立 oracle 和公式应保留；验证分成采集语义、模拟调度账本及精度三层，新总入口不调用旧总验收。 |
| SA-15 | [旧 AC-08](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/tools/verify_acceptance.py)从锁定 commit git archive 后独立编译 [ordinary_native](../tests/ordinary_native.cc)，对 QD1/QD32/small-CMT 严格比较；夹具仍共用 [ordinary_fixture](../tests/ordinary_fixture.h)、ExternalFlow 和生成配置。 | 可提取原生导出／比较流程；必须明确共享输入驱动的信任边界，加入独立请求清单和初态／账本核验，不能把共同配置解析错误也自证通过。原生不支持平台自定义消息／local-read 扩展，不能让它验证全部下沉。 |
| SA-16 | [CTest 注册](../CMakeLists.txt)混有 event bridge、runtime、controller 核心测试、direct/动态 IR 比较，以及 v02/v03/v04 交付门控。 | 按断言迁移，不能按文件年代或名字一刀切。测试／工具完整初始清单已保存；后续删除前逐项给出去向和替代出口。 |

## 3. 关键设计决定

1. **新执行输入版本，不给旧包增加 timing-only 开关。** 旧包的 expected Work 和摘要并非完整模型输入，必须重新采集阶段特征与依赖；必要转换也须独立来源核验，不能仅更改格式标记。
2. **先建立元数据执行底座，再移除功能路径。** Runtime、HostCpu、Controller、transport、Layout 是一条完整依赖链；任一环节仍要求真实 Bytes 都会使“无 payload 运行”失效。
3. **阶段与语义操作分开。** 一个语义 SCORE 可能包含准备、多个 L2 chunk、merge_append；输出包要能支持一对多阶段和精确绑定表，不能强制一操作一服务或把聚合 Work 均摊到物理块。
4. **真正按依赖调度。** 当前 Replay 顺序消费固定图语法。新执行器须在查询内允许独立就绪节点运行，只在显式 ORDER/资源策略需要时串行；流式读取窗口不能新增隐式业务依赖。索引／依赖就绪策略与内存预算在契约阶段先验证。
5. **配置文件与模型身份分层。** 语义包、物理 Layout、设备参数、目标模型、执行宿主分别记录，修改 Layout 不改变逻辑对象身份；语义变化重采，适用条件变化重测／拒绝。
6. **MQSim 保留状态，不保留业务 payload。** 维持页预装载、FTL/CMT/缓存初态、CQ/doorbell 与排空；数据字节应通过逻辑长度参与计费，而非实际内容参与算法。

## 4. 尚不能由静态核查证明的事项

- 新元数据令牌和 size-only 消息能否完全保持同刻仲裁顺序，需手算夹具和后端对拍。
- 当前采集覆盖新增阶段特征的完整性与额外开销，需小规模 pilot、独立 Work 核对和 A/B/A。
- 移动原生源码／CMake 造成的新构建身份及 CPU 精度变化，必须新测；旧表不直接复用。
- 任意用户 XML 并未得到验证。首版支持字段、缓存模式、容量及 partition 条件须列白名单，拒绝未经支持的组合。
- 通用 DAG 的索引和活跃状态资源消耗需 pilot；只读图搜索的有限内存表现不能自动证明任意 DAG 有界。
- 本轮未重新核验 SIFT1M 全量 payload、可用 RAM／磁盘或既有结果实体，实施前全规模采集门槛仍未满足。

这些事项已列入实施任务和出口，不能当作当前功能缺陷已修复，也不需要为静态核查先运行历史大矩阵。
