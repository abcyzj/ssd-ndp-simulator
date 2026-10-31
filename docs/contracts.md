# V0.1 语义与计费契约

规范标识 `ndpsim-minimal/1`。保留完成边界、四阶段顺序、消息 schema 和唯一计费者；表中签名是语义描述，不是通用 read/invoke 插件 API。实际 C++ 接口见[扩展指南](extensions.md)，能力与观测现状见[输出](output.md)及[限制](model-limitations.md)。修改外部语义需同步契约、夹具与回归，说明兼容性影响。

[原始冻结记录](archive/v0.1/m0.4-minimal-contract.md)包含当时的 Planned/PoC 表，不代表当前实现状态。[手算夹具](../tests/fixtures/m0.4-round.json)由 `tools/verify_contract.py` 持续复核；手算不替代真实 SST 验收。

## 1. 句柄、接口和错误

单进程、单 SSD、一个 SST Component、单 rank/线程，每次实验新进程。`TimeNs`、QueryId、TaskId、HandleId、BackendRequestId、TransactionId、MessageId、ContinuationId 为 uint64；ID 从 1 单调分配、0 保留、进程内不复用，溢出报错。uint32 ObjectId 为向量/图节点 ID。日志附 run_id；地址和裸指针不作稳定 ID。ContentVersion 为 uint64 不可变版本，另记录内容 SHA256；版本相同而内容 hash 不同为输入错误。

`ObjectRef={dataset_id, object_kind, object_id, content_version}`；`ByteRange={offset,length}` 为半开对象内字节范围，length 必须为正。`Extent={lba:uint64,sectors:uint32,logical_slices[]}`，每 slice 保存对象范围与 extent 内偏移。LBA 单位为 512 B；范围先向外覆盖 sector，同批重叠/相邻 sector 合并，再按后端 page 拆事务。不同批/查询不隐式缓存或合并。逻辑有效字节、sector 字节、NAND 页访问、ONFI 字节和 PCIe 字节分别记账。布局须验证版本、范围、加法/乘法溢出及 namespace 边界；PPA 不是永久地址。

`DataHandle={id, object_refs/ranges, version, location, producer_task, state}` 是不透明能力，location 只取 `SSDStorage/DeviceBuffer/HostMemory`。内容、LBA 与驻留各自独立；仿真宿主机拥有完整数据不授予模拟主机读取权。SSDStorage 引用只用于建立读依赖，不可 try_get 数值。输入到设备并不使主机副本有效；每个副本有独立 handle。

| 接口（C++17 语义签名，尚非可调用 API） | 冻结语义 |
| --- | --- |
| `resolve(ObjectRef, ByteRange) -> extents` | 返回有效字节和实际 sector 覆盖；禁止算法直接取 ContentStore 指针 |
| `read(extents, destination, context) -> {task,handle}` | HostMemory 选普通 NVMe；DeviceBuffer 选本地 route；不允许隐式改位置；SSDStorage 目标为 Unsupported |
| `invoke(op, inputs, params, placement, consumer) -> {task,handle}` | placement 仅 HostCpu/Controller；consumer 默认 HostMemory，二者独立；Controller→HostMemory 自动建返回依赖；设备结果可在设备建立依赖 |
| `when_all(handles, continuation_id)` | 全部目标副本 Visible 后恰恢复一次；任一失败异步恢复错误，不阻塞线程；空集也排队一次，不内联递归 |
| `try_get(handle, consumer)` | 返回 Ready 值或结构化状态；Visible 且位置及权限匹配才可读取；算法仅有 HostMemory 权限 |
| `release(handle)` | 释放调用方引用；重复 release 为 Released 错误；在途 producer/consumer 持有内部引用，不能提前回收 |
| `evaluate(ready_inputs, params) -> {private_output,work}` | 受信执行器才取得真实只读视图；输出进入私有槽，绝不直接调用算法 |
| `reserve(bytes,lifetime,context) -> credit/wait` | 超过可容纳单块立即 CapacityExceeded；暂满 FIFO 等待；credit 只由 BufferModel 释放一次 |
| `submit_read(route,extents,context) -> request_ids` | 每实际后端请求一个 ID；父任务汇总所有请求；每请求汇总所有事务及缓存服务 |
| `send(src,dst,schema,payload,context) -> message_id` | 唯一传输模型为 MQSim PCIe_Link，共享原有双向队列；到达后才完成 |
| `initialize/peek/synchronize_now/dispatch_current_time` | 包装现有 E1 入口；只在 SST now 执行当前片，不调用原生完整循环代替桥接 |
| `capabilities()/subscribe(filter)` | 仅研究接口；记录来源、定义和实现状态，不授予算法全局设备状态 |

