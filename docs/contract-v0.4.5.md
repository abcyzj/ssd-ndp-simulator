# P01 契约与风险设计冻结

版本：`v045-contract/1`；2026-11-06（Asia/Shanghai）。需求依据为 [PRD FR01/03～09](prd-v0.4.5.md)及[实施计划 §3/P01](implementation-plan-v0.4.5.md)。本页、schema、阶段目录、头文件设计和风险夹具共同冻结；修改不兼容字段必须新格式版本并重验受影响出口。**这是设计冻结，不是 SDK、通用包校验器或生产执行器实现，也不关闭 AC。** P02/P05/P07～P13 才分别实现相应入口。

P00 的 `run1/manifest.json` 与 `final1/manifest.json` 及其逐文件 hash 已在本次依赖核验中重新检查。P00 缺失的完整原 NSG/prepared/历史验收实体继续登记，不影响元数据契约设计；它们仍阻塞正式采集/性能验收。当前可运行版本仍为 V0.4。

后续状态：[P02通用SDK与包校验](capture-sdk-v0.4.5.md)已完成；下文的P01状态/检查数字保留冻结时快照，生产执行器及其他后续出口仍未实现。

## 1. 版本、边界和组成

| 契约 | 本次冻结产物 | 后续实现 |
| --- | --- | --- |
| 通用共享类型 | [contract/2 schema](../schemas/ndpsim-contract-v2.schema.json) 的 `$defs` | P02 公共类型和校验 |
| `ndpsim-static-ir/2` | [manifest schema](../schemas/ndpsim-static-ir-v2.schema.json)，共享 operation/stage/index/tail/object/port/operator_spec | P02 writer/校验，P04 图采集 |
| `ndpsim-measurement/2` | [Measurement schema](../schemas/ndpsim-measurement-v2.schema.json) | P04/P06 |
| `ndpsim-cost-profile/1` | [Profile schema](../schemas/ndpsim-cost-profile-v1.schema.json)，预测与实测表分支 | P05/P06 |
| `ndpsim-effective-config/2` | [配置绑定 schema](../schemas/ndpsim-effective-config-v2.schema.json) | P09/P10/P14 |
| C++17 接口 | [interfaces.h](design-v0.4.5/interfaces.h)，只含标准库的声明 | P02/P05/P07 分别迁入实际模块 |
| 图扩展 | [阶段目录与归属](../examples/p01-contract/graph-stage-catalog.json)、[多阶段操作样例](../examples/p01-contract/graph-stages.json) | P04/P13 |
| 非图表达及手算 | [块求和算子](../examples/p01-contract/sum-operator.json)、[fork/join](../examples/p01-contract/fork-join.json)、[手算数据](../examples/p01-contract/hand-cases.json) | P17 完整教学链路 |

JSON Schema 为 Draft 2020-12。每个封闭记录拒绝未知字段；JSON 读取器另须拒绝重复键、非有限数、uint64 位置浮点写法、路径逃逸和溢出。业务 Work/特征使用 `{value:uint64, unit:string}`，名称/单位集合由注册的算子阶段定义，公共层没有 INIT/SELECT、SIFT、固定七项 Work 或图循环白名单。扩展单位必须注册，禁止运行时猜单位。

Schema 描述单记录结构；跨记录引用、哈希/计数、required 端口、模型适用性等依下文实施，不宣称 JSON Schema 单独证明它们。夹具中的零 hash 是**合成占位身份**，只演示类型，尚非可执行/已封装的新包，生产入口必须拒绝；本任务没有实现封包器或通用验证工具。

## 2. 包、拓扑和信任边界

包包含 `manifest.json`（对象目录、来源、查询原始ID/顺序、文件清单/预算）、`operations.jsonl`（语义操作及其多个阶段）、`index.jsonl`（逐阶段键/依赖/拓扑rank/定位/阶段摘要）、`tail.json`（完整计数、终态、丢失数=0、包ID）。对象可以为任意只读逻辑对象；manifest 保存大小、形状、类型、内容摘要和版本，不保存 payload 或原始 payload 路径。访问为 `(object, version, offset_bytes, length_bytes)`，允许空访问但仍校验 offset≤size，禁止越界及 offset+length 溢出。Layout 独立解析为 extent，不将 extent 写回逻辑身份。

