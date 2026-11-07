# V0.4.5 可执行实施计划

日期：2026-11-05（Asia/Shanghai）。依据 [正式 PRD](prd-v0.4.5.md)和[源码核查](source-audit-v0.4.5.md)。

状态：**P00/P01/P02/P03/P04/P05/P06/P07/P08/P09/P10/P11/P12/P13/P14/P15/P16/P17 已完成（2026-11-07）；P18 已完成配方三版及 A01/A02 正式驻留 CPU 精度验证（见 docs/resident-accuracy-v0.4.5.md），P19～P21 未开始，全部 AC 尚未验收。** 当前可运行交付仍为 V0.4。[P00 基线与迁移记录](baseline-v0.4.5.md)保存本次构建、93项CTest、小夹具与清单证据；[P01 契约与风险设计](contract-v0.4.5.md)已冻结并完成设计检查；[P02通用SDK与校验](capture-sdk-v0.4.5.md)已完成；[P03独立图搜索构建](native-v0.4.5.md)及[P04新IR采集与语义校验](capture-graph-v0.4.5.md)已完成（合成pilot），P18 已执行 tuning32 正式训练及 evaluation128 两点的驻留精度核验。

## 1. 实施原则与关键路线

先冻结通用契约和风险夹具，拆出原生／采集和拟合工具，再实现无 payload 的依赖执行、运行时配置及 SSD 映射；新对拍通过后才删除对应旧路径。V0.4 可作为迁移期参考，不作为新发布运行前提。

```text
P00 源码／测试／数据基线 → P01 通用契约 → P02 校验与SDK
                                      ├→ P03 独立原生 → P04 新采集／独立核对
                                      ├→ P05 模型接口 → P06 独立拟合
                                      ├→ P07 元数据Runtime → P08 DAG执行
                                      └→ P09 Layout → P10 设备配置
P04 + P05 + P08 → P11 内存接线
P05 + P07 + P09 + P10 → P12 传输／Controller资源 → P13 SSD映射
P04 + P06 + P11 + P13 → P14 用户入口与报告 → P15 分层Validator
P10 + P12 → P16 MQSim对拍
P02 + P05 + P08 + P12 + P14 → P17 扩展示例
P04 + P06 + P11 + P14 + P15 → P18 正式采集／拟合／CPU精度
P13 + P15 + P16 → P19 SSD账本验收
P16 + P17 + P18 + P19 → P20 清理与回归 → P21 干净发布／总交付
```

P01 完成后，独立原生、模型、运行时和配置可以按接口分别推进；这是任务依赖关系，不要求启动多个代理。精度未达时，继续不依赖该精度的配置、后端和教程工作；不得记总通过。

## 2. 目标模块与构建边界

以下为本计划采用的默认落点，首次实现时允许因实际依赖微调并记录理由；不改变 PRD 边界。

| 目标目录／目标 | 来源与责任 |
| --- | --- |
| `sdk/`，独立 CMake | 通用 IR／Measurement 元数据类型、采集 writer、封包基础和 hash；不含图搜索语法、SST/MQSim 或功能算子。JSON／hash 依赖显式声明和保留许可证，不借用 SST external 目录。 |
| `workloads/graph-search/`，独立 CMake | 迁入 native、core、功能算子和真实数据加载；含 benchmark／采集／测量及图特定适配、recipe、独立 oracle。通过显式 SDK 路径或安装包链接，隔离复制可独立构建。 |
| `extensions/graph/` | 图搜索算子描述、阶段／Work 规则、Host／Controller 映射和离线语义校验接线；功能实现不链接到仿真目标。 |
| `src/ir/`、`src/runtime/`、`src/timing/`（P07元数据底座）、`src/dag/`（P08静态执行） | 通用新 schema、类型／依赖检查、静态调度、元数据令牌、服务／返回门控及回收。旧动态接口迁移期共存，P20 删除。 |
| `src/mapping/`、`src/model/` | 编译期算子／映射／模型注册；通用资源队列、credit 和成本算法。图特定规则移出核心。 |
| `src/layout/`、`src/backend/`、`src/transport/` | 元数据布局、运行时设备配置、已有 MQSim 桥和读路径、size-only 传输。SST Core 不变。 |
| `python/ndpsim/` | 通用校验、训练／拟合、CLI、输出、manifest 和运行编排；负载专用配方由扩展提供。使用可安装包和 `ndpsim` console entry point。 |
| `configs/layouts/`、`configs/devices/`、`configs/models/`、`configs/experiments/` | 用户可编辑配置，提供 schema、默认样例、单位及支持范围；无构建时设备参数嵌入。 |
| `examples/extension/`、`tests/` | 最小接入教程；核心契约、映射、独立 oracle、包门控和发布检查。测试源码不成为生产依赖。 |

根 CMake 仍是模拟构建入口，继续使用 prepare_sst 和缓存；原生子工程不执行根构建。只安装／使用拟合工具不需要 C++、SST 或 MQSim。构建产物身份按实际文件重新计算，不能因只移动目录而豁免 Profile 绑定。