`RequestContext` 必含 query/task、operation、route、对象范围/version；后端扩展稳定 request/transaction ID。内部 mapping/GC 以 source 标注，query/task 可为 null 并给出 reason，不能强行归给当前查询。

错误记录为 `{code,query_id,task_id,handle_id?,request_id?,time_ns,detail,causal_parent?}`。冻结 code：NotReady、InvalidHandle、Released、WrongLocation、PermissionDenied、VersionMismatch、OutOfRange、Overflow、InvalidArgument、Unsupported、CapacityExceeded、DependencyFailed、BackendFailure、Stalled。先检查 ID/释放/版本/权限与位置，再返回 Failed 或 NotReady；NotReady 不是失败。无效请求提交前拒绝且不占 credit；依赖失败不再发后继 I/O，保留根因。不可恢复的在途后端错误终止实验、退出非零且不产生成功结果；V0.1 不提供在途取消/回滚后继续运行的承诺。

## 2. 完成、可见性与生命周期

Task 状态为 `Submitted → WaitingDependencies → WaitingResource → Running → ServiceDone → WaitingReturn → Visible`；无需某阶段可零时间跳过，但仍遵循事件顺序。失败为终态 Failed。handle 为 Pending/Visible/Failed/Released；运行时私有结果不属于 Visible。任务 Visible 表示在指定 consumer 的目标副本完成，不表示所有位置都有效。

| 操作 | ServiceDone | 允许发布 Visible 的边界 |
| --- | --- | --- |
| 普通 read | 所有后端请求的事务/cache 服务汇总完成 | 所有关联 sector 数据 D2H 到达 **且** 对应 CQ 被主机消费；不能只认 CQ 或存储完成；CQ head doorbell 仍真实计费、排空，但不额外阻塞已到达数据 |
| 本地 read | cache 汇总满足事务列表空且缓存服务计数归零，含 NAND data-out | E2 同刻安全清理后稳定完成记录到达；新增 staging 写服务完成才把 DeviceBuffer handle 设 Visible；无普通 SQ/CQ/完整向量 D2H |
| 主机计算/控制 | 输入到主机、资源获准、功能执行完成后，配置的模拟 CPU 服务完成 | 本地私有结果提交；输出不再走 PCIe |
| 盘控计算 | query/命令到达、设备输入及 staging 完成、资源获准后，模拟计算完成 | DeviceBuffer consumer 可本地发布；HostMemory consumer 须等待结果读出、消息全部到达及端点处理完成 |
| 上传/返回 | 对应源端准备服务完成 | 所有消息到达目标及目标 staging/处理完成；发送入队不等于完成 |

功能执行在输入就绪及资源获准后执行一次，work 含元素数、运算数、输入/输出/scratch 字节、控制比较次数；真实输出长度决定传输量。墙钟耗时仅记仿真效率。默认 FP32 输入、按维度固定顺序 FP64 平方 L2，拒绝非有限值，禁 fast-math/FMA 改写；排序键为精确 `(distance, uint32 ID)`，容差 abs 1e-9 + rel 1e-7 仅用于跨运行核对，不用于同分判断。整语义批次所有 chunk 返回后才更新候选。

请求由后端拥有，事务由原 PHY 路径释放。本地最后事务信号返回前不可删除请求；同刻清理复制值记录再释放 request，平台仅接收值，不持有其指针。父任务去重 request ID，拒绝重复完成；所有子请求成功才提交一次。运行时持有依赖和私有输出，待引用归零且在途访问结束回收；credit 生命周期独立于 handle 引用。

## 3. 时间与事件顺序

SST timebase 和 SelfLink 为 1 ns；uint64 整数 ns，只有 SST 推进权威时间。新增服务用 `setup_ns + ceil(work/rate)`，验证正有限 rate 和算术溢出。MQSim 原生传输取整保持原样，不统一改成 ceil。