稳定键是 `(query 原始ID, op 字符串ID, stage 字符串ID)`；segment 在 op 内按实际调用次序从0递增，chunk 标识**语义**块，可为 null。stage ID 不从物理块或采样时戳生成，Measurement/实测表复用同一键。一次 semantic op 可含 prepare、L2、append 等多 stage；`semantic_work` 保存独立核对的原算法计数，stage.work 保存该服务边界的需求，**不将所有 stage.work 盲目相加冒充语义 Work**。状态是查询私有不可变版本链，before/after及输入/输出端口显式声明（初始input=null、before=0）；每个 after 唯一产生且递增，消费者连至相应版本的生产阶段。拒绝跨查询依赖/共享可变状态/时延决定控制流/原地更新未版本化对象。

四种边都从某输出端口连到输入端口，输入/输出 type 和 unit 必须匹配算子声明：DATA 门控数据描述，ADDRESS 门控地址选择，CONTROL 门控分支选择，ORDER 门控执行顺序。所有边只在源 **Visible** 后满足；不能用 ServiceDone 代替返回到达。失败源使后继失败关闭，不发布成功令牌。每个required输入至少一条边；cardinality=one必须恰一条，many允许多个不同生产者（重复边仍拒绝），用于任意扇入join；optional可无边。输出端口各有唯一描述，禁止多个stage隐式覆盖同一版本。禁止未知算子/版本/端口/位置、重复端口/键/边、缺失required输入、环及不完整终态。图选择/循环合法性由图扩展独立核对，不由通用层推断。

`topological_rank` 是查询内唯一连续的逻辑拓扑序，所有边源rank小于目标rank；存在合法拓扑排序不意味着只按此序串行执行。物理操作行/分片和查询存放顺序可以重排，索引中的逻辑rank/键/依赖和字节位置须同步更新。index 每stage定位其父operation行，多个stage可共用offset/length，以stage ID定位；索引的依赖及规范化stage摘要必须与操作流逐项一致。索引不是绕过输入核验的可信副本。

预检顺序：单行有界扫描 → 对象/类型/单位 → 索引定位与完整覆盖（同operation的stage共享范围不计重复行）→ 拓扑/版本/端口 → 终态闭合（每个stage可达至少一个query terminal，terminal无后继）→ 文件/hash/实际计数/tail → 扩展语义报告。tail 须完整覆盖原始查询清单，每查询至少一个终态；缺失/截断/多余行/失配立即失败。生产者临时目录完成验证后原子发布；失败目录保留，不截断记录伪装成功。

规范化JSON：UTF-8、键按Unicode码点排序、无额外空白、整数十进制、`ensure_ascii=True`、LF结尾；对象字符串允许Unicode但规范编码转义。file hash 对实际原始字节；stage hash 对规范化stage记录。包ID为 SHA256(`ASCII ndpsim-static-ir/2` + NUL + manifest原始32-byte hash + index原始32-byte hash + operations原始32-byte hash)。tail 不参与包ID，避免自引用；manifest files 必须恰为 index/operations（及显式只读元数据分片，首版仅这两项），不得列自身、tail或外置语义报告。语义报告在封包后生成，单独绑定包ID，其hash由有效配置绑定，**不回写manifest制造hash环**。

P08 在接纳查询前验证完整查询的stage索引和依赖计数，在预算内保留其全部就绪信息，再按索引按需读详情。不以滑动文件窗口阻塞已就绪分支；Q增大只增加排队查询描述，不保留已完成的活跃图。每行/查询索引默认≤16MiB；配置声明 max active stages，接纳前用每查询索引大小与活动配额预算计算，超限拒绝，不用隐藏串行回退。实例字段不能放大部署上限。完成且owner/pin/在途回调/边均清零才回收；实际峰值及大Q梯度由P08实测。

身份分离：输入内容摘要、workload/semantic_version/numeric_version/work_version/semantic_config_sha256（含算法参数和逻辑chunk）、扩展语义，采集 source/binary/compiler/flags，模型测量来源/目标硬件，模拟器source/binary，当前执行宿主分别记录。换宿主不改变目标硬件，也不要求两者相等；改变语义/Work重采，改变模型上下文/采集构建重新测量或拒绝。结构报告和 independent-semantic 报告分列；hash仅证明完整性，模拟不冒称本次核验了原始内容/真实结果。正式图包必须有对应独立数值/搜索/逐stage Work报告。top-k只作外部参考，禁止称为模拟重算结果。