## 3. 必须先冻结的接口

P01 输出版本化 schema、头文件级接口设计和可执行最小夹具，冻结以下内容后再接线：

1. **IR**：新 `ndpsim-static-ir/2`，区分通用对象／端口／依赖与扩展语义。拓扑有序、查询内依赖，首版拒绝跨查询依赖和共享可变状态；每节点和每阶段有稳定 ID。包不得含调度用采集时间戳。保留流式完整性校验及索引，不复制完整业务数据。
2. **阶段需求**：语义 op 可对应多个 stage；包含阶段 Work、额外特征、准备上下文、chunk/segment 关联及输出字节。数据相关特征从真实采集产生；物理分块必须有可核验的 Work 分解规则。图配方规定 state／prepare／merge 的互斥归属。
3. **算子与映射**：OperatorSpec 声明端口／单位／版本／支持位置；Mapping 接收已验证元数据和有效配置，产出依赖明确的 read／transfer／reserve／compute／release 步骤。映射不得调用真实算法。
4. **CostModel**：输入 stage、位置、Work、上下文和目标硬件，输出非负整数 ns；注册公式与模型参数分开，缺模型／范围不符拒绝。模型只估服务，不含等待。新 Profile 格式和 Measurement 格式在此冻结，不复用旧 stage 白名单为通用规则。
5. **Runtime 与消息**：输出是元数据描述和完成令牌，数据大小使用显式计数，不用 Bytes 长度或同长度占位数组；协议头／关联 ID 可保留。完成、资源释放和后继提交的 phase 顺序明确。
6. **身份**：区分采集输入内容 hash、语义版本、模型来源构建、被模拟硬件、模拟器构建和执行宿主。无 payload 执行仍检查包和报告完整性，不伪造本次内容核验。
7. **调度**：沿用 offered/admission/worker_start/visible/drain 定义；内存严格点 1/1/1。通用依赖可并行，worker 策略需要的顺序由显式 ORDER／执行策略表达。索引或就绪窗口不能人为阻塞已可运行分支。
8. **配置权威**：逻辑对象来自 IR；物理映射来自 Layout；设备和 PCIe 参数来自 MQSim 配置；CPU／Controller／独立缓冲来自资源和模型配置。禁止重复隐式覆盖；当前执行宿主不自动成为模型目标硬件。

上述设计已由[P01契约](contract-v0.4.5.md)、schema和头文件声明冻结；通用采集SDK/封包校验已由[P02](capture-sdk-v0.4.5.md)实现；通用成本与算子注册已由[P05](cost-model-v0.4.5.md)实现；执行器/映射注册仍待后续任务，不是已可调用的完整模拟API。格式变更通过新版本声明；不提供长期旧schema兼容层。

## 4. 任务清单

状态：**P00/P01/P02/P03/P04/P05/P06/P07/P08/P09/P10/P11/P12/P13/P14/P15/P16/P17 完成；P18 已完成配方三版及 A01/A02 正式驻留 CPU 精度验证（见 docs/resident-accuracy-v0.4.5.md），P19～P21 未开始**。每项共同保存：需求来源、输入与有效配置、源码／依赖／构建身份、命令／退出码、原始输出、独立断言、产物 hash、失败根因与适用限制。只有对应出口具备实际证据后才更新状态。

### M1：冻结与通用接口

| 任务／依据 | 依赖、输入与改动落点 | 交付与验收出口 |
| --- | --- | --- |
| **P00 基线与迁移清单（已完成）**；FR11/12，AC10/12；SA-01/15/16 | 本次 snapshot、锁文件、CMake 测试注册及 tools/tests 清单；检查实际输入所在位置和获取方式，不先采全量。 | 保存 V0.4 参考身份及必要小夹具参考；逐文件／逐测试登记保留、改造、删除候选、替代断言、删除门槛；冻结正式矩阵和预算计划。旧通过仅作来源，缺失参考先补，不能直接标迁移等价。**实际出口**：[完成记录](baseline-v0.4.5.md)，262文件/93测试清单、矩阵/预算、构建及93/93 CTest、四组新小夹具/独立核对通过；缺失全量 prepared/原NSG及历史实体已登记，未计正式验收。 |
| **P01 契约与风险设计冻结（已完成）**；FR01/03～09；AC02/03/06～09 | P00；§3、SA-02～12。设计新 schema、注册接口、阶段输入、消息大小和资源归属表。 | 图搜索完整阶段和一个非图算子能表达；手算 fork/join、ORDER、零服务、返回门控、chunk 拆分案例；确认每项 Work 的来源和计费者，未决分解规则阻塞相应映射。**实际出口**：[冻结契约/检查记录](contract-v0.4.5.md)，5份schema、C++17接口声明、图完整阶段/非图算子及11项设计测试通过；矩阵供给/worker/slots/admission已冻结。尚未实现生产SDK/执行器，AC仍待验收。 |
| **P02 通用校验与采集 SDK（已完成）**；FR03/04/09；AC02 | P01；迁移 Recorder、封包及 v04_contract 的通用部分，图语法移出。 | SDK 显式提交依赖与元数据；流式封包／索引／校验；正例及环、缺边、重复、未知端口／算子、截断、篡改、超限负例。合成包标 fixture；结构通过与语义通过分列；公共层不出现固定 INIT/SELECT 语法。**实际出口**：[P02实现与检查](capture-sdk-v0.4.5.md)，独立SDK/安装消费、流式封包/磁盘索引及结构校验、合成完整包；16项测试/2项SDK CTest、安装后seal/check及根93项回归通过。结构与语义分列；P04图语义和其余AC出口尚待实现。 |