平台调度键为 `(time_ns, delta:uint64, phase, sequence:uint64)`，phase 固定为 0 Backend、1 ResourceCompleteRelease、2 Publish、3 ContinueSubmit。同 phase FIFO sequence；每次入队分配单调 sequence，已开始的阶段不重入。phase p 回调产生同刻 q>=p 的事件进本 delta，q<p 进 delta+1；不增加 1 ns。phase 0 内 MQSim 保留原生 EventTree FIFO，持续吸收同刻新事件（包括 E2 安全清理）直到稳定，再交阶段 1。完成观察回调只复制完成信息并入队后续阶段，不内联恢复算法。

例如 t=30：后端最后事务→同片安全清理→phase1 staging/资源释放→phase2 发布→phase3 continuation 提交新 I/O；新 I/O 的同刻后端事件进入 `(30,delta+1,0)`。零 ns PCIe 消息依此执行，不越过当前时间也不递归恢复算法。

每次注入先同步 MQSim 到 SST now；有有效过去事件即失败，不能补跑。peek 最早时间与平台最早时间取 min 预约；更早事件递增 generation，过期唤醒只忽略，不能同步后端时钟。Ignore 沿用 E1 回收语义，不再使用可能已回收的事件指针。默认同刻有效后端事件上限 100000、平台同刻事件上限 100000（跨 delta 累计），超过即 Stalled 并报告近因果链。队列为空但任务/credit/消息未结束为 Stalled；查询完成后仍需排空必要消息、资源和后端事件才允许成功 finish。通用调度器实现见 `src/backend/event_bridge.h` 与 `src/runtime/runtime.cc`。

## 4. 每项服务与流量的唯一归属

| 服务/流量 | 唯一 owner | 边界与禁止重复项 |
| --- | --- | --- |
| NAND array、FTL、GC/WL、mapping I/O、物理页 | MQSim | 无平台 SSD 固定服务费；后台单列 source |
| ONFI channel/命令/data-out 与 chip/die 调度 | MQSim | NAND 输出传输已经收费，不再在平台按同一 ONFI 字节收费 |
| MQSim cache/DRAM、CMT | MQSim | 仅后端实际模型；SIMPLE/TURNED_OFF 仍保留 CMT |
| query/input/result/scratch credit | BufferModel | 独立新增缓冲容量，非 MQSim DRAM 容量，不宣称共享竞争 |
| 独立缓冲写入 staging、计算读取、结果写入/读出 | BufferModel | 每次新增拷贝分别计字节和服务；与 ONFI/PCIe 是不同资源阶段；计算 rate 仅含算术，不能再含这些缓冲服务 |
| Controller 距离计算 | ComputeModel | 非流水 slots+FIFO，`launch+ceil(work/rate)`；不另加独立吞吐罚时 |
| Host 距离与候选选择/去重/更新/最终 top-k | HostCpuModel | 共享有限 slots，主机距离 rate 含主机数据加载，不另收 Host DRAM；控制按真实计数和 setup |
| 普通 NVMe SQ/CQ/doorbell/数据，query/命令/结果/状态 | MQSim.PCIe_Link | 两方向各 FIFO、可双向重叠；传输入队与到达逐消息记账；SST SelfLink 只调度 |
| 平台端点 decode/encode 固定处理 | EndpointModel | 默认 0，需配置并记阶段；无第二个序列化带宽费，不含原生 NVMe 端点已有行为 |
| ContentStore、真实函数、序列化宿主机内存 | WallClockOnly | 不记目标系统服务，观测零附加模拟时间 |

缓冲 credit 在本地读提交前预留：query 常驻 + 输入 sector 覆盖 + 输出上界 + scratch；命令 IDs/header 接收空间也计入工作块 scratch，不能作为无限免费设备队列。接收拥塞可先在主机保留待发命令；不把传输完成伪装成设备可执行。query 上传前预留目标空间。工作块固定按配置上限及单查询可容纳量确定，不能按瞬时空闲量改变语义批次。输入/scratch 在计算最后使用后释放；结果在 D2H 到达前保守保持；query 在 CloseAck 后释放。传输保留源数据至到达，失败先停止新任务、排空安全可完成的访问再释放；不可安全排空即进程失败。

BufferModel 新增阶段默认分离串行服务：input staging write → compute-input read → arithmetic → result write → result read-out → PCIe；query 接收写入一次、每块 query 读取计一次。缓冲端口默认单 FIFO，容量与带宽是两个约束，可与其他独立资源重叠；未实现流水重叠。HostCpu rate 含主机加载而 Controller rate 不含独立缓冲访问，这一口径必须写入配置。scratch 若有实际读写由 work 声明，不能只按容量推断流量。

