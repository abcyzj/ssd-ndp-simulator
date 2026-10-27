# SSD 近存计算仿真平台 V0.1：源码核查与实施规划

日期：2026-10-23（Asia/Shanghai）  
环境与依赖修订：2026-10-27，构建基线改为本机 macOS（Apple Silicon / arm64），两个依赖 submodule 已初始化；本机工具链见第 12 节。  
依据：SSD_NDP_Simulator_V0.1_PRD.md，重点覆盖第 9、11 节及 FR-01～FR-10、AC-01～AC-11。  
截至 M0.2 的历史状态：M0.1 静态归档及 M0.2 本机工具链、SST/原生 MQSim 构建与小读基线已完成，当时未修改依赖源码，平台集成、元素加载及 AC 尚未运行。运行证据见 [M0.2 记录](m0.2-native-baseline.md)；当前 M1.1 进展见下方补记。

2026-10-27 M1.1 进展补记：已完成 PIC 库、`ndpsim.System` 加载及无 I/O 单设备生命周期验收；随后按用户要求将同样的改动直接落实到 `third_party/MQSim`，平台直接编译子模块，不再使用构建时 patch 栈。原生 oracle 保持不变，源码身份及维护方式见 [MQSim 源码维护约定](mqsim-source-management.md)。仅支持 `native-small` profile，事件桥、Host/I/O 路径及 AC 仍未验证；UBSan 随机数溢出、未做泄漏审计等限制见 [M1.1 记录](m1.1-library-lifecycle.md)。本文后续章节保留原设计依据，当前任务状态以 [任务清单](v0.1-tasks.md) 为准。 后续构建流程已按用户要求统一到根 CMake：通过单一 tools/prepare_sst.py 在配置阶段自动准备、构建并私有安装 SST，命中有效缓存则直接复用，可显式指定 SST_ROOT 复用安装，M0.2 脚本不再是前置步骤，实际入口见 [README](../README.md)。

## 1. 结论与范围

推荐采用 **C++17、一个 SST Component 内的多个逻辑模块、回调驱动的算法状态机、可步进的 MQSim 事件桥**。主机维护搜索循环；平方 L2 批量距离算子支持主机 CPU 和 SSD 控制器两个位置。默认示例使用确定性的小型图搜索，不把它称为 DiskANN/AISAQ 复现。

首要工程问题是 MQSim 事件推进和盘控本地读取路径。它们需要适配开发，现有源码未提供可直接使用的 SST 集成。先通过存储基线和路径守恒实验，再扩大算法和并发规模。近存模式的收益是实验结果，不能作为实现假设。

V0.1 保留：单 SSD、应用层只读索引/向量、真实结果决定下一轮访问、异步依赖、有限查询并发、参数化计算/缓冲/传输、设备/请求/channel/die 观测和命令行离线实验。

V0.1 不开展：多 SSD、可写搜索、自主盘内搜索、channel/die/plane/latch 计算、周期精确加速器、GPU 依赖、多个存储后端、Web 前端、完整 DiskANN/AISAQ 复现。

PRD 没有发现必须改变目标才能消除的直接冲突；以下边界需要明确：

| 表面张力 | 本规划的处理 |
| --- | --- |
| SST 统一时间，而 MQSim 有自己的事件引擎 | 保留 MQSim 队列和设备语义；只有 SST 到达相应时间，后端才执行该时间的事件 |
| 应用只读，而 MQSim 要建立映射/设备状态 | 初始化允许预装载写入和内部维护；测量区间的应用请求只读，初始化活动单独记录 |
| 研究观测要求 die，而原生公开信号主要到 chip | 增加 die 状态变化观测；不能把 chip idle 信号冒充 die 利用率 |
| 逻辑数据真实，而 MQSim 主要模拟时序/元数据 | 真实内容由平台只读 ContentStore 保存；驻留和可见性由运行时门控 |
| 第一版可简化模型，但所有 FR 都是必须项 | 简化只影响精度和策略；真实数值、因果依赖、路径计费和观测可信度不可省略 |

## 2. 核查依据、版本与证据边界

### 2.1 实际检查了什么

原核查时工作目录只有 PRD，没有现有实现、构建文件或 Git 仓库，也未找到适用的 AGENTS.md。当前主项目已使用 Git，AGENTS.md 和两个依赖 submodule 均已存在。后续开发、构建与验收以本机 macOS / arm64 为基线，工具链已做路径和版本盘点，CPU/内存资源、本机构建及原生运行已由 M0.2 补齐，见第 12 节与证据索引；不填写未经测量的运行效率。

本轮通过公开远端查询并下载了三份上游源码，逐文件检查下述入口；下载位于 /private/tmp/ssd-ndp-audit-20261023/，三个上游检出的 git status 均为空。临时目录不作为交付依赖，下面的完整提交号和源码永久链接才是复核依据。

上述下载记录属于 2026-10-23 静态核查。2026-10-27 已在主项目初始化以下依赖，当前开发使用仓库内的 submodule，不依赖原临时检出：

| 依赖路径 | origin URL | 当前固定 commit | 状态与维护方式 |
| --- | --- | --- | --- |
| third_party/MQSim | https://github.com/abcyzj/MQSim.git | 51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16 | 已初始化；与原核查基线一致；后续改动提交到该 fork |
| third_party/sst-core | https://github.com/sstsimulator/sst-core.git | ffd71527bc26a1f2a4fb685fdeb2ca0799997d78 | 已初始化；固定 v16.0.0_Final 对应 commit；不修改 SST Core |

可用 `git submodule status --recursive`、各子模块的 `git rev-parse HEAD` 和 `git remote get-url origin` 复核；.gitmodules 保存路径/URL，主仓 gitlink 保存固定提交。初始化盘点时两个子模块的已跟踪源码均无修改；M1.1 之后 MQSim 适配直接保存在该子模块工作区，HEAD 与未提交差异分别记录，SST Core 保持不变。初始化不表示编译、SST 模块加载、原生基线或 AC 已通过。

| 项目 | 选定的核查基线 | 用途与决定 |
| --- | --- | --- |
| SST Core | v16.0.0_Final；commit ffd71527bc26a1f2a4fb685fdeb2ca0799997d78 | 推荐实施基线；标签对象 fc986275689378649e3b7392da9ffee779ac3b4d 与解引用后的 commit 不同，锁文件保存 commit |
| MQSim | commit 51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16 | 必需后端；本次查询 master 指向此提交，实施后不再跟随 master 漂移 |
| DiskANN C++ | cpp_main 的 commit 78256bbab4685e1774e78d331e081a153be26823 | 仅作复用工作量核查，不成为默认构建依赖；当前主页 main 与 C++ 分支不能混为一谈 |

SST 的版本号还由该提交 configure.ac 的 AC_INIT 确认，[发布入口][S0]与[构建定义][S1]可复核。实际使用的 SST/MQSim 已锁定；**M0.2 已验证本机原生可构建性；集成时序等价性和性能仍未经过验证**。工具链兼容措施与原生限制见 M0.2 记录，PoC 尚未开始；DiskANN 仍仅作调研参考。

### 2.2 SST：可用入口与改动边界

| 实际入口 | 源码发现 | 实施位置 |
| --- | --- | --- |
| BaseComponent 生命周期、configureSelfLink | setup/finish、SelfLink 配置和时间查询存在；自链可以承载下一次唤醒 [S2] | 新增平台 Component；不修改 SST Core |
| Link::send_impl、SelfLink | 事件交付时间由当前时间、delay、link latency 合成；SelfLink 构造时 latency 为 0 [S3] | 平台资源延迟由自身模型产生；自链用于调度，不再额外计一次物理传输延迟 |
| Activity 比较器 | 排序包含时间、优先级/顺序标签和队列序号 [S4] | 不依赖不同 Component 的隐式同刻顺序；V0.1 将有关事件汇入一个平台调度入口 |
| primary component API | registerAsPrimaryComponent、DoNotEndSim、OKToEndSim 存在 [S5] | 所有查询和必需在途任务结束后才释放仿真存活约束 |
| configure.ac、构建配置 | 使用 C++17；有禁用 MPI 的构建选项 [S1] | 本机 macOS 单 rank、单线程；同一架构、编译器、标准库和 C++ ABI 构建 SST 与元素库 |

SST 配置脚本使用 Python 是框架接线方式，不表示新增 Python 算法前端或 C++/Python 运行时绑定。