## 3. 图搜索阶段、特征和唯一计费

图语义仍是 `ndpsim-ranked-expansion-v1`、固定FP32输入/维序FP64 L2、禁fast-math/FMA、原容器及计数；逻辑chunk=16不能因设备容量变化而重定义。完整列名和来源在阶段目录；下表定义互斥计时边界。每次真实调用都采Work及额外特征，空stage也有记录/显式零服务理由。原七项语义Work的含义保持：elements、flops、read_bytes、write_bytes、output_bytes、candidate_checks、comparisons；物理sector/PCIe/buffer字节另建账本。

| 语义操作/阶段 | 必需特征（单位未标均count） | 互斥计费边界 |
| --- | --- | --- |
| INIT/init | copied_bytes B | query复制/校验、初始容器构造；排除后续状态插入 |
| MARK_SCORED/state | objects | resident只发布零服务版本token，插入由score_prepare计一次；SSD另计Host state |
| MARK_EXPANDED/state | objects | resident只发布零服务版本token，插入由adjacency_prepare按节点计一次；SSD另计Host state |
| SCORE_L2/score_prepare | vectors, dimensions, state_objects, copied_bytes B, decoded_bytes B | resident随机取数/复制/状态插入；SSD Host只处理真实返回缓冲；Controller只Host命令/ID准备，禁止向量Host复制 |
| SCORE_L2/score_l2 | vectors, dimensions，加七项stage Work | Host含输入decode/算术/packed得分写入；Controller公式只算术，独立缓冲读写另计 |
| SCORE_L2/merge_append | old_scores, new_scores, copied_bytes B, released_vectors | 每语义chunk拼接与临时工作集销毁，稳定segment；指定逐调用实测表；不得分摊到物理块 |
| MERGE_CANDIDATES/merge_finalize | old_scores, new_scores，加独立比较/检查计数 | 批末候选合并/去重/插排/替换，排除上述chunk append |
| SELECT_FRONTIER/select | old_scores, expanded, L, B, E，加candidate_checks | 候选遍历与选择ID构造；终止空选择仍记录 |
| READ_ADJACENCY/adjacency_prepare | edges, state_objects, copied_bytes B, decoded_bytes B | resident行读取/复制/展开状态插入；SSD只Host行decode，状态另计；真实存储由MQSim负责 |
| JOIN/join | 无业务Work | 零服务控制汇合；若实际拼接有CPU成本，归前述adjacency_prepare的独立segment，不留未归属功能拷贝 |
| DEDUP_NEIGHBORS/dedup | edges, scored，加candidate_checks/comparisons | seen遍历、fresh有序插入及邻接容器销毁，真实计数独立核对 |
| FINISH/finish | objects | 最终top-k复制及查询持有容器/状态销毁；禁止用整查询墙钟残差补费 |

state token 的语义版本可先建立；resident 的物理插入成本沿ORDER边归下一prepare，在新capture中显式关联唯一 `state_objects`，不能既收state模型又收含插入的prepare。mark的早期token只表示逻辑版本意图；除负责插入的prepare外，所有读取该状态的业务stage必须还依赖对应prepare完成（可通过显式依赖路径），图扩展预检必须核对这项门控；不得凭零服务mark提前使用尚未计费的状态。每个mark必须被对应prepare完整且仅一次覆盖；终止时未覆盖插入拒绝。SSD state独立采样，prepare的state_objects必须为0。三个上下文 `resident_random/2`、`ssd_host_return/1`、`ssd_controller_command/1` 完全分离；新版本不得默套旧resident实测表/模型。实际feature范围和新计时开销由P04/P06 pilot冻结，不是复用旧Profile的身份许可。

采集需另保存逐item L2计算属性、每语义chunk成员顺序和每阶段segment、原始动态控制计数。公共stage access表达范围；图扩展元数据目录把ID映射成对象/offset，扩展validator核对真实ID/行号，不允许模拟查payload推地址。P01样例只表达所有阶段和一op多stage，并未伪造完整真实搜索证据。

### Work分解与容量分块