接纳需保留至少一个完整工作块前进空间（当前实现保留最大确定性块，见[盘控资源](controller.md)），不能让 query credit 占满设备；单 query+最小块都放不下时启动失败；暂满才 FIFO 等待。返回消息目标主机内存默认不建容量压力，必须作为简化记录。

## 5. 消息格式与字节表

平台消息版本 1：little-endian，逐字段编码，禁止 `sizeof(C++ struct)`、指针、隐式 padding。float32/float64 按 IEEE754 位编码，所有保留位为零。每消息一条逻辑 envelope，经原 MQSim 双向队列封装；共享平台消息及端点分派已实现，见 `src/transport/`。

| 公共头字段 | offset | bytes |
| --- | --- | --- |
| magic（ASCII `NDPS`） | 0 | 4 |
| schema_version=1 / kind | 4 / 6 | 2 / 2 |
| total_bytes（含头） / status | 8 / 12 | 4 / 4 |
| query_id / task_id / query_token | 16 / 24 / 32 | 8 / 8 / 8 |
| count / reserved=0 | 40 / 44 | 4 / 4 |

头共 48 B；token 为设备 query handle 的稳定 uint64 ID，Open 请求为 0，OpenAck 授予非零 token；非零 task_id 为本次消息操作 ID，ACK 回显。status 0=OK，1=InvalidArgument，2=Unsupported，3=CapacityExceeded，4=DependencyFailed，5=BackendFailure，6=VersionMismatch；非响应 status 必须为 0。诊断文本只进日志，不隐含在线字符串。MessageId、chunk 关联、上下文由运行时持有，Query/Task/Token 均验证所属关系。

| kind 数值/名称 | 方向 | body（固定字段顺序） | total_bytes |
| --- | --- | --- | --- |
| 1 QueryOpen | H2D | dimension:u32, metric:u16=1(L2sq), dtype:u16=1(FP32), content_version:u64, query:FP32[d]；count=d | 64+4d |
| 2 QueryOpenAck | D2H | 无；count=0，成功含 token | 48 |
| 3 Score | H2D | chunk_id:u64, dimension:u32, reserved:u32=0, ids:u32[b]；count=b | 64+4b |
| 4 ScoreResult | D2H | chunk_id:u64, records:packed{ID:u32,distance:FP64}[b]；count=b，输入 ID 顺序 | 56+12b |
| 5 QueryClose | H2D | 无；count=0，等所有块成功返回后发送 | 48 |
| 6 QueryCloseAck | D2H | 无；count=0，确认设备已不使用该 query | 48 |
| 7 Error | D2H | 无；count=0，非零 status；关联失败 task/token | 48 |

Open 失败返回 Error(token=0)；Score 失败返回 Error 而非部分 ScoreResult；Close 失败返回 Error。chunk_id 在 query 内唯一，b>0、ID 不重复、维度/version 必须匹配，长度与 count/total_bytes 精确一致；未知版本/kind、非法方向/status、非有限值、截断/溢出或超限消息拒绝，不产生成功 ACK。可识别头的命令错误返回一次 Error；无法可信解析/关联的封包导致实验失败，不构造虚假 QueryId。错误消息本身仍走链路计费；不可恢复后端错误可能只有非零退出日志，不承诺发回 Error。V0.1 不重试、不分片重组；每块必须满足后端 `unsigned int` payload 和 `int total_transfered_bytes` 上界，否则配置失败/减小确定性 chunk。

`payload_bytes` 是传给链路的 envelope 全长（平台头已计入），`useful_bytes` 另记 query/IDs/得分等语义量。原生消息保留现有 `sizeof` 和编码；不把普通 NVMe 消息改成平台头。

源码依据：[PCIe_Link.h](../third_party/MQSim/src/host/PCIe_Link.h)、[PCIe_Root_Complex.cpp](../third_party/MQSim/src/host/PCIe_Root_Complex.cpp)、[Host_Interface_Defs.h](../third_party/MQSim/src/ssd/Host_Interface_Defs.h)。令 P=payload、M=max_payload、O=tlp_header+dllp+physical，则 WRITE_REQ/READ_COMP 及平台 envelope 的 `wire=P+ceil(P/M)*O`（P=0 为 0）；READ_REQ 的 wire 固定 O+4。原生服务为 `floor(ceil(wire/lanes)/lane_GBps)` ns，保留小消息 0 ns 语义。packet header 不能再在 EndpointModel 收一次。wire 仅指 MQSim 公式模拟量，不代表完整真实 PCIe 协议。H2D/D2H 各按实际发送实例记录，观测聚合不是第二次收费。