### 2.3 MQSim：已核查的调用链与必需改动

| 核查项 | 源码事实 | 建议改动与验证点 |
| --- | --- | --- |
| 程序入口 | main.cpp 先 Reset，再构建 SSD_Device、Host_System、Attach，最后调用阻塞的 Start_simulation [Q1] | 抽取/复用参数装载；库目标排除 main.cpp，保留独立原生可执行文件用于对照 |
| 事件引擎 | Engine 是 singleton；Start_simulation 包含触发器安装、验证、启动和完整事件循环；没有公开的外部步进接口 [Q2] | 将初始化与运行循环分开，新增 peek/同步当前时间/执行当前时间片入口及最早事件变化通知 |
| 集成开关 | Is_integrated_execution_mode 固定返回 false [Q2] | 不能将函数名当作集成能力；默认保留 false，不直接开启涉及原始数据指针复制的分支 |
| 同刻事件 | EventTree 按 Fire_time 分组，组内尾部追加；Ignore_sim_event 只置标志 [Q3] | 步进必须保留组内顺序、回调新增同刻事件、忽略事件和释放语义 |
| 时间与初始化顺序 | sim_time_type 为 uint64，ONE_SECOND 为 10^9；对象表为 unordered_map [Q2][Q4] | 后端单位 ns；必须先 Reset；M1 不顺便重排对象启动顺序，记录顺序及工具链并做基线对照 |
| 请求拆分 | NVMe segment_user_request 按扇区、页边界生成事务，User_Request 持有 Transaction_list [Q5] | 复用/抽取拆分逻辑用于本地读取；禁止把一个 NAND 子事务完成当作整任务完成 |
| 盘控可用边界 | PHY 的 READ_DATA_TRANSFERRED 在 NAND 数据输出结束后广播事务完成；cache manager 汇总事务及缓存服务完成 [Q6][Q7] | 设备输入就绪应取请求级汇总点，不能取 NAND array ready 或第一个 die 完成 |
| 普通读取回传 | Handle_serviced_request 调用 Send_read_data，后者按 Size_in_byte 发送完整读取数据，随后有 CQ 完成路径 [Q5] | 新增设备本地读取 route，并在统一完成分派点分流；不能仅追加一个旁路观察者后仍走原回传 |
| NVMe 队列副作用 | Handle_new_arrived_request/Handle_serviced_request 会更改 SQ/CQ 和 on-the-fly 状态 [Q5] | 本地读取不能伪装普通请求而不处理这些副作用；独立入口复用拆分页/缓存/FTL/TSU/PHY |
| 真实内容 | Page 保存 LPA 元数据；copy_read_data_to_transaction 处理 LPA，root complex 对数据内存写回不执行真实内容写入 [Q6][Q8][Q9] | 外部只读 ContentStore 保存向量/邻接内容；按完成事件授予使用能力，不从 MQSim Data 指针取得真实向量 |
| PCIe | PCIe_Link::Deliver 有两个方向队列；带宽/分包开销由 estimate_transfer_time 计算；端点解析固定消息类型 [Q10][Q11] | 保留它作为唯一主机接口计费者；新增平台消息封装和端点分派，普通读和近存消息共享相同队列；验证小消息截断到 0 ns 的原生行为 |
| 初始映射 | 未映射 READ 会调用 online_create_entry_for_reads；preconditioning 在 Host_System 启动中调用 [Q12][Q13] | 不能忽略设备初态；默认确定性预装载布局涉及的 LBA，再进入测量；初态生成不依赖未来搜索路径 |
| 静态状态与退出 | Engine、SSD_Device、cache/host interface 等使用静态实例；错误宏含交互输入，main 也有 cin.get [Q1][Q2][Q4][Q13] | 每个实验进程只建一个设备；批量实验启动新进程；适配入口使用非交互错误报告，保留原生对照构建 |
| 构建 | 上游 Makefile 使用 C++11、直接链接可执行程序 [Q14] | 平台 CMake 包装源码库，编为 PIC 并验证 C++17 兼容；不假定上游已经导出可链接的集成库 |

计划形成下列源码改动组，直接在 MQSim 子模块中实现并分别评审：

1. **事件可步进**：src/sim/Engine.h/.cpp；必要时调整 EventTree.h/.cpp 的安全取出接口；不修改 FTL/TSU 策略。
2. **外部普通 I/O**：新增 IO_Flow_Base 派生的外部 flow，以及 Host_System 的注入/构建路径，保留原有 NVMe/SQ/CQ 完成行为。该基类实际提供受保护的 Submit_io_request 和虚拟 NVMe_consume_io_request；Generate_next_request、Get_statistics 等仍需实现，不能假设 Host_System 已支持外部 flow。[Q17]
3. **设备本地读取**：User_Request 路由/关联元数据、Host_Interface_Base 完成分派、Host_Interface_NVMe 拆分逻辑复用、设备请求生命周期管理。
4. **共享传输及完成门控**：PCIe_Message、PCIe_Root_Complex、PCIe_Switch 的平台消息分派；在主机收到数据消息/CQ 时发出可关联的完成通知；PCIe_Link 只增加必要的流量和队列观测。
5. **内部观测**：ONFI_Channel_Base::SetStatus、Flash_Chip 命令开始/完成及 suspend/resume、PHY transaction 完成钩子；默认不改变调度决定。
6. **库化和诊断**：外部构建包装、非交互失败出口、上游补丁说明和原生对照目标。

以上为拟新增接口/改动，不是已经存在或已经实现的集成功能。

### 2.4 DiskANN 复用决定

核查的 C++ cached_beam_search 将候选扩展、sector 读取、PQ 距离和精确距离写在同一个搜索过程；Linux reader 调用 io_submit 后等待 io_getevents。只替换 reader 会在 SST 回调中阻塞，并且没有解决算子放置与结果可见性。其构建还引入 MKL、libaio 和向量指令选项。[D1][D2][D3]

推荐先实现下面定义的 GraphSearchExample。未来复用 DiskANN 时需要拆分搜索状态、I/O continuation、PQ/精确距离调用和缓存语义；复用索引生成/格式导入可先独立评估。两者都应作为单独范围，不能把本例的 Recall 或搜索路径称为 DiskANN 结果。

## 3. 总体架构与核心不变量

### 3.1 逻辑结构

~~~mermaid
flowchart TD
    A[图搜索状态机：主机控制] --> R[异步运行时：任务、依赖、结果门控]
    R --> L[布局与驻留管理]
    R --> C[主机/盘控计算资源与独立近存缓冲]
    R --> B[MQSim 适配：普通读/设备本地读/传输]
    B --> M[MQSim：缓存、FTL、TSU、NAND、PCIe]
    C --> F[功能算子：真实平方 L2]
    L --> S[只读 ContentStore：向量、邻接、query]
    F --> S
    T[SST 时间与 SelfLink 唤醒] --> R
    T --> B
    R --> O[结果与事件观测]
    B --> O
~~~

这是逻辑职责图。V0.1 使用一个 ndpsim.System Component 包含运行时、资源模型、后端桥和内容仓库，避免提前设计跨 rank 数据共享。只有 SST 推进模拟时间；各模块可持有等待队列，但不能独立运行到未来。

| 模块 | 负责 | 不负责 |
| --- | --- | --- |
| AlgorithmSession | 候选/已访问集合、批次、停止和状态恢复 | NAND 类型、PPA、MQSim 全局对象 |
| Runtime | QueryId/TaskId/依赖、操作计划、结果可见性、错误传播 | 用真实函数耗时决定模拟延迟 |
| ContentStore + Layout | 不可变内容、版本/hash、对象字节范围到 LBA、驻留句柄 | 模拟 SSD 的物理地址分配 |
| FunctionalExecutor | 返回真实值、工作量和真实输出大小 | 直接发布给算法、私自绕过读取路径 |
| Compute/Buffer | 服务时间、slots/队列、容量/credit、释放和背压 | 重复计算 NAND/PCIe 延迟 |
| MQSimBackend | 事件桥、普通/本地请求、事务关联、共享 PCIe、完成边界 | 把后端内部类型泄漏到算法接口 |
| Observer/Experiment | 能力标记、事件/统计、配置与版本快照、查询结果 | 无成本向算法提供全局 SSD 状态 |

### 3.2 关联与可见性

每个只读对象都有 ObjectId、ContentVersion、内容 hash；每次模拟拷贝都有独立的 DataHandle。handle 记录对象范围、目标位置、producer_task、状态和引用计数；不向算法提供原始指针。

