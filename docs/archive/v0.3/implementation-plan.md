# V0.3 可执行实施清单

状态：**V03-0～V03-7 已完成**（2026-11-03），包括完整 SIFT1M 52条矩阵、三组交替效率及Q梯度；V03-7 总交付12/12 AC已通过，日常默认已切换为IR，显式direct保留。V03-6实际证据见[收口](ir-matrix.md)。V03-0冻结命令、身份与预算见[基线收口](direct-baseline.md)。

## 规划时仓库复核（历史快照）

- PRD记录的源码基线为 `fb2ded2097fd07fb93bfc9258f02122fad50ed73`；当前主仓 HEAD 为 `1d5e0404db9ccf543c68febf6d6c6c99d97b51de`（2026-11-02，`chore: archive v0.2 development materials for v0.3`）。两者之间 `src/`、`CMakeLists.txt`、`tests/` 未变化；差异限于归档配置/工具和相应说明。
- 工作区核查时干净。子模块仍为 MQSim `8f7ddf6d8cafdb7c783b3fcfd4693f15c6c5854b`、SST Core `ffd71527bc26a1f2a4fb685fdeb2ca0799997d78`，与 V0.3 PRD 记录一致。此项仅为 Git 身份核对，不是本轮源码哈希、构建或运行验证。
- 当前直接路径仍在 `src/search/graph.cc` 与 `src/sst/graph_host.cc`：`GraphSearch` 通过读取/评分回调和 `HostCpu` 执行；`GraphHost` 按 placement 接入普通读取或 `Controller::open/score/close`。`src/runtime/`、`src/backend/event_bridge.h` 提供可复用的异步可见性及 SST 时间机制。
- 当前配置、运行器、源码和测试中没有 `execution_engine=direct|ir`、IR schema/校验器或动态 IR 执行实现。V0.2 证据不是 V0.3 direct 冻结运行；本轮也未执行 direct 参考运行、构建或测试。

## V03-0 复核记录（2026-11-03）

- 主仓 HEAD 为 `88a1d81f87da4504890add3139bea09b61cef82c`；MQSim/SST 完整提交与上列一致且未修改。相对规划快照，搜索、根 CMake、原测试和实际运行器未变。
- 锁定完整 SIFT1M 准备产物、原始四文件、图/真值/固定查询及 builder 身份重新核验通过；严格复用准备输入，本轮新跑6次 direct，不复用历史运行结果。
- 根配置/构建退出0，CTest 67/67；新增证据校验测试3/3；完整评价128查询两放置结果/决策摘要一致，detail8的1,066批及资源账本独立核对通过。12项冻结断言全通过。
- RAM实测16GiB；完整base运行前已冻结PRD §9默认门槛。磁盘采样峰值3,514,770,629B，最小卷余量93,319,942,144B。主证据 `results/v0.3/direct-baseline-final/manifest.json`，hash及复现步骤见收口文档。
- 没有实现engine选择、IR/schema或比较器，没有运行完整AC、原生oracle、ASan或52条矩阵；不将本任务通过视为V0.3验收通过。

## V03-1 本轮收口（2026-11-03）

- 完成schema/能力目录、C++内存表示及严格静态校验；结构合法不表示算子已可执行。七算子均标记execution_supported=false。
- 本轮根配置/构建退出0，CTest 69/69，schema测试9组、冻结证据测试3组及独立JSON Schema核对通过；V03-0冻结产物重新核验。详见[收口与限制](ir-schema.md)。
- 主证据`results/v0.3/schema-v031/final/manifest.json`保存实际命令、退出码和hash；未运行完整AC、原生oracle、ASan或SIFT矩阵。该轮未启动后续任务；V03-2 本轮结果见下节。

## V03-2 本轮收口（2026-11-03）