### M2：独立原生与成本工具

| 任务／依据 | 依赖、输入与改动落点 | 交付与验收出口 |
| --- | --- | --- |
| **P03 独立图搜索构建（已完成）**；FR02；AC01 | P02；CMake、native/core/operators/content 中原生必需部分，抽 SDK 公共依赖。 | 独立 CMake 和显式 SDK 引用；隔离源树无 third_party/SST 安装仍可构建运行；链接依赖检查、固定数值、原生结果／计数独立 oracle 通过。不得携带整个模拟 src 作为“独立工程”。**实际出口**：[P03实现与检查](native-v0.4.5.md)，独立负载/显式SDK源码与安装接入、最小功能依赖、隔离构建两组各3/3 CTest、独立60搜索/11查询功能与Work核对、系统库链接检查、根93/93回归及311文件迁移审计通过。旧raw observation仅保留能力，新格式图采集归P04；完整AC01/11仍待后续出口。 |
| **P04 新 IR 采集与语义校验（已完成）**；FR02/03/08/09；AC01/02/04 | P02/P03；原生边界、native_observation、validate_v04.expected_operations 和独立搜索核对。 | 采全阶段 Work、特征、chunk/segment 与依赖；benchmark 和采样分离；小夹具逐操作／阶段独立核对，A/B/A pilot、完整性和资源预算；保留真实 top-k 参考及校验报告，模拟包不依赖 payload 路径。**实际出口**：[P04实现与检查](capture-graph-v0.4.5.md)，真实图IR/2全阶段/Work/依赖、独立搜索/阶段oracle与Measurement/2；11项测试、隔离/ASan各4项、根94项通过；32查询合成pilot的33196操作/41446阶段与29031样本完整核对，9次A/B/A扰动中位0.1964%通过，低信号92项单列。正式SIFT/新AC及SSD准备上下文仍待后续出口。 |
| **P05 通用模型与注册接口（已完成）**；FR04/07；AC08/09 | P01；cpu_profile、host_cpu、Controller 公式。 | 常数、非负线性、表及现有 GBDT 算法从图规则分离；支持编译期扩展；对给定特征独立计算期望 ns，覆盖单位、舍入、零值、溢出、未知模型／范围及目标身份。去除模拟宿主必须等于测量机的隐式限制。**实际出口**：[P05实现与检查](cost-model-v0.4.5.md)，通用公式/算子注册、Profile/1严格加载及逐调用表，15项/228请求独立数值与负例、根95项、定向ASan、SDK2项/P02的16项/P01的11项/P00的3项通过，327文件/95测试迁移审计通过。未接模拟资源、未拟合或验收新AC。 |
| **P06 独立拟合／制表（已完成）**；FR08；AC08 | P02/P05；fit_v04_cpu_profile、fit_v04_merge_profile、bind_v04_merge_append。 | 通用 Measurement/训练 manifest → Profile/报告，图配方单独提供；隔离 Python 安装可运行、重复拟合确定性、训练评价分离、质量负例及表缺项／Work 不符拒绝。不再要求 V04-0/V04-2c 的历史成功目录。**实际出口**：[P06实现与检查](fit-v0.4.5.md)，独立Python拟合/制表/评价、外置图配方与质量门控；16项测试、四种公式/表由P05 C++消费、隔离安装/重复确定性、根96项、定向ASan及P00/P01/P02/P05回归通过；335文件/96CTest迁移审计，P04 pilot的29031样本复核通过但92低信号保留。未做正式拟合/精度或新AC。 |

### M3：元数据执行和配置基础