关联链为：

    QueryId → TaskId → BackendRequestId → [TransactionId...]
                   → 输入/输出 DataHandle → ContentVersion
                   → SST 事件（时间、delta、序号、状态转移）

Task 状态建议为：Submitted → WaitingDependencies → WaitingResource → Running → ServiceDone → WaitingReturn → Visible，任意阶段可转 Failed。普通读的 ServiceDone/Visible 与计算任务的边界分别定义，不共用一个含糊的“完成”状态。

必须保持：

1. 持有 handle 只表示可以建立依赖；try_get 在 Visible 前返回 NotReady，无效/已释放句柄返回结构化错误。
2. 功能算子在输入已到计算位置、资源获准时执行，真实输出进入私有结果槽；服务和必需传输完成后才提交到消费者。
3. 主机结果可见时间不早于输入依赖、计算完成和返回数据到达；普通 NVMe 读还须满足原生完成协议。多子请求必须全部成功才发布父任务。
4. 算法只在自己的 continuation 被调度时恢复；等待只挂起当前查询，不能等待线程条件变量或阻塞 SST 引擎。
5. 平台只读内容可以常驻仿真宿主机，模拟 HostMemory/DeviceBuffer/SSDStorage 的有效性仍独立维护。算法没有 ContentStore 的任意读取权限。适配层必须核对请求/完成的逻辑 extent 及可得的 LPA 信息，不能用直接读取内容仓库掩盖后端地址关联错误。
6. 一份物理搬运/服务只有一个计费者；observer 不产生额外延迟，算法看不到 observer 的全局内部状态。
7. task、handle 和资源有确定的失败清理规则；不支持任意放置/plane/latch 能力时明确返回 Unsupported。

## 4. SST–MQSim 事件桥方案

### 4.1 方案比较

| 选项 | 评价 |
| --- | --- |
| 后端完整跑完再回填延迟 | 破坏外部事件插入和闭环，排除 |
| 固定周期轮询 MQSim | 可做调试参考，但增加空转/量化并使同刻交错难解释，不作默认 |
| 保留后端队列、精确调度下一时间片 | 改动集中，保留原生同刻队列，推荐 |
| 把每个 MQSim 事件直接改成 SST Event | 长期可选，但涉及更多生命周期和取消语义，不作为第一条实施路线 |

### 4.2 推荐协议

- 平台统一使用 uint64 整数 ns。MQSim 源码已有 ns 时间语义；SST Core timebase 和 SelfLink timebase 均显式设为 1 ns。新增资源服务时间向上取整到 ns，溢出、负延迟、非有限带宽参数在加载时拒绝。
- initialize 只执行 Reset 后的触发器安装/验证/启动，不进入原来的 while 循环。后端当前时间初始化为 0。
- 适配层查询后端最早事件 T，与平台资源/查询事件的最早时间取最小，向 SST SelfLink 预约唤醒。若新事件更早，则发新唤醒并用 generation 标识忽略旧预约，避免依赖不存在的自链取消 API。
- 唤醒到达时，先核对 SST now。MQSim 仅能执行 Fire_time == now 的事件，绝不执行 Fire_time > now 的事件；出现未处理的 Fire_time < now 是桥接错误并立即诊断，不能悄悄补跑。
- 外部 I/O/消息注入前把 MQSim 的当前时间同步到 SST now；只能在不存在未处理过去事件时同步，防止请求基于陈旧时间排队。
- 保留 EventTree 同刻 FIFO 和 Ignore 语义。对本轮回调新插入的同刻后端事件继续按原生规则处理到稳定，再离开后端阶段；删除事件和其参数遵循各自所有权约定。
- 在平台入口定义顺序键 (time_ns, delta, phase, sequence)：当前轮已有后端事件 → 资源完成/释放 → 结果发布 → 算法恢复与新提交。同一 phase 内按单调 sequence 稳定处理。晚阶段产生的同刻早阶段事件放到 delta+1，不人为增加 1 ns。
- 全部会影响设备的查询提交/计算完成/传输提交都经该入口，避免依赖 SST 不同 link 的碰巧排序。后端内部的原有 FIFO 保持不变。
- 设置同刻无限事件诊断上限，报告近期因果链；队列无事件但仍有未完成任务时，报告依赖/credit/队列状态，不能输出“成功结束”。

这里的 initialize、peek_next_time、dispatch_current_time 等是计划增加的适配 API。原有 Start_simulation 保持可独立运行，并尽可能复用同一初始化与时间片执行逻辑，以便基线对照。

M1 的默认目标是整数 ns 输入下原生与桥接完成时间完全一致。若出现差异，先查初始化顺序、同刻排序和完成边界；不能把任何差异泛称为量化误差。新增浮点参数取整另设显式 oracle；只有能逐事件解释并被测试限定的量化差异才可接受。

## 5. 一轮 ANNS 的具体数据流

### 5.1 算法、数值与布局默认

示例采用有限候选宽度 L 的确定性 best-first 图搜索：固定入口 e、批扩展大小 B、k、最大扩展次数 E。数据为 FP32 有限值，平方 L2 以固定维度顺序累加 FP64，输出 FP64；禁用 fast-math/隐式 FMA 改写。候选按 (distance, uint32 ID) 升序，批次等齐后更新，拒绝 NaN/Inf。跨工具链距离容差建议 abs 1e-9 + rel 1e-7；默认功能对照使用同一工具链/同一算子，期望候选决策相同。

    score(entry)，等到主机可见后入候选集合
    while 候选内有未扩展节点 且 扩展数 < E:
        按稳定次序选至多 B 个未扩展节点，标记已扩展
        并发读取它们的邻接表，等待该批全部到达主机
        按 ID 排序、去重，移除已经评分的邻居
        按预定块大小发起 score(neighbor_ids, query, placement)
        等本轮所有 score 结果在主机可见
        按稳定次序合并/裁剪候选至 L，保留已评分节点的 top-k
    返回已评分节点中的 top-k；若不足 k，输出不足标记

算法终止由候选状态和预算决定，不需要预知完整访问 trace。邻接和评分阶段均可有多个独立请求在途；B=1 是最简单的因果测试配置。

| 对象 | 默认表示/位置 | 读取与保持规则 |
| --- | --- | --- |
| query | FP32[d]，初始 HostMemory | 主机模式常驻；盘控模式每查询上传一次，DeviceBuffer 驻留至查询关闭 |
| vector[id] | FP32[d]，SSDStorage 的独立向量区域 | 两种模式同一内容和布局；按需要读取，没有默认完整主机副本 |
| adjacency[id] | uint32 degree + uint32 IDs，SSDStorage 独立区域 | 搜索控制在主机，两种模式均通过普通 NVMe 读取到主机；degree/ID 均计入返回 |
| 候选、visited、expanded | HostMemory | 主机控制成本显式建模；不作为 SSD 的免费输入 |
| 距离与候选 ID | 主机决策所需输出 | 盘控结果按显式 wire schema 返回 ID+FP64 distance；主机模式已有 ID，仅本地生成得分 |
| PQ | 默认不使用 | 不配置、不建立假 PQ 缓存；未来作为独立算子与布局扩展 |

布局建议：vector stride = align_up(4d, 512)，邻接 stride = align_up(4+4R, 512)，两个区域分离，偏移/大小写入 layout.json。stride padding 不是功能有效数据，但读取所在扇区的字节须计入链路/缓冲。跨 sector 对象向外覆盖 512 B，后端再按所配 NAND page 边界拆分；按字节范围合并同一批重叠读取，V0.1 默认不做跨批/跨查询数据缓存。跨页能力用专门对象夹具验证，不能因默认 d 小就省略。

必须分别统计有效对象字节、sector 请求字节、NAND array 页访问次数、ONFI 实际传输字节和 PCIe 模拟字节；不能把“页读取”直接等同于一整页都经过 PCIe。

### 5.2 主机模式

1. 主机 CPU 资源为候选选择、邻接去重等工作计费；控制完成后提交本轮邻接普通读取。
2. MQSim 模拟请求/SQ 获取、cache/FTL/TSU、NAND 命令/array/data-out、完整读数据 PCIe 返回及 CQ；聚合完成后邻接 handle 在主机可见。
3. 主机根据邻接产生向量读取；这些向量同样通过普通 NVMe 完整返回。平台只在关联的数据消息和请求完成条件均满足后，标记 HostMemory 有效。
4. CPU 距离模型获准后，功能执行器使用这些已有效的数据和本地主机 query 算真实 L2；模拟服务完成后发布主机得分。
5. 本轮得分齐备后，提交候选更新控制工作；完成后决定下一轮。