- 完成增量 DAG 接纳、四类依赖及活动 lease 校验、可见真实值绑定、不可变版本状态、异步 JOIN、失败传播与活动映射回收；复用 Runtime/EventBridge，不修改时间或计费实现。能力目录仅 JOIN 标记 execution_supported=true，其余六算子及完整 engine 仍未实现。
- 根配置/构建退出0，完整 CTest 74/74；原生微型探针及真实 SST 四组因果运行通过，2,001 JOIN 的活动/终态回收及同刻提交顺序对照通过；定向 ASan 3/3。9组 schema、3组冻结证据测试和 V03-0 全部冻结产物 hash 复查通过。
- [接口、收口与限制](ir-lifecycle.md)；主证据 `results/v0.3/dynamic-v032/final3/manifest.json` 保存源码/输入/依赖身份、命令、退出码、日志、断言及 hash。首轮证据汇总格式失败保留，后续完整重跑通过。
- 没有运行完整 AC、原生 oracle、真实搜索四组合/52条 SIFT 矩阵或 Q/RSS 梯度；未开始 V03-3，不以管理层探针通过替代真实算子映射或 V0.3 验收。

## V03-3 本轮收口（2026-11-03）

- 七算子均有真实映射；QUERY_READY/JOIN 异步无新增费用，READ/SCORE 复用原读取、HostCpu 和 Controller。host 保留逐块提交，controller 保留一次 Open、容量 chunk、真实返回及 Close；控制父任务直接复用原 HostCpu 操作描述，保留提交时间和服务次数。
- 补齐初次 MERGE 的固定空历史得分 literal；同步 schema/能力目录/hash/样例。复合失败逐子任务排空，Controller 增加无费用异步 chunk 排空通知；Open 失败和提前 Close 安全清理。默认 direct、生产 GraphSearch、SST/MQSim 及计费公式未改。
- 根配置/构建退出0，完整 CTest **75/75**，定向 ASan **4/4**；20次真实 MQSim 进程探针通过四组合逐批/时序/Work/读取/消息/link/credit/资源账本、跨页/多chunk/单块容量/同刻CPU竞争/失败排空。新跑冻结 smoke 两 placement，完整结果/模拟统计一致，事件和服务 CSV hash 与 V03-0 一致。
- [接口、实际命令、失败记录与限制](ir-mapping.md)；主证据 `results/v0.3/mapping-v033/final4/manifest.json` 保存输入/源码/依赖身份、命令、退出码、日志、断言及 hash。V03-0/1/2 登记证据全部重新核对。
- 未运行完整 AC、独立原生 oracle、真实 SIFT 四组合/52条矩阵、搜索 Q/RSS 梯度或效率预算；生产前端、engine 选择及全链路观测仍待 V03-4/5。停止于 V03-3，不自动开始下一任务。

## V03-4 本轮收口（2026-11-03）

- 独立生产 IrGraphSearch 仅提交类型化 IR、消费可见输出和更新不可变 scored/expanded 状态；复用 V03-3 映射。GraphHost/运行器支持显式 execution_engine=direct|ir，省略仍 direct；写入有效 engine、实际输入/源码身份与空操作 contract，拒绝非法 engine/错配 contract。原 GraphSearch、Runtime、EventBridge、计费服务及两个子模块未改。
- 根配置/构建退出0，完整 CTest **78/78**；16次合成生产 SST、12次真实 SIFT10k（N10000/d128/Q32）四组合新运行，逐批/结果/时序/Work/读取/消息/link/资源账本一致；固定图及查询下交换向量改变两个 engine 的真实后继读取。生产前端受控输入/计算/返回延迟各100ns使后继READ 40→140ns，独立查询均7ns，提前读取均 NotReady。
- 20次真实后端进程探针覆盖生产前端常规/小容量/同刻竞争及既有映射边界；冻结direct smoke两放置新跑结果/模拟统计一致，events/service.csv hash相同；定向ASan **2/2**。V03-0/1/2/3成功证据及登记hash全部复核。
- [实现、旁路审计、实际命令、失败记录与限制](ir-frontend.md)；主证据 `results/v0.3/frontend-v034/final2/manifest.json`。首轮final因汇总器写死图文件名失败，修正为manifest路径后完整重跑通过，保留失败证据。
- 未执行完整AC、原生oracle、完整SIFT1M 52条矩阵、Q/RSS/效率验收或正式statistics/detail对照。V03-5全链路观测和独立比较器未实现，不切换日常默认；停止于V03-4。

## V03-5 本轮收口（2026-11-03）