原生 QD1 单读的消息表：SQ doorbell H2D 2 B → SQ fetch READ_REQ D2H 4 B → SQE READ_COMP H2D **72 B** → read data D2H sector覆盖量 → CQ D2H 16 B → CQ head doorbell H2D 2 B。本机 ABI 的 SQE 是 72 B，不能按 NVMe 标准想当然记为 64 B；由契约 ABI 探针复核。并发/队列行为按实际消息统计，不机械乘此 QD1 表。

## 6. 手工核对一轮两种模式

固定 d=32、B=1、两个新邻居 b=2；query 128 B 主机常驻；邻接 degree=2 有效 12 B、LBA32 的 512 B；两个向量有效 256 B，分别 LBA7/8 各一 sector，合成 1024 B 跨两 NAND page（8 sectors/page）。输入是手算路径夹具，不是预置完整搜索 trace。query、候选/visited 在主机；向量、邻接在 SSDStorage；无 PQ/跨批缓存。服务输入总量两模式相同：邻接512+向量1024=1536 B；实际 NAND/ONFI 数值以后端事件为准，不以 sector 量推算整页链路量。

主机轮：CPU select → 普通邻接 read（数据+CQ到达）→ CPU 去重出 IDs → 普通 vector read（两个子事务都完成、数据+CQ到达）→ HostCpu L2 → CPU merge/top-k → 下一轮。query 不上传；得分为主机内存输出；不存在平台结果消息。

盘控轮：预留 query、Open→Ack（可与邻接 read 重叠）→ 与主机相同邻接/去重 → 预留整块 credit→Score 到达→本地 vector read（两子事务汇总、同刻安全清理）→staging→buffer input/query read→Controller L2→result write/read→ScoreResult D2H 到达→HostCpu merge/top-k→Close→Ack。向量 HostMemory 从未有效，vector D2H=0；邻接 512 B 仍完整返回。手算计入一次查询开启与关闭开销；多轮时 Open/Close 各按 query 一次，不能每轮重收。

取 M=128、O=28、lanes=4、lane_GBps=1 的未校准记账参数：

| 模式/消息组 | H2D payload / wire B | D2H payload / wire B |
| --- | --- | --- |
| host 邻接普通 read（含 SQ/CQ） | 76 / 160 | 532 / 700 |
| host 向量普通 read（含 SQ/CQ） | 76 / 160 | 1044 / 1324 |
| **host 合计** | **152 / 320** | **1576 / 2024** |
| controller 邻接普通 read | 76 / 160 | 532 / 700 |
| controller Open / OpenAck | 192 / 248 | 48 / 76 |
| controller Score / ScoreResult | 72 / 100 | 80 / 108 |
| controller Close / CloseAck | 48 / 76 | 48 / 76 |
| controller 本地 vector read | 0 / 0 | 0 / 0 |
| **controller 合计** | **388 / 584** | **708 / 960** |

因此 fixture 的 payload 总量 host=1728、controller=1096 B；wire 总量 host=2344、controller=1544 B。这里只证明契约账本一致，不能据此声称延迟更低。存储与链路不重复相加字节，缓冲访问另记资源字节：本例无额外 scratch 访问，controller query write=128、input staging write=1024、compute input+query read=1152、result write=24、result read-out=24 B，共2352 B；查询128+input1024+result24+命令scratch72=1248 B credit 峰值（序列执行、无其他块），所有 credit 最终归零。结果缓冲只存 packed records，不把传输头又当结果数值复制。


## V0.2 T5 受信回收边界

生产观测不保留已完成任务/lease/消息历史；结果 owner/pin、依赖与发布 continuation、服务/返回 completion 全部解除后才删除 Runtime 任务。Controller 只在已有安全失败边界排空本地读取、staging/compute 及 Error 返回后调用 `fail_after_drain`；不能将该入口用于任意在途取消。详情见 [T5 观测与生命周期](t5-observation-lifecycle.md)。不改变 SST/MQSim 时间、可见性或唯一计费者。