因果链：邻接到达 → 邻居确定 → 向量到达 → 主机计算完成 → 主机候选更新完成 → 下一轮读取。多个向量请求、不同查询和已具备输入的独立任务可因资源允许而重叠。

### 5.3 盘控模式

0. 每查询先经共享 PCIe 队列发送 query 和查询元数据，预留并保持 query buffer；上传完成后设备 query handle 有效。可与本查询不依赖 query 的邻接读取重叠。
1. 邻接读取与主机候选选择仍走上述主机控制路径，不能假定邻接免费驻留主机。
2. 主机将待评分 IDs、query token、维度/算子参数作为近存命令发送，经同一 PCIe H2D 队列到设备。只有命令到达、query 有效且设备资源/缓冲可接纳，才生成本地向量读取。
3. 本地读取复用请求拆分、cache、FTL、TSU、PHY；请求级服务完成后进入 DeviceBuffer staging。此 route 不调用 Send_read_data，也不为本地子读取生成普通 NVMe SQ/CQ 副作用。
4. staging 的容量和写入服务完成后，功能执行器计算真实距离，盘控计算资源模拟执行。输入 buffer 维持至计算不再需要它们；结果 buffer 持有到返回传输读取完毕。
5. 序列化 ID+FP64 得分及状态，经 PCIe D2H 队列回主机。只有返回全部到达后，主机能读得分并执行候选更新。
6. 查询结束后释放 query buffer、结果/输入句柄；query 开启、关闭和错误状态消息的字节/固定处理成本也要配置并记录。

近存链：命令/query 到达 → 本地存储读取/data-out → 设备 staging → 盘控计算 → 紧凑结果返回 → 主机控制。完整向量从未为了该算子先回主机；若盘控反而因缓冲、计算或小任务开销变慢，这仍是有效结果。

### 5.4 字节守恒与时间口径

每批 b 个 d 维向量：向量有效字节 4bd；实际 sector 读取覆盖量由布局决定。盘控得分输出为 12b 字节（uint32 ID+FP64 distance，显式序列化，无结构体隐式 padding），加状态/协议头。首次 query 有效载荷为 4d，加 query 元数据。邻接输出有效量为各节点 4+4degree 之和，普通读路径上的 sector padding 另外记录。

PCIe 原生读还含 doorbell、SQ 获取、CQ 等控制消息；平台消息也有显式 header/分包开销。不得仅比较 4bd 与 12b 而遗漏 query、IDs、邻接和控制流量。wire_bytes 定义为 MQSim 传输公式实际计入的字节；其编码/协议精度不能被解释为未经校准的真实 PCIe 全协议精度。

每查询延迟从被接纳的 query submit（盘控包含 query 上传）到最终 top-k 在主机可见；排在并发上限外的提交等待单独报告，另给 offered-to-complete 延迟。总吞吐 = 完成查询数 / 从首个接纳到最后一个完成的测量窗口。组件累计忙时不能相加冒充端到端延迟；调试时沿依赖边重建关键链。

## 6. 关键接口草案

以下是契约草案，不承诺这些类/方法已存在。采用 C++ 状态机和 callback，避免为了 V0.1 引入协程框架或跨语言绑定。

| 接口 | 输入 / 输出与契约 |
| --- | --- |
| Layout.resolve(ObjectRef, ByteRange) | 返回一个或多个 LBA extent、有效字节与访问字节；拒绝越界、无效 ID、溢出 |
| Runtime.read(extents, destination, context) | 返回 task + DataHandle；HostMemory 选普通路径，DeviceBuffer 选本地路径；destination 不可隐式切换 |
| Runtime.invoke(op, inputs, params, placement, consumer) | 返回 task + result handle；placement 与 consumer 分开，盘控计算/主机消费自动建立必要返回依赖 |
| Runtime.when_all(handles, continuation_token) | 注册一次恢复事件；失败显式传播，不阻塞事件引擎 |
| Runtime.try_get(handle, consumer) | Visible 且位置/权限正确时读取值；其余返回 NotReady/Invalid/Released/WrongLocation |
| Operator.evaluate(ready_inputs, params) | 产生私有 Output + WorkDescriptor；仅受信功能执行器可获得真实数据视图 |
| CostModel.describe(op, WorkDescriptor, config) | 得到计算/内存工作量与服务阶段；数据相关工作量和输出长度来自真实执行结果 |
| Buffer.reserve(bytes, lifetime, context) | 返回 credit 或等待 token；单操作永远无法容纳时立即错误，不无限等待 |
| Storage.submit_read(route, extents, request_context) | 返回稳定 BackendRequestId；完成含请求聚合状态，普通/本地完成边界不同 |
| Backend.initialize / peek_next_time / synchronize_now / dispatch_current_time | 新增桥接接口；检查 SST 时间一致性；不允许自主越过 now |
| Transport.send(src, dst, message_schema, payload_bytes, context) | 适配到 MQSim 同一双向队列；收到到达事件才完成；不是第二个独立带宽模型 |
| Observer.capabilities / subscribe | 返回 Native/Extended/Unsupported 能力及定义；只供研究工具使用 |

RequestContext 至少含 QueryId、TaskId、操作类别、对象范围、route、ContentVersion。由单调 ID 和 request-owned 元数据建立关联，不用裸指针地址作为离线日志标识。映射/GC 等内部事务可无单一 QueryId，记录 source 和可确定的因果关联，不能强行全部算给当前查询。

## 7. 资源归属、服务模型与缓冲生命周期

### 7.1 唯一计费表

| 资源/活动 | 权威模型 | 平台处理与明确限制 |
| --- | --- | --- |
| NAND array、物理页、FTL、GC/WL、映射 I/O | MQSim | 保留原策略；不在外部叠加“SSD 固定延迟” |
| ONFI channel、chip/die 调度和 data-out | MQSim | 观测状态和事务，不再按带宽额外扣一次相同数据传输 |
| MQSim 数据缓存/DRAM、映射 CMT | MQSim | 只报告实际建模访问；cache 关闭不代表设备没有全部 DRAM，也不补造通用 DRAM 竞争 |
| 近存输入/query/结果缓冲 | 新增 BufferModel | 独立于 MQSim cache/CMT；显式容量、staging 带宽、读出成本；不声称共享盘控 DRAM 竞争 |
| 近存计算单元 | 新增 ComputeModel | 任务 slots、服务时间/启动间隔、队列和工作量 |
| 主机距离计算与候选控制 CPU | 新增 HostCpuModel | 默认共享有限服务 slots；控制成本按比较数/候选数等配置，真实 C++ 耗时不计入模拟 |
| H2D/D2H 链路 | MQSim PCIe_Link | 普通读、query、近存命令和结果共享队列；SST SelfLink 不重复收费 |
| 额外端点固定处理时间 | 平台端点模型（默认 0，允许显式配置） | 仅描述 MQSim 链路公式未覆盖的阶段；不得再叠加相同序列化成本 |
| 真实数据的 mmap/内存、功能函数墙钟 | 宿主机执行 | 只记仿真效率；不当成目标主机/SSD 的模拟服务时间 |

### 7.2 服务与背压

首版计算默认每 slot 非流水、FIFO 服务：service_ns = launch_ns + ceil(work_units / rate_units_per_ns)。slots 决定并行任务数；吞吐由 slots/service 导出，不能再叠加一个互相矛盾的独立总吞吐限制。如启用流水模式，单独定义 initiation_interval 和完成 latency，另做验收；默认不实现。

主机控制服务使用固定 setup + 明确计数项（候选检查、去重/排序比较等）。计数来自执行过程；存在提前退出/多阶段依赖时拆成任务。主机距离算子的 rate 默认表示含数据加载的综合服务能力，不单独宣称建模主机 DRAM 带宽/缓存层次；若后续独立拆内存服务，必须防止与综合 rate 重复计费。HostMemory 的向量/邻接缓存复用默认关闭，应用算法保留的候选状态与已算得分例外并列明。

盘控一次工作块先预留保守上界 credit：输入 sector 覆盖量 + 输出上界 + scratch；query credit 在整个查询期间保持。预留在存储提交前完成，防止读出后无处存放。staging 只对新增近存缓冲服务计费。计算结束释放输入/scratch；返回传输完成释放结果；查询完成释放 query。错误走同样的释放规则。