唯一可直接注册的图算术拆分规则为 `graph-l2-items/1`：一个原子item是完整d维向量，按采集顺序分区、不能在维度内切割；每子块b项有 elements=b*d、flops=3*b*d、output_bytes=write_bytes=12b、read_bytes=4bd+4d（非空），candidate_checks=comparisons=0。空子块不提交存储/传输，服务仍按声明的空操作token边界异步。语义计数read_bytes按**语义chunk**每块读一次query；物理Controller计数按**实际物理chunk**每块一次query，额外query-read只进物理缓冲账本。两者分别核对，不修改语义Work。

物理块仅可细化语义chunk，不能跨chunk合并或按瞬时空闲量变化；块上限由配置容量及Layout单item sector覆盖上界确定。任一item放不下即拒绝；物理结果按原顺序汇合后才运行该语义chunk的Host merge_append。只缩放可证明可加的flops/字节；准备、排序/去重、候选比较、分配/释放、逐调用ns或非线性模型均**不可按比例分摊**。这些stage标 `none/1`，映射必须保持整个调用边界；要求拆分而无合法规则即 Unsupported。Host服务若要求未采过的subchunk prepare/成本上下文，同样阻塞该映射，不能借L2可加推断全部CPU成本可加。

容量沿现有Controller规则冻结：Q=4d，V=单item最大sector覆盖，S=额外scratch，C=独立buffer容量，n=min(semantic_chunk, floor((C-Q-64-S)/(V+12+4)))，n≥1；最大工作块W=64+S+n(V+12+4)，query admission≤floor((C-W)/Q)。与实际映射合并sector的输入/结果/命令scratch credit核对，留至少一个最大块前进空间。本版正式SSD admission=1，两放置都要求能容纳该最大块，不以容量不足静默缩成不同接纳条件。

## 4. Mapping、模型和Measurement

OperatorSpec注册name/version、端口/单位/扇入基数、stage特征、上下文、支持位置、output_size_rule和独立语义validator。output_size_rule为带版本的编译期注册纯元数据规则，核对采集输出描述且使用checked uint64算术；graph的query=4d、vectors=4bd、scores/candidates/topk=12n、frontier=4n、邻接=4*(1+edges)（JOIN为各行之和）、state/token=0B；块求和u64结果=8B，禁止依赖宿主payload长度。未知规则或不符即拒绝。Mapping输入是已验证stage+有效配置，输出有显式依赖的 reserve/read/transfer/compute/release/publish计划（见C++声明）；包含extent、大小/方向、资源/lease、服务stage_key、返回和发布边界。plan不得执行L2/排序/去重，不接受payload或allocate(logical_bytes)占位缓冲。已知未来访问不能越过依赖提前read/reserve。只有部署依赖索引/已验证元数据允许提前读取宿主元数据文件，不能等价为目标资源预取。

静态注册model/mapping/operator ID与版本，重复或缺失即错误，不回退Host。注册公式实现与参数分开；公式注册带参数schema hash，Profile只保存数据参数，不装载脚本。缺特征、单位/范围/准备/语义/target不符或缺item Work立即拒绝。模型输入为stage及Work/额外特征、位置、上下文、目标硬件，输出uint64整数ns，**仅服务、不含排队或宿主测量运行时间**。

内置公式冻结如下，P05实现独立数值用例：constant/1原样返回uint64 ns；nonnegative-linear/1为ceil(intercept+有序Σcoefficient*feature)，binary64、有限非负系数/截距、禁FMA、范围上界验证、结果<2^64；upper-bound-table/1为单整数特征递增闭上界，下一整数接续且完整覆盖范围、ns非负；log-gbdt/1保留现有前序树、<=阈值、显式右子树、最多180树/深3、按树序binary64求和后max(1,ceil(exp(initial+rate*sum)))，检查溢出，但不再绑定某图算子名。unknown formula拒绝。零服务合法但不可同步放行；成本估计不读取采集时间戳。非线性函数跨平台差异在目标实现身份中记录，不能宣称任意宿主逐位等价。

Profile的prediction规则记录training Measurement与原始query ID、fit报告、feature范围/单位、参数schema、公式版本和target。实测表使用独立source=measured-table，逐行匹配完整稳定键、Work、features以及外层binding（包/输入/语义/采集构建/上下文/target/配额）；行唯一，缺项/失配拒绝。表至少3个不同独立measurement artifact及完整质量报告，按冻结规程对相同call的合格corrected_ns取中位；不得用同hash重复三次冒充独立运行。表覆盖/质量/身份/唯一计费单列，不能计模型泛化误差。正式图允许表的stage是graph配方的merge_append；其他用户扩展按显式recipe，不作为通用白名单。