- 新增必需 observation 身份/决策摘要与七算子累计/活动/峰值统计；detail 在既有预算内流式写 operation/value摘要、Runtime阶段、实际子任务、Controller chunk与Open/Close关联。statistics无隐藏DAG历史，无新增模拟服务或费用；两个子模块未改。
- 独立 `ndpsim-equivalence/1` 比较器逐字段核对结果、时序、Work、资源、请求/传输、credit及初态；按query/角色/有序元素或块建立语义锚点并连接IR scope，保留ID引用关系，白名单只列确切管理/身份/宿主测量字段。缺关联、错误父操作、未知字段、资源差异及跨engine缓存复用失败关闭；既有独立数值/搜索/账本核对继续执行。
- 根配置/构建退出0，完整CTest **79/79**；显式合成8次、真实SIFT10k/Q32 8次及冻结direct两放置2次新运行通过。statistics/detail与同placement direct/ir严格等价；两engine独立账本通过；19项比较器对抗检查及2048B真实IR预算拒绝通过；全部IR记录独立JSON Schema检查通过。冻结direct结果/模拟统计相同，events/service.csv hash一致。
- [接口、规范化白名单、命令、失败记录与限制](ir-observation.md)；主证据 `results/v0.3/observation-v035/final2/manifest.json`，10项命令阶段均退出0，1,235项产物hash。V03-0～4依赖成功证据全部复核，代码与依赖在运行期间未变。
- 未执行完整AC、原生oracle、ASan、完整SIFT1M 52条矩阵、Q/RSS/效率验收。普通NVMe内部transaction ID及共享NAND命令参与集合仍Unsupported；不伪造归因。默认仍direct，停止于V03-5。

## V03-6 本轮收口（2026-11-03）

- 新增失败关闭的完整矩阵/效率入口；严格复核V03-0～5依赖、相同完整base/NSG/真值/查询及builder、16GiB RAM和冻结磁盘预算。两个engine各26条全部新跑，补4次第三组交替、4次冻结smoke及18次Q梯度，共78次生产SST运行；不复用历史仿真结果、不重抽查询或重建SIFT图。
- 诊断首组旧IR查询墙钟2.57倍后，改用经完整静态校验的contract缓存、仅活动producer描述、共享不可变payload和原packed FP64得分；JSON只在诊断时生成。静态文件仍全文校验，新操作结构/引用/真实shape/可见性与计费、事件顺序不变。比较器资源数组改为流式SQLite，保留全部字段和顺序。
- 根配置/构建退出0，完整CTest **80/80**、定向IR ASan **4/4**；52条覆盖、28项同placement engine对照、重复/日志模式/并发决策、两engine独立数值/搜索/全账本及完整评价集冻结参考均通过。171项断言全true，129个命令阶段退出0，2,831项产物hash；运行期间源码/依赖/冻结预算一致。
- host/controller三组统计模式中位墙钟比 **1.299966/1.427963 ≤1.5**；每对IR RSS增量通过。Q32/128/512各3次、两placement共18次，终态清零，峰值5 ops/7 values；Q32→512中位SST RSS增量81,920/16,629,760B ≤64MiB。SST最大854,425,600B；磁盘采样峰值10,764,730,877B <25GiB，最小卷余量83,994,238,976B >20GiB。
- [命令、全部样本、四点质量、detail开销、失败与限制](ir-matrix.md)；主证据 `results/v0.3/matrix-v036/final5/manifest.json`，补充 `results/v0.3/matrix-v036/closeout/manifest.json`。开发中断/插桩失败与旧IR诊断保留，不计正式通过。未运行完整V0.1 AC或独立原生oracle，不宣称V0.3总交付；默认仍direct，停止于V03-6，V03-7未开始。

## V03-7 本轮收口（2026-11-03）