确定性分块由配置的 chunk 上限和单查询可容纳量推导，不能按瞬时空闲量改变算法批次语义。多个 chunk 计算/传输可重叠，但算法仍等待完整语义批次后更新。buffer 小于一个 query 加最小工作块时启动即报错；仅暂时占满时 FIFO 等待。并发 query 接纳同时受 max_inflight 和 query-buffer credit 约束，不能让全部容量被 query 占满而没有工作块空间。

## 8. 观测能力矩阵与输出

| 指标 | 源码已有基础 | V0.1 计划与可信口径 |
| --- | --- | --- |
| 设备/流请求数、完成时间 | User_Request、IO_Flow 统计及 NVMe 完成路径 | 增加平台 query/task 关联和逐请求提交/服务/传输完成事件 |
| 请求到事务拆分、LPA/PPA | transaction 列表、物理地址、Source 字段 | 分配稳定 TransactionId；映射/GC 分开；PPA 仅研究日志可见 |
| channel 忙闲 | ONFI_Channel_Base::SetStatus；有 channel idle 信号 [Q15] | 扩展状态转移事件，busy 表示总线占用，不等同 NAND array 忙 |
| chip 状态 | PHY 的 chip bookkeeping、chip idle/ready 信号 | 可导出辅助调试信息，不能替代 die 指标 |
| die array 忙闲 | Flash_Chip 的 start/finish、Die::Status、suspend/resume [Q16] | 增加开始/结束/暂停/恢复事件，带 channel/chip/die 和 command/事务 ID 集合 |
| die data-wait/transfer | PHY 有命令与 data-out 阶段 | 单独列阶段，避免将 array busy 和 controller occupied 混为一个利用率 |
| plane/latch 内部计算/占用 | 有些 plane 地址/计数不等于具备 latch 模型 | plane 精细时序、latch 占用/计算均 Unsupported；不输出伪零 |
| 缓冲、计算、PCIe 流量 | 新增资源及现有链路队列 | 从同一事件流累加，payload/wire 分列，峰值和排队可复算 |
| 能耗、绝对硬件校准 | 本轮没有依据 | 不支持，不提供看似精确的数值 |

事件字段至少为 time_ns、delta、sequence、event_kind、QueryId/TaskId/RequestId（可空且有原因）、实体 ID、bytes、source、状态前后值和 causal_parent。channel/die 的共享命令可关联多个事务，统计利用率按时间区间并集计算，不能按参与请求数重复累加。

利用率窗口统一为测量起止 [first_admitted_query, last_query_visible]；区间在窗口边界裁剪。每个实体 busy_union_ns / window_ns；多实体汇总明确分母。后台活动在窗口内计入设备忙时，按 source 单独列量。

机器可读交付建议：resolved_config.json、manifest.json、layout.json、results.json、queries.csv、resources.csv；debug 时可选 events.jsonl。日志默认关闭，但统计始终可用。manifest 包括数据/索引/query/ground-truth hash、种子、三方 commit/补丁 hash、编译器、依赖、参数来源、初态与预热方式、数值语义和模型能力。

## 9. M0–M4 任务拆解与依赖

相对工作量定义：S 为局部文档/封装，M 为单模块实现与验证，L 为跨模块集成，XL 为核心语义改造并需基线证明。它们是排序尺度，不是工时，也不应相加转换为日期。估计假设：单 SSD、单 rank/线程、简化图算法、一个默认距离算子、无共享 DRAM 精细模型。

| 任务 | 依赖 | 可评审交付物 | 验收/退出条件 | 规模 |
| --- | --- | --- | --- | --- |
| M0.1 源码与版本核查 | PRD | 本文第 2 节、完整 commit、改动清单 | 本轮已完成静态核查；运行结论保持未验证 | M |
| M0.2 本机 macOS 工具链/原生基线 | M0.1 | 已完成工具链与锁文件、SST 和原生 MQSim 构建、小读输入/输出；见 [M0.2 证据](evidence/m0.2/README.md) | 本机 arm64 构建、命令入口和两次小读验收通过；资源、初态、RSS/墙钟已记录；不含元素加载 | M |
| M0.3 事件与本地路径先行验证 | M0.2 | 第 11 节 E1/E2 的最小 PoC、事件顺序和路径账本 | 精确插入外部事件；本地读取无向量主机回传；不满足则先修订桥/路径设计 | L，风险最高 |
| M0.4 冻结接口/默认/能力 | M0.3 | 设计决定记录、resource owner、wire schema、能力矩阵 | 所有计费有唯一 owner；数据路径和完成边界可手工复核 | M |
| M1.1 库化与生命周期 | M0.2 | MQSim PIC 库、保留的原生 oracle、SST 元素骨架 | 同进程只建一个设备，初始化/结束不挂交互输入 | M |
| M1.2 精确事件桥 | M0.3、M1.1 | 初始化/peek/dispatch/time sync、稳定同刻调度、过期唤醒处理 | AC-08 基线；同刻新增/取消/更早插入覆盖 | XL |
| M1.3 外部普通 I/O + 初态 | M1.2 | 外部 flow、NVMe 请求/数据/CQ 关联、确定性预装载 | 单读、跨页、重叠/排队读保持原生完成行为；初态可重复 | L |
| M1.4 基础观测 | M1.3 | 请求、channel、die 事件钩子和核对脚本 | AC-09 基础；命令/array/data-out 语义可区分 | M–L |
| M2.1 内容、布局和 handles | M1.3 | ContentStore、LBA 映射、驻留/引用/错误契约 | 跨页/无效地址/句柄；真实内容不因物理映射改变 | M |
| M2.2 异步依赖与可见性 | M2.1 | 任务状态机、when_all、私有结果槽、失败传播、死锁诊断 | AC-03、AC-04；独立查询持续推进 | L |
| M2.3 功能与成本分离 | M2.2 | FP32 输入/FP64 L2、工作量、CPU 服务模型和简单动态分支例 | AC-02、AC-10 的基础；没有墙钟计费 | M |
| M3.1 图搜索和离线真值 | M2.3 | 固定种子数据、图、查询、独立精确搜索、算法伪码、主机模式 | AC-01 主机；Recall 定义可复核，动态搜索不是固定 trace | M |
| M3.2 本地读取与共享传输 | M0.3、M1.3、M2.2 | 本地 request route、聚合完成、query/命令/结果 PCIe 封装 | AC-06；普通 NVMe 基线持续通过 | XL |
| M3.3 盘控计算/缓冲 | M3.2、M2.3 | ComputeModel、credit/staging/释放、确定性 chunk、盘控模式 | AC-05、AC-07；过大请求明确拒绝 | L |
| M3.4 两种放置端到端 | M3.1、M3.3 | host/controller 成对配置、逐批决策与流量对照 | AC-01、AC-02、AC-05、AC-06 全部通过 | M |
| M4.1 并发与实验输出 | M3.4、M1.4 | 有限并发、完整配置快照、结果/统计/能力文件、日志开关 | 延迟/吞吐分位数有样本数；AC-09 完整 | M |
| M4.2 参数/复现回归 | M4.1 | AC 矩阵运行器、受控计算/带宽/缓存实验、原生回归 | AC-10、AC-11 与 AC-01～AC-09 回归 | L |
| M4.3 文档与交付 | M4.2 | 一条运行命令、扩展指南、许可证/补丁清单、限制/验证记录 | 干净环境复建复跑；全部 AC 有可追溯证据 | M |

依赖主链：M0 风险验证 → M1 可靠存储与时间 → M2 数值因果闭环 → M3 两种路径与资源 → M4 并发、实验与交付。M0.3 是进入主体实现后的首个小实验，不代表本轮已经进行编码。开发可临时用假后端验证运行时，但 M1/M3 的退出必须使用 MQSim。

**规模重点**：事件桥、本地 route/生命周期/传输是两项 XL；运行时与缓冲为 L；示例算法与数据通常为 M。完整 DiskANN 接入会新增至少一个独立的 L–XL 范围，并扩大数值与搜索语义验证；默认不纳入。若 E1/E2 揭示必须大改调度或真实内容存储，需重新估计，不能用当前尺度保证工期。

## 10. 可重复验收方案