| 任务／依据 | 依赖、输入与改动落点 | 交付与验收出口 |
| --- | --- | --- |
| **P07 元数据 Runtime 与资源服务（已完成）**；FR01/07；AC03/10 | P01；runtime、HostCpu、FifoResource、BufferModel、EventBridge。 | 以描述／令牌替代 evaluate/Bytes；显式 Work／duration 输入；保留 FIFO/slots、异步四 phase、failure/drain、owner/pin 生命周期。独立手算、延迟注入、零服务、同刻 FIFO、失败不放后继及终态清零；无需功能算子链接。**实际出口**：[P07实现与检查](runtime-v0.4.5.md)，独立元数据Runtime/HostCpu/FIFO/credit、四phase与failure/drain；手算/延迟/零服务/slots1、2、4/owner-pin/失败不放后继通过，5项SST接线、根102项、定向ASan及P01/P00检查通过，342文件/102CTest迁移审计通过。未实现包级DAG/正式映射，未关闭新AC。 |
| **P08 静态 DAG 执行器（已完成）**；FR01/03；AC02/03/10 | P02/P07；新静态 scheduler 替代 Replay 分支解析，GraphHost 仅保留通用 SST 接线。 | 从索引加载就绪依赖和阶段；多分支 fork/join 不额外串行，四种边都门控；请求接纳／worker／完成／排空；窗口边界、物理记录合法重排、循环拒绝及大 Q 元数据回收检查。**实际出口**：[P08实现与检查](dag-v0.4.5.md)，原始IR索引/SQLite定位、完整查询就绪队列、四边Visible门控、FIFO接纳/worker/失败排空、通用SST GraphHost；18项DAG/3个SST变体、根104项、定向ASan2项、SDK/P02/P01/P00检查通过；Q1/64/512活跃stage峰值4/8/8且终态清零，353文件/104CTest审计通过。未接正式图映射/模型/CLI，未关闭新AC。 |
| **P09 无内容 Layout（已完成）**；FR06；AC07 | P01；ContentStore/Layout/ann_layout 和 Python 布局生成。 | 逻辑对象元数据到 extent 的独立解析器；图布局作为配置实例；普通／跨 sector/page／非对齐／空访问、重叠／容量／溢出负例；计算逻辑与物理字节的独立 oracle。模拟不构造 ContentStore。[实现与实际检查](layout-v0.4.5.md)；根CTest105/105，独立Layout及定向ASan通过；不计AC07完成。 |
| **P10 MQSim 运行时配置（已完成）**；FR06；AC05/07 | P09；device_profiles、profile.h、OrdinaryDevice、LifecycleBackend、preload_window。 | 外部 XML 解析一次并生成有效几何／partition，白名单与预算校验在设备分配前执行；不同路径运行、不重编译切配置；比对初态／预装载／CMT／排空。同步 oracle 配置输入，保留固定默认 XML 作为样例而非嵌入源码。**实际出口**：[P10实现与检查](device-config-v0.4.5.md)，严格外部XML/typed activation、有效几何/全资源partition、预算/namespace/Layout门控，设备/生命周期及PCIe/队列接线；根106项、配置11项、ASan4项通过，六个XML原生/fork/SST共同字段精确一致（SST各2次），368文件/106CTest审计通过；不计AC05/07或P16完成。 |
| **P11 内存 timing-only 接线（已完成）**；FR01/07/10；AC03/04 | P04/P05/P08；新 Host 映射、worker 策略、SST 接线，替代 replay_input/run_graph 的数据前置。 | 同一新包无 payload 运行，0 SSD 请求／无设备实例；阶段服务可追溯，1/1/1 时序手算，2/4 worker 配额定向检查。改变模型改变模拟时间，改变宿主执行负担不改变模拟时间；尚不宣称正式 CPU 精度。**实际出口**：[P11实现与检查](memory-timing-v0.4.5.md)，独立resident Plan/Profile→HostCpu接线、SST内存模块及逐阶段来源/窗口/排空trace；小图385阶段删除payload后运行、1/2/4 worker与独立slots/admission、模型变更/宿主竞争/实测表binding正负例通过；根108项、定向ASan4项、P00/P01及375文件/108CTest审计通过；不计AC03/04或正式CPU精度完成。 |

### M3/M4：SSD 映射、入口与验证工具