Measurement中clock记录线程CPU来源/分辨率与同协议empty probe；raw=end-start，overhead=floor(empty_probe_ns/empty_probe_count)*calls，corrected=raw-overhead（保留负值），corrected<calls*resolution为low_signal。低信号不得直接训练或制表，可在事先冻结的连续独占同类窗口合并。eligible/planned/observed完整匹配采样stride；完整IR不采样。benchmark/capture/sample分别运行，benchmark无CPU探针且不能用建表调用代替。score外层审计只闭合prepare与l2，间隙≤max(5%外层raw,2*resolution*calls)，不另计服务。遗漏/丢失、跨桶重叠、未归属功能拷贝/销毁拒绝质量出口，不用QPS/墙钟残差拟合。

训练只用tuning32，四折按原始query ID分组，冻结recipe/模型后才评价evaluation128；evaluation只能绑定对应实测服务表，禁止回流预测拟合。历史evaluation暴露继续按V0.4发布记录披露。新阶段/构建变化必须新采、重拟合或明确失败，不编辑hash继承旧通过。

## 5. 时间、令牌、消息和资源

时间继承[既有事件契约](contracts.md)：SST唯一权威，MQSim≤SST now，整数ns；调度键 `(time, delta, phase, sequence)`，phase 0 Backend →1 ResourceCompleteRelease →2 Publish →3 ContinueSubmit；同phase FIFO。回调产生同刻较低phase进入delta+1，不加1ns。Runtime只有元数据输出和CompletionToken，没有evaluate/Bytes/get业务值接口。

状态为 Submitted→WaitingDependencies→WaitingResource→Running→ServiceDone→WaitingReturn→Visible；无需返回仍经过异步Publish。资源释放在最后实际使用后phase1，成功令牌在phase2，后继submit在phase3。输入/服务/必要返回缺一不得Visible；零服务也走上述phase，已开始phase不重入。失败停止新服务/后继，既有I/O/链路/缓冲排空后释放owner/pin/credit，再发布失败；不能安全排空即实验非零退出。close等待所有chunk实际返回，query credit保守保持至CloseAck；失败亦不凭空清零。

| 对象/服务 | 唯一owner及释放/可见边界 |
| --- | --- |
| NAND/FTL/GC/WL/CMT/cache/ONFI与预装载 | MQSim；保留初态和测量窗口，不再加平台SSD固定费 |
| 普通NVMe SQ/CQ/doorbell/数据及平台消息 | MQSim PCIe双向FIFO；原生完成事件驱动，Endpoint不重复收序列化带宽 |
| Host CPU各stage | HostCpu slots/FIFO；新Profile含该边界的加载/decode/算术/拷贝，不另收Host DRAM |
| Controller算术 | Compute slots/FIFO；仅算术，不能把buffer服务混进flops rate |
| 独立buffer容量及query/input/result/scratch credit | BufferModel；源保留到实际传输到达，输入/scratch至最后计算使用，result至返回到达，query至CloseAck |
| query写、input staging写、compute input+query读、result写/读出 | BufferModel单端口FIFO，分别ceil(bytes/rate)，不重复MQSim ONFI/PCIe；scratch访问须显式Work，不由容量猜 |
| 端点处理 | EndpointModel；只计显式新平台处理，默认0，不重复原生NVMe端点 |
| 逻辑对象/版本/元数据token | Runtime owner/pin/依赖/inflight分开；都归零才回收，credit不跟随token任意释放 |
| 哈希/索引预检/JSON/真实采集/报告 | 宿主wall-clock only，不变成模拟服务 |

Message用固定大小协议字段加显式logical/payload/wire大小描述，不encode/decode业务数组，不分配同长度零缓冲。graph协议 `graph-readonly/1` 保留现有固定头48B：Open 64+4d，OpenAck48，Score64+4b，ScoreResult56+12b，Close/CloseAck/Error48；useful logical量分别query=4d、IDs=4b、得分=12b、ACK=0。一般扩展使用自己的size_rule注册，不把L2协议硬编码通用调度；字段校验query/task/token/chunk/count/version/direction/status，未知协议/规则/溢出拒绝。元数据相关ID不等于线上业务payload，线上大小仍按规则收足。