所有验收在固定 commit、补丁、工具链和 seed 下执行；通过记录为输入/hash、配置快照、命令、结果、断言和必要事件片段。下面都是将来必须运行的实验，本轮没有把任何 AC 标为已通过。

| AC | 可重复输入与操作 | 必须观察到的行为 |
| --- | --- | --- |
| AC-01 最小端到端 | 固定种子小图，k=10；同一命令入口分别选 host/controller | 两次均输出 top-k/距离/Recall、模拟延迟、配置/版本；至少一个查询，随后覆盖有限并发 |
| AC-02 数值决定访问 | 专用无同分图：0 邻接 1、2；1→3、2→4，B=1。q=0，x1=1、x2=3；另一输入只把 x1 改为 5 | 第二轮扩展从 1 改为 2，邻接读取 LBA 从 adj[1] 改为 adj[2]；先验证决策变化再验证地址；两种放置均成立 |
| AC-03 时延因果 | 隔离链 read→compute→return→decision，另放一个不共享受扰资源的独立查询 | 分别增加受控阶段 Δ（例如 100 ns），后继按预期推迟；独立查询继续；MQSim 存储链另用单个 cache miss 调 NAND 参数验证 |
| AC-04 禁止提前可见 | 极快功能函数+长模拟服务/返回；在输入就绪、服务结束前、返回结束前布置探针 | 功能结果暂存仍不可 try_get；依赖决策只在主机 Visible 后发起，未完成结果/无效句柄有诊断 |
| AC-05 放置一致 | 一组固定图/查询、批次等齐策略、相同数值实现 | 每批候选/扩展 ID 和最终 top-k 相同；距离满足容差；时序差异不改变默认决策 |
| AC-06 本地路径 | 一个已知 b、d、degree 的 cache-off 批次，导出消息账本 | 盘控 vector D2H payload 为 0；query/IDs/结果/邻接及控制消息全被计入；payload、wire、请求/事务账可逐项核对 |
| AC-07 容量/竞争 | 设置只能容纳一个工作块的近存 buffer，再发两个可完成块；另测 slots=1 的计算竞争 | 峰值不超容量，有正等待且释放后前进；单块过大立即报错；服务次序/排队满足模型，失败后无 credit 泄漏 |
| AC-08 MQSim 基线 | 同一受控 I/O trace/初始化：QD1、多个并发、同刻请求、跨页、CMT miss；原生与桥接都关闭 NDP | 逐请求 LBA、拆分、完成顺序/时间、请求数吻合；默认 ns 用例要求时间完全一致。改变取整规则的试验须单独声明且有逐事件误差界 |
| AC-09 观测可信 | 小拓扑已知读序列，启用状态日志；使用不依赖统计累计代码的离线核对器 | 请求数/流量守恒；channel 与 die 区间并集重算一致；每事件有时间/实体 ID；plane/latch 不支持状态明确 |
| AC-10 可复现 | 同一输入重复运行；将功能执行改为更慢但数值相同的参考实现/加入仅墙钟延缓的测试变体 | 去掉 run_id/墙钟字段后的结果与模拟事件稳定；模拟服务时间不因真实执行变慢而变动；仅仿真墙钟变化 |
| AC-11 参数因果 | 输入预就绪的计算任务扫 rate；预就绪固定载荷传输扫带宽；各自无其他瓶颈 | 服务时间符合配置公式/取整；带宽影响链路服务/排队；复杂 ANNS 只解释瓶颈，不强制所有趋势单调 |

AC-02 夹具使用独立 sector 的邻接记录，避免虽然节点变化却读到同一 LBA；entry 固定初始化，下一轮观测针对邻接读取，不依赖布局巧合。例子可设 x0=10、x3=2、x4=4 补全一维向量，L≥2、扩展预算覆盖第二轮；修改前后所有距离均无同分歧义。

独立精确 ground truth：对所有向量穷举 FP64 L2，按同样 ID 同分规则选 k；Recall@k = 近似 ID 集合与真值 ID 集合交集大小 / k，数据集须满足 N≥k。算法可达性/Recall 降低应报告，不设置 Recall=1 的产品门槛。另用易手算向量验证距离算子，避免 ground truth 与被测错误互相掩盖。

AC-08 的固定 trace 是存储适配 oracle；AC-02 和端到端示例仍由真实结果动态发起访问。两者不可相互替代。

## 11. 关键风险、先行实验与推荐决定

| 风险 | 影响 | 先行实验/降低办法 | 决策门槛 |
| --- | --- | --- | --- |
| R1 事件桥越过外部事件/同刻顺序错误 | 闭环因果失真，所有性能结论不可信 | E1：安排 MQ 事件 10/30 ns，在 20 ns 注入请求并产生更早完成；再测同刻新增/Ignore/过期唤醒 | 事件绝不越过 SST now；AC-08 基线通过才扩展规模 |
| R2 近存路径仍回传完整向量或错误汇总 | 重复计费/提早计算/悬垂指针 | E2：跨页两事务+可控乱序完成；验证仅最后子事务就绪，vector D2H=0，普通 route 仍一致 | 本地/普通 route 有独立且可复核的完成与清理；失败则修路径，不减去已发生延迟伪装近存 |
| R3 缓存命中与请求删除时机 | cache/PHY 多观察者访问已删除对象，或重复完成 | E2 扩展：cache-off 先行，然后支持的缓存配置对照；在单一分派点路由并复制稳定 ID | 一次完成/一次释放，覆盖混合命中与错误路径；未验缓存配置必须拒绝或标限制 |
| R4 传输双计或缺漏 | 近存比较存在系统偏置 | E3：普通 read 与 query/result 混发，核对共享队列、payload/wire、CQ/消息头 | 每字节有唯一账本来源，跨模式同一链路争用 |
| R5 近存缓冲形成死锁 | 并发不能完成或超容 | E4：query 常驻+仅一块工作空间，多查询、多 chunk、失败注入 | 保留可前进空间、过大请求失败、峰值和释放守恒 |
| R6 逻辑内容绕过驻留/可见性 | 真实数值存在但数据路径不真实 | handle 权限与 AC-04；功能执行器独占 ContentStore 数据视图 | 算法不可直接取任意向量；每次有效化都有生产任务/事件 |
| R7 初态/映射/CMT/后台活动混淆放置收益 | 两模式比较不公平 | E5：确定性预装载，比较测量起点映射/缓存摘要、种子及初态；首次读在线映射单独标注 | 两模式起点相同；未知初态不纳入收益结论 |
| R8 源码静态全局、工具链和库化 | 多实例污染/构建或退出失败 | M0.2 本机 macOS 原生构建；单设备/每实验新进程；SST16 C++17 PIC 与模块加载验证 | 最小构建和 oracle 可复现；不承诺同进程多次 Reset 完整隔离 |
| R9 die/channel 指标命名与模型精度不符 | 利用率或可观测能力被夸大 | 明确定义 array、bus、wait；多事务命令用参与集合，统计按区间并集 | 事件可独立重算，不用 chip 或累计相加替代 |
| R10 算法扩张/未校准参数 | 延迟交付或得出绝对预测 | 简化图、单距离、参数 sweep；所有参数标注 configured/unvalidated 或来源 | 平台正确性先通过；不设“盘控更快”门槛 |

### 建议默认与待核实项的处置

| 项目 | 推荐决定 | 尚待验证的内容 |
| --- | --- | --- |
| 语言/异步形式 | C++17，显式状态机+continuation token | 与 SST16 头文件/编译选项实际构建兼容 |
| SST/MQSim 版本 | 使用第 2 节已初始化子模块及固定 commit，SST Core 无补丁 | 本机 macOS / arm64 上构建与桥接基线 |
| 首算法/数值 | 简化确定性图，FP32 输入/FP64 平方 L2 | Recall/扩展预算在示例数据上的实测表现 |
| 搜索控制 | 主机；邻接/候选在主机决策，距离可放盘控 | 命令格式和每个控制阶段的参数量级 |
| 缓存 | 首个可交付配置采用 SIMPLE + TURNED_OFF 数据缓存；保留 CMT | 合法参数组合和真实初始化行为；ADVANCED/读缓存不作为默认已支持能力 |
| 传输 | MQSim PCIe_Link 唯一计费，增加平台消息分派 | 分包字节、端点序列化/回调边界对照 |
| 近存缓冲 | 独立容量/带宽模型，包含 query/输入/结果/scratch | 参数未校准，不代表与 MQSim DRAM 共享竞争 |
| 实验初态 | 测量前确定性预装载布局 LBA，数据缓存关，记录映射/CMT/后台状态 | 预装载完成判定、统计窗口切换；未校准为稳态 SSD |
| 前端/结果 | JSON 配置，保留原始 MQSim XML；JSON/CSV 结果，可选 JSONL 日志 | 配置 schema、路径和失败诊断的可用性 |
| 查询提交 | 默认按 query 文件顺序、max_inflight=1；可设正整数上限，前一查询完成后接纳下一条 | 并发 1/2/4 验证；首次 smoke 的 E=64、评分 chunk 上限 16，记录实际展开数与 chunk 规则 |