| 任务／依据 | 依赖、输入与改动落点 | 交付与验收出口 |
| --- | --- | --- |
| **P12 元数据传输与 Controller 资源（已完成）**；FR01/05/07；AC03/06 | P05/P07/P09/P10；messages/pcie、Controller、local_read、conversation。 | 大小明确的传输描述，去 query_values/scores/L2/content Handles；复用本地读、staging、compute、credit 与会话排空。独立算 wire/payload、PCIe 取整和方向；输入／服务／返回延迟注入、容量不足及在途失败清理。不得分配与逻辑 payload 同大小的占位缓冲。**实际出口**：[P12实现与检查](controller-timing-v0.4.5.md)，独立size-only消息/PCIe/本地读与Controller、P05需求来源、credit部分释放与Close排空；27项定向（含10项SST）、根135项、ASan19项、P00/P01及384文件/135CTest审计通过；保留首轮P11空格路径失败并修复测试参数传递。不计AC03/06或正式SSD账本完成。 |
| **P13 图搜索 SSD 两放置（已完成）**；FR05/09；AC06 | P04/P08/P12；extensions/graph 的 Host 普通读与 Controller 本地读计划。 | 同包更换放置，逻辑操作／Work 一致；物理 chunk 拆分有规则，query/IDs/adjacency/results/ACK 和 SQ/CQ 分层账本，Controller 完整向量 D2H=0；未知映射／不可分解 Work 拒绝；不把 resident_random Profile 默套到 SSD。**实际出口**：[P13实现与检查](graph-ssd-v0.4.5.md)，同IR/2包实际SST双放置、固定完整item分块与独立物理需求、SQ/CQ/平台/NAND/buffer分层trace；7项定向、根136项、ASan19项、P01/P00及390文件/136CTest审计通过，未知映射/规则/Work/实测表分摊拒绝，Controller向量D2H=0。仅小夹具，不计AC06或P19正式账本完成。 |
| **P14 统一 CLI／报告（已完成）**；FR06/10/12；AC07/11 | P04/P06/P11/P13；抽取 run_native/run_graph/run_config/run_experiment、experiment_output 的可用模块。 | 安装 `ndpsim`，子命令见 §5；有效配置与输入快照，错误退出码、预算和新目录输出；不自动构建或跑验收；报告区分参考结果、服务预测、实测表、目标硬件和宿主。相对路径、带空格路径与外部 cwd 验证。**实际出口**：[P14统一CLI/报告](cli-v0.4.5.md)，安装后采集/校验/拟合/三放置模拟/描述比较，资源预算及身份快照；verify明确拒绝未实现总验收，三层Validator仍属P15；定向7项、根137项、ASan2项、P01/P00及398文件迁移审计通过，不计AC07/11或总AC完成。 |
| **P15 三层 Validator（已完成）**；FR09～12；AC02/04/06/08/12 | P04/P06/P11/P13/P14；拆 validate_v04 和 compare_reference_paths 的独立核对。 | capture 语义／Work，simulate 调度／账本，native-vs-sim 精度分别生成报告；按稳定键关联但不依赖模拟器真实结果。删样本、篡改表、重复配置、少重复、quick／失败均不能出总通过。**实际出口**：[P15三层Validator](validator-v0.4.5.md)，安装CLI三层报告、全阶段稳定键/依赖/服务唯一计费、独立SSD账本及精度/质量重核；最终定向8项、根138项、P01/P00及402文件迁移审计通过。只关闭实现及夹具出口，未执行正式精度/SSD账本或12项AC；P16出口见下项。 |
| **P16 独立 MQSim 对拍工具（已完成）**；FR11；AC05 | P10/P12；从 verify_acceptance 的 AC-08 提取独立 oracle 导出／构建，不调用旧总 AC。 | 锁定原生 commit 独立导出；固定请求清单、共同配置／初态和独立账本，QD1/QD32/small-CMT 至少各两次；共同顺序／LBA／size／完成时间及指定状态统计严格一致。新增配置修改用例证明两边加载相同文件；平台扩展另由 P12/P19 验证。**实际出口**：[P16实现与检查](mqsim-oracle-v0.4.5.md)，锁定原生独立导出/编译，显式请求及Python独立XML/初态/页事务账本；QD1/QD32/small-CMT/修改XML四组原生及真实SST各2次严格一致；定向7项、根139项、ASan2项及4组显式请求、P01/P00与404文件/139CTest审计通过。保留失败，不计平台卸载/硬件校准或12项总AC通过。 |
| **P17 教学扩展全链路（已完成）**；FR04/05/07/12；AC09 | P02/P05/P08/P12/P14；examples/extension。 | 建议小型只读块求和：原生真实计算／采集，两个独立分支汇合；Host／Controller 映射、显式输入输出大小和独立常数或线性成本。仅扩展目录及注册清单变化，不改核心调度或 MQSim；教程可独立复现，无第二负载精度承诺。**实际出口**：[P17实现与检查](extension-v0.4.5.md)、[独立复现教程](../examples/extension/README.md)，原生真实求和/SDK采集与Python数值/Work oracle、两个独立分支汇合、同包真实SST双映射及独立字节/服务/返回/排空断言；5项定向、根140项、ASan定向1项（同5测试）、隔离原生构建/安装包教程/通用check、P01/P00及415文件/140CTest审计通过。只新增扩展目录和根注册（另同步文档/清单），保留失败；不计正式精度或最终AC验收，不开始P18。 |

### M5/M6：正式验收、清理与发布