- 新增失败关闭的 `tools/verify_v03.py --all`、12项适用AC映射和交付门槛测试；禁止quick/缺项/失败/篡改/身份不符复用。旧参考自动化显式direct，日常省略engine改为ir并记录有效模式；显式direct保留，算法/成本/后端及子模块未修改。
- 根构建与全部CTest **82/82**，完整V0.1 AC **11/11**、锁定原生oracle/慢功能/继承ASan **17/17**、V0.2 T3/T4/T5、定向IR ASan **5/5**通过。因默认源码身份改变，本轮全新运行52条矩阵、第三组补测、冻结smoke及18次Q梯度，共78次生产SST；效率中位比host/controller **1.278253/1.452972**，RSS与Q梯度门槛通过。
- 默认IR/显式direct两放置、冻结参考及省略engine的完整base评价128条两放置新复现通过；12/12 AC全部passed。主证据 `results/v0.3/delivery-v037/final1/manifest.json`；[交付记录、补充检查与限制](../../releases/v0.3.md)。源码/依赖/冻结预算在运行期间未变；成功后仅更新文档和任务状态，后续静态/日常CLI补充在closeout单列。
- 硬件configured_unvalidated、固定查询子集、官方并列边界unresolved、目标0.95独立评价未达、整进程RSS/采样磁盘与定向ASan范围均保留。停止于V03-7，不开始下一任务；未提交、推送或升级/初始化依赖。

## 可执行任务

每项关闭时保存输入/源码/依赖身份、命令和退出码、断言及产物哈希。实际文件名可随实现调整，但不得降低验收内容。