目前没有必须向用户确认后才能完成本规划的问题。只有将目标改为忠实复现 DiskANN、共享盘控 DRAM、SSD 自主搜索，或已有指定硬件校准对象，才需要重新确定范围。其余常规实现选择按上述推荐推进设计即可。

## 12. 目录、构建、运行与版本管理建议

docs、AGENTS.md、.gitmodules、third_party/MQSim 和 third_party/sst-core 已存在；两个 third_party 目录是已初始化的 submodule。M0.2 已增加 tools/macos_m0_2.py、configs/native 和 deps.lock.json；下列平台实现布局及端到端构建/运行命令仍是交付规划，不作为当前可运行入口：

    CMakeLists.txt
    deps.lock.json
    cmake/                       # SST 发现、编译标志与依赖检查
    third_party/MQSim/           # 已初始化：abcyzj/MQSim fork，主仓固定 commit
    third_party/sst-core/        # 已初始化：SST 上游固定 commit，不修改
    include/ndpsim/              # 公开 handles、operation、layout、observer 契约
    src/sst/                    # System Component、事件、ELI 注册
    src/runtime/                # 依赖、任务状态、结果门控
    src/data/                   # ContentStore、布局、驻留
    src/backend/mqsim/          # 引擎桥、外部 flow、本地 route、传输适配
    src/model/                  # 主机 CPU、盘控计算、独立 buffer
    src/operators/              # 功能算子、工作量描述
    src/algorithms/             # GraphSearchExample 状态机
    src/observability/          # 能力、事件、统计
    configs/                    # smoke、host/controller、微基准和上游 XML
    scripts/system.py           # 只做 SST 配置接线
    tools/                      # 数据/图/真值准备、结果核对、运行入口
    tests/fixtures/             # 因果分支、跨页、容量等受控输入
    tests/acceptance/            # AC 对应场景与 oracle
    docs/                       # 接口、模型、补丁维护、验收记录
    results/                    # 按 run_id 输出，默认不入版本库

目标环境改为**本机 macOS（Apple Silicon / arm64）**。2026-10-27 开始实施前的只读盘点结果如下，保留为历史；最终工具链与通过证据见本节后文及 [M0.2 记录](m0.2-native-baseline.md)：

| 项目 | 已发现的信息 | M0.2 尚需验证或补齐 |
| --- | --- | --- |
| 系统与架构 | macOS 26.5.1，build 25F80；uname -sm 为 Darwin arm64 | 可用 CPU/内存、构建与运行资源记录 |
| C++ 工具链 | Xcode Command Line Tools；Apple Clang 21.0.0，目标 arm64-apple-darwin25.5.0 | M0.2 验证 C++17、MQSim 兼容性及 SST 编译；元素模块加载由 M1.1 验证 |
| CMake | 已发现 CMake 4.4.4 | 验证所锁定 SST 的导出配置与平台包装在该版本可用 |
| Python | PATH 默认 /usr/bin/python3 为 3.9.6；另有 arm64 Miniconda Python 3.13.12，PATH 的 python3-config 来自该 Miniconda 前缀 | 建议先以 Miniconda Python 3.13.12 及同前缀 python3-config 配套验证；不得把系统解释器与另一套开发头文件/库混用 |
| Autotools / GNU Libtool | PATH 未发现 autoconf、automake、glibtoolize、glibtool；/usr/bin/libtool 是系统工具 | 补齐 SST autogen.sh 使用的 Autoconf、Automake、GNU Libtool；系统 libtool 不能替代完整 GNU Libtool 工具链 |
| SST 运行入口 | PATH 未发现 sst | M0.2 从已固定的 third_party/sst-core 构建安装并验证命令入口；元素模块加载由 M1.1 验证 |

盘点命令包括 `sw_vers`、`uname -sm`、`xcrun --find clang++`、`clang++ --version`、`cmake --version`、`python3 --version`、`command -v` 以及 `python3-config --prefix/--includes/--ldflags --embed`；均未执行编译、安装或仿真。

所锁定 SST 的 configure.ac 要求 C++17；其 [Python 版本检查][S6]要求 Python >= 3.9。已发现的 Python 版本满足这一数值门槛，但 Python 头文件、嵌入链接及 SST API 兼容性仍需实际验证。M0.2 锁定最终选用的解释器、python3-config、前缀和版本；若现有候选构建失败，再依据错误选择并记录兼容版本。本机候选为 Apple Clang 21、CMake 4.4.4、配套的 Miniconda Python 3.13.12；后续平台 CMake 最低版本约定仍为 3.22，不代表当前 CMake 4 已通过验证。

平台不依赖 sst-elements；只需 SST Core。SST 保留固定源码的 [Autotools 引导流程][S8]，单机可禁用 MPI；不将它直接作为普通 CMake 子目录构建。平台 CMake 优先使用 SST 安装后的 [SSTConfig.cmake 导出接口][S7]，结合 sst-config 核对实际 flags，构建 SST 可加载的 MODULE；使用其 Darwin 链接规则，不硬编码 Linux 模块后缀或链接参数。该导出接口仅声明 cxx_std_11，平台必须显式要求 C++17。SST、MQSim 库与平台元素库统一使用 arm64、Apple Clang、相同标准库及 C++ ABI，不混入 x86_64 构建产物。MQSim 由平台 CMake 包装库目标并保留原生 oracle，排除 main.cpp；具体兼容修正属于后续开发。目标环境的构建、原生基线和桥接验证通过前，不宣称该组合已兼容。

预期构建与运行形态：

    cmake -S . -B build -DSST_ROOT="$PWD/build/deps/sst" -DCMAKE_BUILD_TYPE=Release
    cmake --build build -j2
    python3 tools/run.py --config configs/host.json --output results/host
    python3 tools/run.py --config configs/controller.json --output results/controller
    ctest --test-dir build --output-on-failure -L acceptance

run.py 只校验配置、准备固定种子 smoke 数据和设置元素库路径，再调用 sst scripts/system.py；它不是算法性能模型。最小端到端命令应自动校验数据 hash，输出实际展开的 sst 命令。首次数据/图/真值准备与正式仿真墙钟分开计量。上面的 run.py/端到端形态仍为规划；当前已存在的根 CMake 构建、CTest 和 run_lifecycle 入口见 [README](../README.md)。默认 SST 私有前缀为当前构建目录的 _deps/sst-install，不依赖 M0.2 的历史安装。

源码依赖已采用 Git submodule：.gitmodules 保存 URL/路径，主仓 gitlink 锁定完整 commit。新检出使用 `git submodule update --init --recursive` 获取主仓记录的版本，不能使用 --remote 自动跟随分支。M0.2 已实现 [deps.lock.json](../deps.lock.json)，记录相同 URL/commit、空补丁哈希、工具链和配置，并由运行脚本校验其与 gitlink 一致。MQSim 改动直接修改 third_party/MQSim 并直接编译该目录，不维护构建时 patch 栈或另一个集成源码副本。提交时先保存到 abcyzj/MQSim fork 的完整 commit，再更新主仓 gitlink；未提交状态记录 HEAD、相对原生基线的 diff 和文件哈希，不自动提交或推送。SST Core 固定且不修改；未修改原生 oracle 由锁定的原生 commit 独立导出构建，详见 [源码维护约定](mqsim-source-management.md)。DiskANN 仅留核查记录，不加入默认依赖。

保留 SST、MQSim 和将来实际复用代码的许可证与版权文件；每组依赖源码改动解释目的、默认开关、基线影响和验证，不在未实现阶段宣称已能长期无冲突升级。

### 最小规模与参数来源

建议 smoke 从 N=2048、d=32、最大邻居数 R=16、32 个 query、k=10、L=32、B=1、seed=42 起步；图生成加入固定环边保证连通，再加确定性邻居。它是平台测试输入，不作为算法性能优势证明。