| 任务／依据 | 依赖、输入与改动落点 | 交付与验收出口 |
| --- | --- | --- |
| **P18 正式采集／拟合与驻留精度（已完成）**；FR02/08/09；AC01/04/08 | P04/P06/P11/P14/P15；锁定全量输入、新原生构建、模型配方和预算。 | 执行 §6 采集与重复日程；全部128查询独立语义／Work核对、两点模型／表分列和严格 CPU 精度。先冻结训练模型再评价；未达保留失败，不调评价表当预测模型，不减少规模。**实际出口**：[P18执行记录](resident-accuracy-v0.4.5.md)。按用户授权重建并锁定完整 SIFT1M NSG，复用五点 tuning32、两点各三轮完整表采样和 A/B/A。三版将 INIT、merge_append、score_prepare、adjacency_prepare 列为逐调用表。五个预测阶段通过，evaluation128 两点各三次无探针 benchmark、两次确定性 SST 和参考情景独立账本及敏感性通过。AC01/04/08 保留至发布汇总。 |
| **P19 SSD 正式账本与条件预测**；FR05/07；AC06 | P13/P15/P16；同身份 evaluation128 两点包、合法 SSD 准备上下文模型、锁定 XML／Layout。 | §6 四个 SSD 配置全量执行；独立核对每操作映射与流量／缓冲／排空；只 SSD Host vs Controller 求收益。设备／链路／盘控标 configured_unvalidated，保存所有点，不以“下沉更快”为出口。 |
| **P20 删除旧路径并执行当前回归**；FR11；AC10 | P16/P17/P18/P19 和 P00清单；按 §7 逐项迁移。 | 去除 direct/动态IR/旧Replay、旧默认和冗余公开脚本；移除生产功能链接，保留原生功能库及独立 oracle。检查 imports/CMake/文档引用无悬空；清理后的根构建、全部当前 CTest、定向 ASan及受影响正式出口复验，不能使用删除前身份直接宣称最终通过。 |
| **P21 干净发布与总验收**；FR10/12；AC11/12 | P20 及全部前序出口；公开依赖／数据准备、可安装 Python、独立 native、配置／教程／许可证。 | 无维护者 results/cache 依赖的干净目录构建与最小全链路；执行最终身份对应的完整验收，12 AC 全覆盖，完整证据和限制；更新 README/架构/扩展/测试/发布记录。只经授权才进行 Git 提交、推送或发布。 |

## 5. 计划命令与运行职责

以下为 P14 已实现入口，详见 [CLI 使用说明](../python/CLI.md)。`compare` 只做描述性比较；`verify` 已由P15接入三层Validator（[格式与用法](../python/VALIDATION.md)）；未知/总验收spec仍退出3，完整12项AC聚合留待P21，层级通过不计总验收。

```sh
# 独立原生：显式 SDK 源路径；无 SST/MQSim 前置
cmake -S workloads/graph-search -B build/graph -DNDPSIM_SDK_ROOT="$PWD/sdk"
cmake --build build/graph -j2

# 模拟：继续使用根构建和固定 SST 缓存
cmake -S . -B build/v0.4.5 -DBUILD_TESTING=ON
cmake --build build/v0.4.5 -j2
ctest --test-dir build/v0.4.5 --output-on-failure

# CLI；独立图程序也保留自身 benchmark/capture/sample 命令
ndpsim capture --workload graph --adapter workloads/graph-search/python/graph_capture.py --native <native-binary> --operators extensions/graph/operators.json --config <capture.json> --output <new-directory>
ndpsim check --package <package> --registry <registry.json> --adapter workloads/graph-search/python/graph_capture.py --report <capture-validation.json>
ndpsim fit --manifest <training.json> --budget <execution-budget.json> --output <new-directory>
ndpsim simulate --config <experiment.json> --build <build-directory> --output <new-directory>
ndpsim compare --spec <comparison.json> --output <new-directory>
ndpsim verify --spec <validation.json> --output <new-directory> # P15三层证据spec；完整AC聚合属P21
```

`check` 区分结构和业务语义验证；后者由负载扩展在原生侧访问原数据。`simulate` 只读包、配置、Profile 和校验报告，不要求输入数据集。`verify` 负责构建检查／完整日程及聚合，不隐藏在 simulate 中。原生 benchmark、CPU sample、capture 分别运行；便捷 capture 不把采样墙钟写为调度时间。

## 6. 正式验收矩阵与采集日程

### 6.1 端到端配置

固定完整 1M×128 base、原 NSG、evaluation128 原始 ID/顺序、k=10、B=1、语义 chunk=16；两点 L/E=32/64 与64/128。初始配置依据 PRD 和既有锁定参数，P00/P01 输出机器可读清单，不通过改变数据或算法满足精度。

| ID | 路径 | L/E | 模拟重复 | 对应比较 |
| --- | --- | --- | ---: | --- |
| A01 | 内存 Host，worker/slots/admission=1/1/1 | 32/64 | 2 | 三次独立原生 benchmark；严格精度 |
| A02 | 同上 | 64/128 | 2 | 三次独立原生 benchmark；严格精度 |
| B01 | SSD Host | 32/64 | 1 | 与 C01 相同语义与设备；账本／条件收益 |
| C01 | SSD Controller | 32/64 | 1 | 与 B01 比较 |
| B02 | SSD Host | 64/128 | 1 | 与 C02 比较 |
| C02 | SSD Controller | 64/128 | 1 | 与 B02 比较 |