| ID | 需求来源 / 依赖 | 实现范围 | 验收方法 | 完成证据 |
|---|---|---|---|---|
| **V03-0 基线冻结（已完成）** | PRD §3、§7.1、§9、§12(1)；依赖：当前 direct 入口和固定 SIFT1M 准备产物 | 记录工作区源码及未提交 diff、MQSim/SST 完整 commit、工具链、输入/图/真值/查询身份和配置；对固定 direct 配置运行并保存结果及足以独立核对决策、资源账本的详细样本；核实机器 RAM/卷余量与 IR 预算 | 输入身份复核；direct 运行成功；结果可由现有独立核对器重查；记录实际资源与建议效率门槛的冻结值 | [本轮收口](direct-baseline.md)；`results/v0.3/direct-baseline-final/` 的 manifest、12项断言、命令/log、源码/输入/产物hash、6次新运行、独立1,066批核对与预冻结预算；67项CTest及3项证据测试通过 |
| **V03-1 schema 与能力目录（已完成）** | FR-03-01/06/12、IR §3–4、AC-03-01；依赖：V03-0 | 建立 `ndpsim-ir/1` 内存类型、对象/ValueRef/Operation/作用域、端口和能力目录；提供与内存定义一致的机器可读 schema/合法样例；拒绝未知必需语义 | 合法样例通过；版本、算子、位置、字段、类型、shape、身份及重复 ID 等非法输入被明确拒绝 | [V03-1 收口](ir-schema.md)；C++类型/往返与静态校验、机器schema/七算子目录/合成样例及JSON诊断；manifest记录IR/算子版本和目录hash；根构建退出0、69/69 CTest、9组schema测试与3组基线证据测试通过；独立Draft2020-12核对通过；执行映射明确为未实现 |
| **V03-2 动态依赖与值生命周期（已完成）** | FR-03-04/06/07、IR §5/7/8、AC-03-04/06/07；依赖：V03-1 | 实现增量 DAG 接纳和 ADDRESS/DATA/CONTROL/ORDER 校验、真实可见值绑定、状态版本/所有权、异步 JOIN、失败传播及活动对象回收；复用 Runtime/EventBridge | 非法前向/自依赖/错误作用域拒绝；零时长异步；延迟扰动不提前恢复；失败无成功后继；完成后 waiter/value/op 映射可回收；同刻资源仲裁不变 | [本轮收口](ir-lifecycle.md)；`results/v0.3/dynamic-v032/final3/`：完整 CTest 74/74、定向 ASan 3/3、原生及 SST 四组因果日志、真实值动态后继、状态所有权/失败排空/非法引用、2,001 JOIN 峰值与终态回收、同刻 FIFO 对照；未实现 V03-3 资源映射 |
| **V03-3 算子与 placement 映射（已完成）** | FR-03-02/03/05、IR §6–7、AC-03-02/05/06；依赖：V03-1、V03-2 | 实现 QUERY_READY、READ_ADJACENCY、SCORE_L2、MERGE_CANDIDATES、SELECT_FRONTIER、DEDUP_NEIGHBORS、JOIN；映射至既有读取、HostCpu、Controller。SCORE_L2 保持 host 原逐块提交/评分及 controller 原 Open/容量 chunk/返回/Close 语义 | 两 placement 的提交与汇合顺序、工作量/服务次数、消息和账本核对；跨页/多 chunk/单块容量/失败用例通过；向量完整 D2H 为零 | [本轮收口](ir-mapping.md)；`results/v0.3/mapping-v033/final4/`：七算子真实服务映射、20次真实 MQSim 探针、四组合逐批/时序/Work/全链路字节/credit/资源账本一致、跨页/多chunk/单块容量/同刻竞争/失败及Open排空；根构建和75/75 CTest、定向ASan4/4、冻结smoke两放置新跑结果/事件/服务一致；未启动生产前端迁移 |
| **V03-4 动态 GraphSearch 前端（已完成）** | FR-03-02/03/09、IR §9、AC-03-02/03/04；依赖：V03-3 | 将现有搜索流程改为只提交 IR 并消费可见输出；保留 scored/expanded 更新时点、搜索批次、计数、同分和终止行为；支持 direct/ir 显式选择，保留可运行 direct 对照 | 源码旁路审计；合成和真实 SIFT 10k 小矩阵 direct/ir × host/controller 逐批一致；固定图下扰动向量改变真实后继读取且两 engine 各自一致；输入/计算/返回延迟因果检查 | [V03-4 收口](ir-frontend.md)；`results/v0.3/frontend-v034/final2/`：78/78 CTest、16次合成/12次真实SIFT10k生产运行、四组合逐批/时序/账本一致、固定图向量扰动/三阶段延迟、20次后端探针、冻结smoke两放置新跑及定向ASan2/2；实际engine/contract身份、源码旁路审计与失败记录 |
| **V03-5 观测与独立比较器（已完成）** | FR-03-08/09/11、PRD §7、IR §10、AC-03-08；依赖：V03-0、V03-3、V03-4 | 输出 engine/schema/operator/mapping 身份、算子计数、活动/峰值/决策摘要；detail 日志流式关联 IR op/value 与 runtime task/request/transaction/message；实现版本化语义比较键和窄白名单 | statistics/detail 模拟结果一致；比较器逐字段验证第7节；仅允许文档列出的身份、编号、诊断、墙钟/RSS、文件大小差异；跨 engine 结果不得复用 | [V03-5 收口](ir-observation.md)；`results/v0.3/observation-v035/final2/`：身份/摘要/七算子统计、诊断schema及实际关联样本、严格版本化比较器、79/79 CTest、19项对抗检查/预算拒绝、合成8次及真实SIFT10k8次statistics/detail/四组合对照与独立账本、冻结direct两放置新跑；不含完整矩阵或效率验收 |
| **V03-6 完整矩阵与效率门槛（已完成）** | FR-03-10/11、PRD §8–9、AC-03-09/10；依赖：V03-4、V03-5；进入本项前确认固定数据与磁盘预算可用 | 运行完整 base 的两个 engine 各 26 条；按冻结方案做评价集交替重复、Q 梯度和 detail 样本；不重抽查询、不重建图掩盖差异；仅身份严格匹配时复用本轮产物 | 52 条矩阵齐全；同 placement direct/ir 模拟等价、质量和预算门槛通过；Q 梯度证明非必要活动历史回收；超限明确失败并调查，不缩小矩阵 | [V03-6收口](ir-matrix.md)；`results/v0.3/matrix-v036/final5/`：52/52、完整CTest80/80、定向ASan4/4、28项engine对照/重复/日志/并发/独立账本、三组中位比1.300/1.428及RSS门槛、18次Q梯度通过；171项断言/129阶段命令/2,831产物hash；补充closeout完整冻结评价对照和detail开销；当轮未开始V03-7，其现行完成证据见下项 |
| **V03-7 继承回归与交付切换（已完成）** | FR-03-10/12、AC-03-10/11/12；依赖：V03-6 | 增加 V0.3 总验收入口与适用 AC 映射；旧配置省略 engine 可运行并记录有效模式，显式 direct 可用；全部门槛通过后将日常入口默认改为 ir；更新限制、接口、测试和交付文档 | 根 CMake 构建、全部有效 CTest、完整 V0.1 AC、相关 V0.2 检查、原生 oracle、IR 生命周期 ASan；不接受 quick/缺项/失败/身份不符复用 | [V03-7交付](../../releases/v0.3.md)；`results/v0.3/delivery-v037/final1/`：12/12 AC、根构建与82/82 CTest、完整V0.1 AC11/11/独立原生oracle/ASan17/17、T3/T4/T5、全新52条/三组效率/18次Q梯度、IR ASan5/5、默认IR/显式direct/冻结参考及完整base新复现；命令/退出码/断言/hash齐全 |