原始向量内容为 2048×32×4 = 262144 B；采用默认 sector stride 后向量区约 1 MiB，邻接区约 1 MiB。它们不包含 MQSim 自身对象、映射/flash metadata 和事件队列内存，不能用这些大小推断总 RSS。

tiny SSD 几何可从 2 channel × 1 chip/channel × 2 die/chip × 1 plane/die × 64 block/plane × 64 page/block × 4096 B/page 起步（64 MiB 原始容量），这是人工缩小的功能模型配置，必须在 M0.2 验证参数约束；AC-08 原生与桥接使用相同配置。另保留原生 ssdconfig.xml 作为单独配置，不悄悄将其大几何与 tiny 几何结果混比。

计算/控制 launch_ns、rate、slots、buffer_bytes、staging_bytes_per_ns、链路 lane bandwidth/count/packet 参数全部带单位与 source。第一套验证用参数允许人工设定并标为 configured_unvalidated；真实硬件比较另选有来源的配置。链路必须注明十进制 GB/s 与 B/ns 换算，避免 GiB/s 混用。不开工前伪填“校准吞吐”或“预计加速倍数”。

M0.2 已测原生小 I/O 的墙钟与 peak RSS；桥接小 I/O 在 M1、单查询及 32 查询和 max_inflight=1/2/4 在相应运行时任务中测量；依据本机可用 CPU/内存决定是否增大 N、d 和查询数。报告每个分位数的样本量，小样本 p99 不能当稳定尾延迟。大规模实验不作为 V0.1 交付前置条件。

### M0.2 已验证的本机入口（2026-10-27）

[运行与限制说明](m0.2-native-baseline.md)、[完整证据](evidence/m0.2/README.md) 和 [锁文件](../deps.lock.json) 为当前依据。Apple Clang 21 / arm64 / libc++ / C++17、SDK 26.5、配套 Python 3.13.12 已实际编译、链接及运行；CMake 4.4.4 已导入 SST 配置，但未验证元素 MODULE。Autotools 已补齐；SST 和 MQSim 的子模块 commit、gitlink、已跟踪源码及空补丁哈希保持一致。

当前原生构建及小读入口为 `python3.13 tools/macos_m0_2.py --output <新目录>`（解释器绝对路径见运行说明）。脚本独立构建固定源码快照，显式选择 SDK、Python 库路径及 rpath；MQSim 单独使用 `-Xclang -mconstructor-aliases` 避免原生抽象基类完整析构入口 trap。源码未修改，非虚析构的 C++ 未定义行为仍未修复，不能据此声称生命周期已验证。默认安装前缀为 build/native-macos/install/sst，SST 的实际帮助入口为 `--usage` 和 `--help=timebase`，裸 `--help` 缺少参数。

受控 tiny 几何已完成原生运行。初态为新进程、Reset、关闭 preconditioning、初始占用 0、SIMPLE/TURNED_OFF、保留 CMT，READ 懒建映射；不是 M1.3 的预装载/测量窗口实现。两次进程各完成 4 个小读、13312 B、零应用写入，退出 0，XML 完全相同。墙钟、RSS、输入和二进制哈希均已归档。未运行桥接、元素加载或 AC 验收。

## 13. 已完成与下一步门槛

原规划完成：PRD 全文阅读、SST/MQSim/DiskANN C++ 固定提交静态源码核查、架构/路径/接口/资源设计、任务依赖、风险实验与 AC 映射。2026-10-27 已补充两个依赖 submodule 的路径、URL、固定 commit 与本机 macOS 工具链盘点；M0.2 现已完成本机环境、SST 命令入口和原生小读验收，见 [证据索引](evidence/m0.2/README.md)。

尚未完成、也未声称通过：SST 平台元素模块加载、事件桥 PoC、本地路径 PoC、任何 AC 仿真测试、资源参数校准及 ANNS 运行规模测量。M0.2 未启动 M1.1；后续按依赖推进，以 E1/E2 作为主体实现的准入条件。

## 源码永久链接

链接对应本次实际下载并阅读的提交。正文中的入口与行号以这些固定版本为准，在线滚动文档只作框架概念补充。

[S0]: https://github.com/sstsimulator/sst-core/releases/tag/v16.0.0_Final
[S1]: https://github.com/sstsimulator/sst-core/blob/ffd71527bc26a1f2a4fb685fdeb2ca0799997d78/configure.ac#L2
[S2]: https://github.com/sstsimulator/sst-core/blob/ffd71527bc26a1f2a4fb685fdeb2ca0799997d78/src/sst/core/baseComponent.h#L425
[S3]: https://github.com/sstsimulator/sst-core/blob/ffd71527bc26a1f2a4fb685fdeb2ca0799997d78/src/sst/core/link.cc#L623
[S4]: https://github.com/sstsimulator/sst-core/blob/ffd71527bc26a1f2a4fb685fdeb2ca0799997d78/src/sst/core/activity.h#L63
[S5]: https://github.com/sstsimulator/sst-core/blob/ffd71527bc26a1f2a4fb685fdeb2ca0799997d78/src/sst/core/baseComponent.h#L1096
[S6]: https://github.com/sstsimulator/sst-core/blob/ffd71527bc26a1f2a4fb685fdeb2ca0799997d78/config/sst_check_python.m4#L48
[S7]: https://github.com/sstsimulator/sst-core/blob/ffd71527bc26a1f2a4fb685fdeb2ca0799997d78/share/SSTConfig.cmake.in#L1
[S8]: https://github.com/sstsimulator/sst-core/blob/ffd71527bc26a1f2a4fb685fdeb2ca0799997d78/autogen.sh#L1
[Q1]: https://github.com/CMU-SAFARI/MQSim/blob/51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16/src/main.cpp#L260
[Q2]: https://github.com/CMU-SAFARI/MQSim/blob/51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16/src/sim/Engine.cpp#L56
[Q3]: https://github.com/CMU-SAFARI/MQSim/blob/51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16/src/sim/EventTree.cpp#L243
[Q4]: https://github.com/CMU-SAFARI/MQSim/blob/51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16/src/sim/Sim_Defs.h#L8
[Q5]: https://github.com/CMU-SAFARI/MQSim/blob/51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16/src/ssd/Host_Interface_NVMe.cpp#L98
[Q6]: https://github.com/CMU-SAFARI/MQSim/blob/51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16/src/ssd/NVM_PHY_ONFI_NVDDR2.cpp#L325
[Q7]: https://github.com/CMU-SAFARI/MQSim/blob/51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16/src/ssd/Data_Cache_Manager_Flash_Simple.cpp#L214
[Q8]: https://github.com/CMU-SAFARI/MQSim/blob/51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16/src/nvm_chip/flash_memory/Page.h#L11
[Q9]: https://github.com/CMU-SAFARI/MQSim/blob/51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16/src/host/PCIe_Root_Complex.cpp#L9
[Q10]: https://github.com/CMU-SAFARI/MQSim/blob/51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16/src/host/PCIe_Link.cpp#L28
[Q11]: https://github.com/CMU-SAFARI/MQSim/blob/51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16/src/host/PCIe_Link.h#L44
[Q12]: https://github.com/CMU-SAFARI/MQSim/blob/51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16/src/ssd/Address_Mapping_Unit_Page_Level.cpp#L584
[Q13]: https://github.com/CMU-SAFARI/MQSim/blob/51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16/src/exec/Host_System.cpp#L114
[Q14]: https://github.com/CMU-SAFARI/MQSim/blob/51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16/Makefile
[Q15]: https://github.com/CMU-SAFARI/MQSim/blob/51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16/src/ssd/ONFI_Channel_Base.h#L23
[Q16]: https://github.com/CMU-SAFARI/MQSim/blob/51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16/src/nvm_chip/flash_memory/Flash_Chip.cpp#L102
[Q17]: https://github.com/CMU-SAFARI/MQSim/blob/51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16/src/host/IO_Flow_Base.h#L42
[D1]: https://github.com/microsoft/DiskANN/blob/78256bbab4685e1774e78d331e081a153be26823/src/pq_flash_index.cpp#L1268
[D2]: https://github.com/microsoft/DiskANN/blob/78256bbab4685e1774e78d331e081a153be26823/src/linux_aligned_file_reader.cpp#L31
[D3]: https://github.com/microsoft/DiskANN/blob/78256bbab4685e1774e78d331e081a153be26823/CMakeLists.txt#L131