共 **6 个端到端模拟配置、至少 8 次运行**。CPU 逐阶段计费校验、训练、采集、原生重复、MQSim 对拍、教学示例及定向测试另计，不称为旧 12 或52配置验收。SSD调度策略已在[P01](contract-v0.4.5.md#6-配置权威和正式调度冻结)冻结为offered=0、FIFO、worker/slots/admission=1/1/1，Host/Controller 保持相同 offered/admission 与可比资源条件，配置差异清单显式列放置及必要的准备上下文。

### 6.2 采集、模型与重复

- 正式新格式包：tuning32/evaluation128 × 两个 L/E，共4个完整包；采集身份改变则重采。每包的指定实测表阶段至少三次独立 `cpu_sample`，共至少12次，逐调用覆盖；不能用这些调用代替独立 benchmark。
- 预测训练 recipe 默认保留现有 tuning32 的五点采样方案（L/E=16/32、32/64、64/128、128/256、256/512），用于特征覆盖与训练留出；不是增加正式精度点。P06 冻结样本步长、质量规则和特征，重构身份改变需新采；训练四折按原始 query ID 分组。任何调整记录理由且只能使用训练数据。
- 新采集器在正式采集前做小规模 pilot；训练主点做三组 A/B/A，合计9次，扰动≤5%。A/B 模式和测量窗口沿用现行计时规则，在执行前明确，不在看过结果后选择组合。
- 两个 A 点各三次无 CPU 探针的原生 benchmark，共6次；两次模拟逐项确定性核验。采集、制表与 benchmark 均分别保存身份和目录。
- Level A 对两个切分两点分别验证全部阶段计费；evaluation 可使用 A 点详细服务证据，tuning32 另跑计费校验，不与正式 evaluation128 精度混为一谈。
- 模型阶段误差中位≤10%、P90≤20%；两点分别 latency P50≤15%、P95≤20%、QPS≤10%、CPU需求≤15%。实测表只验覆盖／质量／身份／Work／唯一计费，不充当预测误差通过。
- 2/4 worker 用小夹具核验配额、顺序和回收；全规模性能仅诊断可选。SSD 算力敏感性扫描可选，不设置隐含必跑旧矩阵。
- 每次全规模操作先核验 RAM／磁盘／输出预算／超时。资源实际值须本机重取；本计划不复用早期16GiB作为本轮可用资源证明。

### 6.3 AC 到任务／证据映射

| AC-045 | 主任务 | 必需证据 |
| --- | --- | --- |
| 01 | P03/P04/P18 | 隔离独立构建、链接、完整采集与原生独立功能／Work核对 |
| 02 | P02/P04/P08/P15 | 新包完整性与依赖正负例、全查询语义证据 |
| 03 | P07/P08/P11/P12 | 无payload运行、无功能链接／调用、延迟注入和零服务门控 |
| 04 | P15/P18 | A01/A02重复与逐查询／逐阶段报告，PRD全部精度门槛 |
| 05 | P10/P16 | 原生独立源码身份、相同配置／初态／请求及严格比较 |
| 06 | P12/P13/P19 | B/C四点独立账本、下沉数据路径、缓冲及排空 |
| 07 | P09/P10/P14 | 不重编译切配置、布局边界、非法配置、有效快照 |
| 08 | P05/P06/P15/P18 | 模型／表分列、训练隔离、质量、缺项拒绝和唯一计费 |
| 09 | P17 | 教学扩展源码差异、采集及双映射可复现输出 |
| 10 | P00/P20 | 测试逐项处置、当前根CTest与资源／错误路径覆盖 |
| 11 | P03/P06/P14/P21 | 无模拟依赖的原生／拟合；干净目录、统一CLI教程 |
| 12 | P15/P21 | 最终12项AC矩阵、身份、命令／退出码／hash、删改负例和限制 |

## 7. 测试与工具迁移决策

下表是已根据源码确定的按职责处置方案。完整文件库存见核查证据；P00 将其展开成逐项清单并检查 import／CMake 引用，P20 执行删除。标“删除”指新版本入口／代码的候选处置，不在本轮删除，也不删历史证据实体。

| 现有文件／测试组 | 处置与替代出口 |
| --- | --- |
| event_bridge/formal_bridge、ordinary_io、local_read、lifecycle、observation、pcie probes | 保留有效因果／服务／账本断言；配置和元数据接口迁移，P07/P10/P12/P16 接管。原生事件循环夹具仅测试 oracle，不作为生产第二时钟。 |
| runtime_probe、content_probe/content_backend_probe、mapped_content、profile_probe | 分拆：Runtime元数据门控和Layout范围迁入核心；真实内容／mmap验证迁入原生工程；设备配置改运行时；旧句柄功能可见性用令牌生命周期测试替代。 |
| operator_probe、search_core_probe、test_native_search、test_search_core、test_native_observation | 功能／数值 oracle 迁原生，Host公式迁模型测试，采集断言改新格式。保留独立实现，不仅比较共用函数。 |
| controller_probe/controller_resource_probe、pcie_contract_probe | 保留容量、信用、方向、分块、失败排空；去真实 L2／score payload，增加元数据大小和映射测试。 |
| test_static_replay、test_v04_contract、cpu_profile_probe、test_v04_cpu_fit、test_v04_validator | 重写为新包／阶段特征／元数据执行／独立拟合和三层Validator；旧“真实值重算”断言迁采集侧，不直接删功能正确性。 |
| ir_* 动态前端／schema／mapping／comparison | 类型、依赖、生命周期、账本断言迁新通用IR；仅 direct/动态ir一致性和旧固定语法断言在替代测试通过后删除。 |
| test_direct_baseline、test_reference_paths、compare_reference_paths | 冻结参考比较仅迁移期使用；拆出独立数值／搜索／Work oracle到原生侧，旧多引擎比较删除。 |
| test_v02/v03/v04_delivery、test_v04_regression、test_ir_matrix、test_t6_matrix | 取消旧配置数量／兼容出口；把缺项、篡改、quick、失败不出成功等有效门控迁新验收测试。原始 query selection 数据和锁仍保留。 |
| verify_acceptance、verify_m0及相关依赖 | 提取独立 MQSim oracle、预算／证据和仍有效公式测试；不因沿用底层帮助函数继续调用旧总AC。移除历史工具前逐一拆除 import。 |
| run.py/run_config.py/run_graph.py/run_pair.py/run_experiment.py | 用户入口收敛为 CLI；通用运行／导出逻辑归内部模块，数据生成与truth归原生；旧公开命令不保留转发兼容。 |
| collect/evaluate/fit/bind/verify_v04 等阶段工具 | 采集／拟合／比较／验收迁通用包与graph recipe；仅历史编排和固定结果路径的脚本移出发布入口。 |
| benchmark_data、simulation_input、ann_data、图构建／真值准备 | 不作为仿真前置；数据准备和校验仍为独立原生复现必需，保留／迁入负载工具。不可为精简而删除数据获取能力。 |

清理出口还须检查模拟二进制链接图中无图搜索功能库、CLI import 无旧阶段工具依赖、生产配置不引用旧 engine、公开文档无无效命令。保留旧文档链接用于历史追溯时显式标历史。

## 8. 证据、失败处理与完成规则

建议每任务保存于新的 `results/v0.4.5/<task>/<run>/`，包含 manifest、commands、checks、原始日志、有效输入快照及 hash。支持在用户指定外部目录运行；路径本身不是验收身份。正式发布证据须可重新生成，不能要求本机此目录已存在。

开发检查、小夹具、pilot、训练、evaluation、条件预测及完整 AC 分开记录。失败保留目录，重跑新目录；缺模型、范围或身份问题不静默回退。身份变更按依赖清单使受影响证据失效：源码迁移至少重构建并重验语义；原生计时身份变化重采／重拟合；调度／映射变化重跑相关对拍和精度；纯文档变更只做文档检查。

P21 总交付以清理后最终执行源码为准。测试数量不是正确性门槛；12项AC的必需断言、重复和输入覆盖才是门槛。旧V0.4/V0.3通过、复用未核验产物、quick或未执行项均不能计新版本成功。

## 9. 风险门槛与可继续工作

| 风险／待核实事项 | 阻塞与处理 |
| --- | --- |
| 新包缺少阶段特征或不可分解Work | 阻塞对应模型／映射；P01/P04用完整特征清单和独立核对关闭，可继续通用Runtime和配置。 |
| DAG窗口额外串行或元数据失控 | 阻塞执行器出口；P08用fork/join跨窗口、Q梯度及预算夹具验证，不扩大为任意DAG无限规模承诺。 |
| size-only消息改变wire或同刻顺序 | 阻塞SSD映射；P12/P16核对旧协议字节公式及MQSim完成顺序，不能用放宽百分比容差解决。 |
| 原生拆分后精度未达／采集扰动超限 | 阻塞AC01质量、AC04/08和总交付；保留失败，训练侧修复并版本化，不用evaluation调参；配置／教程／后端可继续。 |
| 新XML几何／partition／初态不一致 | 拒绝相关配置；P10独立解析核对支持子集，不承诺任意MQSim选项。 |
| 真实输入或资源预算不足 | 阻塞全规模采集／验收，不阻塞接口和小夹具；不得缩小规模后记正式通过。 |

P00/P01/P02/P03/P04/P05/P06/P07/P08/P09/P10/P11/P12/P13/P14/P15/P16/P17已关闭；**P18** 已通过配方三版及两点正式驻留 CPU 精度核验；**P19～P21** 均未开始。正式实现前不需要再次决定目录命名等常规细节；如需改变PRD范围或精度门槛，应单独明确变更，不能在任务实现中隐式降低要求。