MQSim公式保持：envelope的wire=P+ceil(P/M)*(tlp+dllp+physical)，P=0时0；READ_REQ固定O+4；原生service=floor(ceil(wire/lanes)/lane_GBps)ns，保留0ns。普通NVMe不改成平台header；本机SQE实际72B由ABI探针验证，QD1读账本仍为H2D doorbell2+SQE72+CQ-head2，D2H SQ-fetch READ_REQ4+data sector覆盖+CQ16；并发按实际消息，不盲乘QD1。Host普通read可见须data D2H和CQ消费都完成；本地read无SQ/CQ/完整向量D2H，含NAND data-out完成、安全清理和staging后方可用。单层服务和逻辑/sector/NAND/PCIe/buffer字节分列。

## 6. 配置权威和正式调度冻结

逻辑对象/访问/Work/原始query顺序唯一来自IR；Layout唯一给物理extent/对齐；设备XML唯一给MQSim几何/partition/初态/cache/CMT及PCIe参数；资源文件唯一给Host/Controller slots、独立buffer容量/带宽、Endpoint服务；Profile唯一给计算需求和target适用性；Mapping只给放置/步骤/大小规则。禁止同一参数重复隐式覆盖，例如资源文件不得覆盖XML PCIe带宽，Profile不得覆盖buffer容量。冲突拒绝，不按“最后写入”取值。

所有文件引用相对其声明文件解析，禁止cwd隐式回退；保存原始文件、实际解析路径、hash、完整有效值及来源字段。模拟可外部指定包/模型/布局，不依赖维护者results。`host_memory`要求Host且device=null，0设备实例/0SSD请求；显式fixture_allowed只给夹具。生产新配置不接受旧engine或旧schema转换。target可与execution_host不同；模型测量硬件匹配target。实际参数支持白名单/容量/partition和初始化预算由P09/P10实现，不宣称支持任意XML。

本次将P00矩阵的SSD待定项冻结：A01/A02和B01/C01/B02/C02均 offered=0、FIFO query顺序、workers/CPU slots/admission=1/1/1；query占有worker至最终Host Visible，drain包含CloseAck/链路/资源排空。队列和资源同刻均FIFO，不增加隐式分支串行；扩展中独立stage可并行，图共享状态/原生顺序由显式ORDER表达。所有B/C使用相同设备XML/Layout/输入/Profile适用域与资源配置；差异仅 placement、所需映射及准备上下文/服务来源（逐项快照），Controller特有Compute/Buffer配置仍在Host配置中同值保存但未被使用，不暗改Host配额。其他并发仅定向夹具/诊断，修改正式策略须显式新版本及证据。

窗口沿现行定义：offered→admission→worker_start→visible→drain；latency=visible-admission，offered latency及worker wait另列，QPS=Q/(last_visible-first_admission)，分位数nearest-rank并记录N，加载/预热/哈希/写报告不入窗口。CPU服务、队列等待、关键路径不混同。

## 7. 风险夹具、删除门槛和限制

[设计夹具测试](../tests/test_v045_contract_design.py)只验证本次契约表达和独立算术预期，不是第二个生产事件引擎：

- fork/join：root输入2+服务3+返回4=Visible9；left=9+5+2=16，right=9+11=20，join=21。若显式left→right ORDER，则right27、join28；记录物理重排不改变rank/依赖结果。DATA/ADDRESS/CONTROL/ORDER均等待Visible。
- 输入延迟/返回延迟传播到后继；失败源不成功放行。两个零服务串联可都在0ns，但phase1→2→3→下一delta phase1，不同步递归或+1ns。
- chunk：d=3，5项，语义[3,2]细化成[2,1,2]，语义flops45/read84/output60，物理flops45/read96/output60；差12B是额外query-read。比较计数和append实测ns无规则不可拆。
- byte oracle：d32、b2、M128/O28/lanes4/rate1，Host H2D payload/wire=152/320，D2H=1576/2024；Controller=388/584和708/960，buffer2352B，credit峰1248B，向量D2H=0。独立核对协议、普通read与buffer公式。
- schema/关系负例：旧格式、payload路径/采集时戳、未知字段/算子/端口、跨query、缺边/环/重复rank、失配单位/Work/表身份、低信号与溢出/未知分解等不能静默通过。