## 阻塞项与默认决策

### 开始核心实现前的阻塞项

1. **V03-0 direct 参考证据门槛已关闭。**本轮冻结源码/输入/配置和新运行结果，详细样本覆盖主要阶段与账本，独立核对通过。后续共享重构须分别对照冻结参考，不能仅靠重构后的direct/ir互比；证据见[收口](direct-baseline.md)。V03-1 本轮已复查冻结manifest和全部产物hash；V03-1轮止于schema与能力目录；V03-2已完成管理层实现；V03-3已完成真实算子映射；V03-4已完成生产动态前端；本轮V03-5复查全部依赖证据并完成观测与独立比较器，止于V03-5。
2. **本轮V03-6资源门槛已关闭。**重新读取16GiB RAM，完整输入/准备manifest与V03-0精确匹配，52条和效率/Q梯度均按原冻结阈值通过；磁盘/卷余量采样通过，见[证据](ir-matrix.md)。未来复跑仍须重新预检，不以本轮余量代替未来资源状态。

当前没有发现需要重新决定 V0.3 产品范围的问题。除届时资源复核门槛外，下列是可依规范采用的实现默认，不阻塞设计或编码：

| 决策 | 建议默认 | 依据 / 约束 |
|---|---|---|
| 配置键与版本 | `execution_engine: direct|ir`；IR `ndpsim-ir/1`，开发期缺省direct；V03-7完整验收通过后日常缺省ir，旧自动化参考显式direct | PRD §4.1/§6 FR-03-10；无效值拒绝，不静默回退 |
| IR 落地形态 | 进程内 C++17 强类型对象；机器可读 schema 用于一致性校验与诊断，不要求运行前序列化/解析 | PRD §4.1、IR §1/§3/§11 |
| 操作 ID 与作用域 | run 内 uint64 单调不复用；每操作带 query/iteration/semantic_batch，动态后继只在结果可见后创建 | IR §4–5；不保留全历史表来验证旧 ID |
| 查询状态 | 不透明、版本化 state_ref；提交消费者时持有，结果可见/消费者排空后释放；只对被计费输出执行确定性状态更新 | FR-03-03/07，IR §6.3/§8 |
| SCORE_L2 映射 | 沿用 IR §6.2：host 按原 chunk 逐块提交读与 HostCpu；controller 复用现有会话、容量 chunk、本地读和真实返回 | 不改算法批次、计费、消息或结果可见边界；若探针发现原语义不匹配，先修映射而非放宽等价标准 |
| 对照与规范化 | 保留同构建 direct；以冻结 direct 产物作为独立锚点；用 query/iteration/semantic_batch/角色/块建立语义关联，白名单只排除 PRD §7.1 允许项 | 不能泛化排除时间、资源、工作量或所有 task ID |
| 诊断产物 | 采用 manifest + 可选流式 operations/bindings JSONL；大值只记类型、shape、身份/hash，不存完整 DAG/张量 | IR §10，按输出预算失败关闭；诊断不可回放 |
| 效率阈值 | 已按PRD §9默认值冻结；小规模测量及完整SIFT运行前决策见 [V03-0](direct-baseline.md)，V03-6三组/Q梯度及预算已通过，见[收口](ir-matrix.md) | 正确性阈值不随效率预算调整；所有 hardware 参数仍 `configured_unvalidated` |

本计划不授权扩大负载/设备/计算位置，不要求修改 SST Core 或引入第二条时间、存储、PCIe 计费路径；不提交、推送、升级或初始化依赖。