P01尚未证明生产SST同刻顺序、流式包完整性、运行期去payload、成本精度、Controller排空或大Q有界；对应分别由P02/P04/P05/P07/P08/P10/P12/P16/P18完成。当前阶段目录和函数声明可供后续实现，不是对旧包改版本号的迁移器。无新CPU质量数据，任何未采全stage/分解来源缺失阻塞该映射，不阻塞独立通用模块继续实现。未开始P02，未删除旧参考。

## 8. P01 完成记录与实际验证

状态：**P01完成；P02～P21未开始，全部AC未验收**。本次主仓HEAD为 `2b44f52367abf812757d52690320d46866bc7ee4`；完整工作区文件hash、diff、锁和子模块逐文件身份保存于 `results/v0.4.5/p01/run1/`。MQSim fork仍为 `8f7ddf6d8cafdb7c783b3fcfd4693f15c6c5854b`，相对锁定原生 `51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16` 的diff hash仍为 `f38fc06069ee252ebbf83cdc2e566768d74a255fbb2ff1980ba76b0f2a7ec044`；SST `ffd71527bc26a1f2a4fb685fdeb2ca0799997d78` 未修改。没有修改生产src、根CMake、依赖锁或任何子模块，没有提交或推送。

P00依赖复核实际验证了两个manifest的已知hash、passed状态及其中全部产物hash：run1=`65005486f4f971b561b60b7d23b91d03ea4d05bedeabfb1f60a4de8b307f7aad`，final1=`9018ed4762e7ea8f89d9c53bab42aa983e780905693c185607e7e7ea4572ee45`。这只核验P00来源，不将旧版或P00通过计为本版AC。

实际执行且退出码均为0：

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tests/test_v045_contract_design.py
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tests/test_v045_baseline.py
c++ -std=c++17 -Wall -Wextra -Wpedantic -Werror -I . \
  tests/v045_contract_header_probe.cc \
  -o results/v0.4.5/p01/design-final1/header-probe
results/v0.4.5/p01/design-final1/header-probe
cmake -S . -B build/delivery -DBUILD_TESTING=ON
cmake --build build/delivery -j2
ctest --test-dir build/delivery --output-on-failure
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/audit_v045_baseline.py \
  --build build/delivery --output results/v0.4.5/p01/design-final1/audit
git diff --check
```

最终设计测试 **11/11**，含schema正负例、typed/variadic端口、版本链、四类依赖、fork/join/ORDER、输入及返回延迟、失败传播、零服务phase/delta、Work分解、协议/缓冲独立字节核对、表Work/身份拒绝和跨宿主目标分离。P00门控 **3/3**（新增SSD接纳/供给差异拒绝）；接口探针编译/运行通过。根CTest **93/93** 通过，只作当前V0.4回归，不计新生产执行器验收。迁移审计 **279文件、93CTest**：新增17个schema/样例/独立测试文件；根CTest注册不变。清单为持续维护的迁移依据，P00原始262文件快照仍保存在P00证据中。

`run1/`保存根配置/构建/CTest、初轮设计检查和P00/依赖验证；最终schema/接口修订后在 `design-final1/`重新运行全部设计检查、编译/运行、门控和审计，并逐项验证本轮根CTest所用生产src/CMake/锁、实际二进制/flags/SST缓存receipt完全未变，没有无理由重复完整回归。最终设计manifest SHA256：`223e50c22269c7e3e7658424ccd9af46070c5011d1209934efad307a60e2aef6`。各目录均含可重放编排、commands及退出码、原始log、源码/构建/输入/产物hash和scope；收口文档身份另存 `closeout1/`。

未执行新SDK封包、生产SST风险夹具、新采集/pilot、CPU拟合/质量/精度、SSD映射/新MQSim对拍、全量矩阵或正式AC。本次没有全量资源操作，P00记录的资源值不作为未来许可；每次正式运行仍须新预检。样例是synthetic-fixture且身份未封装，没有冒称原生独立功能证据。P01的门控预期需由后续生产实现逐项兑现，不以设计算术测试替代它们。
