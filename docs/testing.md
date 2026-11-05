# 持续维护的测试与验收

V0.4采用独立的 [12配置验收矩阵](validation-matrix-v0.4.md)（[`tools/verify_v04.py`](../tools/verify_v04.py)，[复现与证据](releases/v0.4.md)），配置数不含原生/模拟重复、训练、A/B/A、detail8及定向/继承测试。交付保留根构建/全CTest、完整V0.1 AC、T3/T4/T5及相关IR定向与独立核对；V0.3的52条及配套效率/Q梯度按矩阵触发条件执行。旧52条门槛和以下现行命令语义不改，少跑不能标作完整V0.3通过。

修订后的V0.4 A/D/O须显式静态IR执行，R组继续direct/动态ir。[原生采集和离线包校验](profiler-v0.4.md)已实现，仿真执行及 SSD 接线已完成，实际边界见 [静态回放](replay-v0.4.md) 和 [SSD 条件预测](offload-v0.4.md)。持续回归须覆盖完整包与查询/终态/依赖/状态负例、真实值及Work核验、延迟门控/未来地址旁路、同包成本切换、旧包拒绝/重采、流式回收和同映射旧成本下的静态/动态/direct时序账本对照；全evaluation128执行IR不能抽样或缩为detail8。具体出口见 [PRD](prd-v0.4.md#83-必须覆盖的功能与负例) 和矩阵，不降低CPU精度标准。

从仓库根目录运行完整验收：

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/verify_acceptance.py
```

入口通过根 CMake 配置/构建平台，默认共享缓存准备锁定 SST；运行全套 CTest，然后运行下表矩阵。`--build`、`--output` 可指定构建及证据目录。`--quick` 只运行 CTest 小矩阵，要求已有构建，不包含完整并发、慢变体、原生和 ASan，产物明确标为 `quick subset`，不能替代完整验收。

| AC | 持续回归断言与输入 | 证据相对 `docs/evidence/m4.2/` |
| --- | --- | --- |
| 01 | 两种 CLI 放置输出 top-k/距离/独立 Recall、模拟延迟和配置 | `branch-host/`、`branch-controller/`；默认输入见 `default-*/` |
| 02 | 仅 x1=1→5，两放置第二轮扩展 1→2，独立邻接 LBA 6→7；查询/图字节不变 | `branch-*/`、`mutated-*/` |
| 03 | 真实 SST 受控链：输入/计算/返回各加 100 ns，后继 55→155 ns，独立查询维持 7 ns；真实 MQSim 单 sector 读取增加 NAND 延迟 100 ns | `causal-*.log`、`nand-*.log` |
| 04 | 每个受控链四次 `try_get` 拒绝尚在服务/返回的私有值；真实值 42→43 决定后继；图核对决策不早于数据/返回到达 | `causal-*.log`、图 `events.jsonl` 及比较报告 |
| 05 | 独立离线批次重放候选/扩展/top-k；默认 N2048/d32/Q32，容量分块、d260 跨页/双 slots | `*-comparison.json` |
| 06 | 向量 D2H=0；query/IDs/得分/邻接/ACK 与普通 SQ/CQ/doorbell 的 payload/wire、请求/事务守恒；每次传输恰有一组 enqueue/start/arrive | `*-comparison.json` 及对应事件 |
| 07 | 两放置并发 1/2/4；单工作块正 credit 等待、峰值有界、接纳降额；过大请求与六类安全失败原子拒绝或排空 | `concurrency/manifest.json`、`failure-*.log` |
| 08 | 从锁定原生 commit 独立导出/编译；QD1/QD32/小 CMT 与真实 SST 各两次逐请求/初态完全一致 | `native-*.log`、`qd*-*.log`、`cmt-miss-*.log` |
| 09 | 从事件独立重算 channel/die 并集及流量/请求计数；JSON/CSV 行数、分位数样本、plane/latch Unsupported | `concurrency/manifest.json` |
| 10 | 默认并发 1/2/4 top-k 不变；日志开/关/重复全部模拟统计相同；分别编译快/慢 L2，比较两放置整个事件流、结果与统计 | `concurrency/`、`fast-*/`、`slow-*/` |
| 11 | 预就绪主机计算/链路隔离公式；顺序图两放置仅改距离 rate，服务和总延迟变化核对 | `host-formula.log`、`link-formula.log`、`*-rate*/`、`parameters.json` |

完整矩阵输出 `ac-matrix.json`，每项记录断言、证据和状态，只有全部 11 项具备通过断言才输出完整通过。`manifest.json` 保存命令、环境覆盖、退出码、源文件/输入/产物哈希、MQSim 相对原生基线的 diff/实际源码哈希和完整依赖提交。原生导出使用新的独立目录，归档和源码哈希保存在 `parameters.json`，不从已修改工作区构建 oracle。原始日志/数据/结果保存在仓库内的本机 evidence 目录（按既有约定不纳入 Git）；新检出用上述入口重新生成。

计算与 staging 采用 `setup + ceil(work/rate)`（staging 的 setup=0）。隔离主机用 27 FLOP：`3 + ceil(27/4)=10 ns`；公式探针另用 28 FLOP、rate=8 验证 7 ns。主机 FIFO 单 slot 完成为 10/15/25 ns、双 slot 为 10/5/15 ns。PCIe 保持 MQSim 原生量化：四 lane 时 `floor(wire/(4*lane_GBps))`；100/76 B 消息、lane rate 1/2/1000000，分别核对双向并行、同向 FIFO 和零 ns 服务，不修改原生取整。

慢功能变体以 `-DNDPSIM_SLOW_FUNCTIONAL=ON` 单独编译，仅重复相同顺序的 FP64 L2 运算 2048 次，使用 volatile 保证执行并逐次检查数值等价；对外工作量/结果字节保持一致。默认 OFF；该开关是仿真器实现开销实验，不能当硬件参数。快/慢两放置使用同一数据，断言慢变体实测墙钟增加，所有模拟事件、结果、资源统计完全一致。仅墙钟字段从统计比较中排除。

真实 NAND 扰动使用普通 I/O 验收组件的 `single_read/read_delay` 参数；默认不影响既有普通请求。新进程加载相同 XML 后给三类 NAND read latency 加相同增量；单次读取 LBA0/512 B、数据缓存关闭、CMT 命中，断言只有一段 array 活动、无映射读取，完成时延与 array 时长均增加 100 ns。受控运行时链中的输入服务由平台夹具驱动；它验证异步语义，不冒充 NAND 读取，这一实际 NAND 实验补齐存储因果。


## 测试与产物分类

- `tests/` 的 probe、scenario 和 fixtures 均保留；根 CMake/CTest 是常规回归入口，T8交付轮非 ASan 配置实际注册并通过67项。测试数量以实际发现为准。
- `tools/verify_acceptance.py`、`verify_concurrency.py` 和 `compare_placements.py` 持续维护；CMake/CTest 已同步调用路径。`verify_contract.py` 是独立契约/ABI 账本核查，不在 CTest 中。
- `configs/regression/` 保留所有持续回归探针/分支夹具（仅历史工具使用的 M0 预装载配置已移入 `configs/archive/v0.2/`）；`configs/experiments/` 为当前默认实验，`configs/sst/graph.py` 是实际组件接线；`configs/native/` 保持不变。
- 历史阶段入口归入 [tools/archive/v0.1](../tools/archive/v0.1/README.md)，三个旧 smoke 配置归入 [configs/archive/v0.1](../configs/archive/v0.1/README.md)。保留复现能力，不默认执行全部历史阶段。完整 AC 自行导出原生 oracle；历史 macOS 原生工具仍可按需运行。
- `build/`、`.cache/`、`results/`、`docs/evidence/` 是已忽略的生成产物，保留本机历史数据，不承诺新检出存在。清理前确认无运行占用、无唯一证据；本次未删除。
- 历史阶段方法见[V0.1 归档](archive/v0.1/README.md)和[V0.2 归档](archive/v0.2/README.md)；已交付基线见[V0.2 记录](releases/v0.2.md)。未运行完整矩阵时不得继承历史通过结论。

常用检查（仓库根目录）：

```sh
cmake -S . -B build/delivery -DBUILD_TESTING=ON
cmake --build build/delivery -j2
ctest --test-dir build/delivery -N
ctest --test-dir build/delivery --output-on-failure
SDKROOT="$(xcrun --show-sdk-path)" /opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/verify_contract.py --output results/contract-check
```

独立契约脚本直接调用 Clang；在本机显式提供 SDKROOT，避免找不到 C++ 标准头。根 CMake 已自动选择 SDK。输出路径须选择未使用目录。环境要求见[README](../README.md)；ASan 是独立探针，不是完整 SST 泄漏审计。

## 继承的真实数据、资源与规模验证

V0.3 必须保留这些测试与独立核对器；名字中的阶段编号仅用于兼容旧入口。当前有效 CTest 数量以实际发现为准，历史67项不是本轮通过证据。

| 覆盖 | 持续验证与入口 |
| --- | --- |
| 数据/schema | `tests/test_m0.py`、`test_benchmark_data.py`、`test_benchmark_artifacts.py`；完整原始文件验证、查询分割、独立完整排序小 oracle、分块 top-K、绑定/边界/可达性、同分和不足 k |
| 真实图与真值 | `tools/verify_t2.py`；SIFT 10k 重排子集、32条诊断query、精确真值与完整base官方ID-only导入；[接口](index-truth-v2.md) |
| 输入与闭环 | `simulation_input_schema`、`tools/verify_t3.py`；相对路径/身份/入口/K校验，原输入及向量扰动的两放置逐批、距离与账本；[接口](simulation-input.md) |
| 内容与容量 | `device_profiles`、`mapped_content`、三种profile预检/越界，`tools/verify_t4.py`；memory/mmap × host/controller、d128/d96并发4、d260跨页/接纳降额、真实小SIFT；[接口](content-profiles.md) |
| 回收与流式输出 | `observation_lifecycle`、`streaming_output`、`tools/verify_t5.py`；任务/lease/区间回收、失败在途completion、外排序、Python堆界；Q32/128/512两放置各三次、日志等价、预算拒绝与可选真实SIFT核对；[接口](observation-lifecycle.md) |
| SIFT矩阵与复用 | `sift1m_matrix`、`tools/verify_t6.py`；冻结26点、百万行按需读取、仅排除墙钟的统计比较、配置/输入/模型/元素/产物篡改拒绝、流式日志及因果错误拒绝；[完整矩阵](sift1m.md) |
| V0.2 总验收 | `tools/verify_v02.py --all`；根构建、完整V0.1 AC、T3/T4/T5、26次SIFT及主配置两放置新复现；[命令与前置准备](archive/v0.2/delivery.md) |

`tests/test_v02_delivery.py` 的三类失败关闭回归由 V0.2 总入口执行，覆盖完整适用 AC/N/A、缺项/失败/quick 拒绝、产物篡改/缺失拒绝；也可单独运行；V03-7现已将其注册为 `v02_delivery_gates`，与新增 `v03_delivery_gates` 一并持续执行。

完整 V0.1 AC 仍包含独立锁定原生 oracle 和 ASan；慢功能夹具为 N256/d128/R8/Q8、k10/L16/E16，要求两放置墙钟慢化且全部事件/结果/模拟统计一致。V0.2 AC-02-10=N/A（Deep10M取消），不能混同仍须执行的 V0.1 AC-10。

历史阶段命令、成功与失败证据统一见[V0.2 归档](archive/v0.2/README.md)及[交付记录](releases/v0.2.md)。V0.3 新增 direct/ir 等价、动态依赖、资源回收与52条矩阵等要求见[PRD](prd-v0.3.md)，V03-0～6的分阶段证据见下节；V03-7已完成完整总交付与默认切换，见[交付记录](releases/v0.3.md)。


## V03-0 冻结检查

`tools/freeze_direct_baseline.py` 对锁定准备输入运行新的 direct 小规模、完整base评价128和详细8查询两放置，保存源码/输入/配置/产物身份、命令/退出码、独立核对和预冻结预算；不复用历史运行结果。方法、实测及限制见 [V03-0](archive/v0.3/direct-baseline.md)。`tests/test_direct_baseline.py` 单独执行，覆盖证据缺失/篡改/失败、非法清单及资源冻结拒绝；本轮3/3通过，未注册到原67项CTest中。它们不代替完整AC或IR验收。

## V03-1 结构契约检查

根 CTest 新增 `ir_schema_catalog`（9组Python检查）和 `ir_schema_memory`（C++强类型/variant及往返），当前本轮69/69通过。独立静态入口、schema生成/导出、实际证据和未执行的后续验收见 [V03-1](archive/v0.3/ir-schema.md)。这些检查不证明IR已经执行搜索。

## V03-2 动态依赖与值生命周期

`ir_dynamic_lifecycle` 使用合成结构夹具及受信测试 adapter，覆盖增量接纳、非法引用、ADDRESS/DATA/CONTROL/ORDER、可见值驱动动态后继、版本化状态所有权、空/零时长 JOIN、失败排空、同刻提交顺序和 2,001 JOIN 回收。`sst_ir_base/input/compute/return` 通过真实 SST SelfLink 验证三阶段延迟与非提前可见，输出逐任务因果状态日志。V03-2 当轮仅 JOIN 有内置执行实现；其余测试 adapter 不替代后续 V03-3 的真实资源映射。

根 CTest 已包含这些用例。定向 ASan 通过 `-DNDPSIM_SANITIZE=ON` 独立构建，运行 `ir_dynamic_lifecycle`、`runtime_dependencies_visibility` 和 `ir_schema_memory`；不是完整 SST/LSan 审计。实际命令、74/74 CTest、3/3 ASan、冻结输入证据重查及限制见 [V03-2 收口](archive/v0.3/ir-lifecycle.md)。未运行的完整 AC、真实搜索矩阵和 RSS 梯度不计通过。


## V03-3 算子与 placement 映射

`ir_operator_placements` 运行测试专用动态 Driver 与原 GraphSearch 的真实 MQSim 四组合；20次独立进程覆盖跨页 d260、多chunk、单块容量、同刻 CPU 竞争、提前 Close、原子拒绝、部分提交和 Controller/Open 失败排空。逐字段比较结果/批次/时刻/Work/普通与本地读取/消息/link wire/credit/资源服务，失败也要求终态清零。

`tools/verify_ir_mapping.py --output <新目录>` 使用锁定 Python 运行根构建、完整 CTest、映射/schema/证据检查、冻结 direct smoke 新运行与独立 ASan 4项；保存失败关闭的 manifest、输入/源码/依赖身份及命令/log/hash。本轮最终75/75 CTest、4/4 ASan、10组 schema、3组冻结证据测试通过；方法、失败记录与限制见 [V03-3](archive/v0.3/ir-mapping.md)。不包含完整 AC、原生 oracle、生产 IR engine 或完整 SIFT 矩阵验收。

## V03-4 动态 GraphSearch 前端

`ir_graph_frontend` 复用真实 MQSim 探针，但常规/小容量/同刻竞争改用生产 IrGraphSearch；旧边界驱动仍单独保留。`sst_ir_graph_frontend` 运行16次合成生产SST四组合，覆盖真实向量扰动、并发2/B2/chunk2、B>E，以及非法engine/错配contract拒绝。`ir_graph_frontend_causality` 使用受信合成服务门控，验证三阶段100ns延迟、提前get拒绝、独立查询及回收；不冒充真实NAND成本。

锁定Python运行 `tools/verify_ir_frontend.py --output <新目录>`，保存根构建、完整CTest、真实SIFT10k（Q32，原始/固定图向量扰动/B2并发）12次新运行、冻结direct两放置重跑、定向ASan及源码/依赖/hash。V03-4本轮78/78 CTest、ASan2/2通过，详见[收口](archive/v0.3/ir-frontend.md)。测试内的局部比较函数不替代V03-5正式独立比较器；没有完整AC、原生oracle、完整SIFT1M矩阵或效率验收，不切换默认engine。

## V03-5 观测与独立比较器

根CTest新增 `ir_observation_comparator`（跨页d260、B2/chunk2、并发2的两engine/两placement/statistics-detail八次真实SST运行、19项比较器对抗检查及IR日志预算拒绝）。生产前端回归也使用正式比较器；保留既有独立搜索/数值/账本检查。Python schema回归使用本机已有jsonschema 4.26.0及referencing。

锁定Python运行 `tools/verify_ir_observation.py --output <新目录>`，核对V03-0～4依赖hash、根构建/全部CTest、新运行合成及真实SIFT10k八组合、独立账本、冻结direct两放置与源码/依赖稳定性。最终79/79 CTest、各项对照与对抗检查通过；见[V03-5命令、证据、开发失败与限制](archive/v0.3/ir-observation.md)。这不运行V03-6或V03-7，不接受完整矩阵、效率或交付。


## V03-6 完整矩阵与效率门槛

锁定Python运行 `tools/verify_ir_matrix.py --output <新目录>`；RAM在受限沙箱下可用 `--ram-evidence <本轮sysctl stdout>`。入口预检冻结输入/依赖/资源，根构建和全CTest、定向IR ASan后新跑52条完整SIFT1M矩阵、补足两placement各三组交替和18次Q梯度。无历史仿真复用/quick选项，失败或缺项不能通过。实际命令、全部样本、质量、独立账本、资源口径和失败记录见[V03-6](archive/v0.3/ir-matrix.md)。

本轮80/80 CTest、定向ASan4/4、52/52及效率/Q门槛通过。新增 `ir_matrix_gates` 五组失败关闭检查；原有比较器19项对抗、预算拒绝、schema、因果与资源回归保留。此次不是V03-7总交付：不运行完整继承AC/独立原生oracle，不切换默认engine。


## V03-7 总交付入口

锁定 Python 运行 `tools/verify_v03.py --all --ram-evidence <本轮sysctl stdout> --output <新目录>`。完整重跑根构建/全部CTest、52条矩阵/效率/Q梯度、完整继承AC/原生oracle、T3/T4/T5、IR ASan和默认入口复现。无quick选项；可选矩阵复用须全执行/验证源码、构建库、输入、配置、依赖与产物身份匹配，缺项/失败/篡改不得通过。`v03_delivery_gates` 和 `v02_delivery_gates` 已注册CTest。日常默认ir；继承自动化基线显式direct。AC映射、实际命令和限制见[V03-7](releases/v0.3.md)。

本轮 V03-7 实际根 CTest **82/82**、完整 V0.1 AC **11/11**（含原生 oracle、慢功能、继承 ASan **17/17**）、V0.2 T3/T4/T5、矩阵 **52/52** 和三组效率/18次Q梯度均通过；定向IR ASan额外 **5/5**。V0.2 T6覆盖由本轮52条矩阵及保留的 `sift1m_matrix`/`ir_matrix_gates`、严格复用校验和独立账本提供；未将旧V0.2的26条或473条断言冒充本轮独立重跑。

## V04-0 开工门槛

根CTest新增 `v04_start_gates`（`tests/test_v04_start.py`，5组）：拒绝缺失/篡改产物、越界路径、失败/重复命令、失败/缺失断言、错误任务/schema及矩阵/配套重复日程漂移。完整开工入口为 `tools/freeze_v04_start.py`，实际命令、83/83 CTest、完整输入/历史参考核验、四次新direct/默认IR smoke与独立核对见[冻结记录](start-freeze-v0.4.md)。本项不表示原生或CPU精度通过，也不替代完整继承AC。

## V04-1a 纯核心持续回归

根CTest `search_core_oracle` 用独立Python数值/搜索与计数公式核对60组小搜索的逐阶段packed结果、评分分块顺序和全部七字段Work；覆盖重复边/自环/跨批重复、同分、空邻接/断连、不足k、预算、B>E、多chunk及非法输入。接口、实际根构建/84项CTest和四组合冻结smoke证据见[纯核心收口](core-v0.4.md)。不代表V04-1c三路径、完整规模或CPU精度通过。

## V04-1c 参考语义回归

`reference_path_comparator` 覆盖独立计数、缺失和变更观测、读顺序及距离容差开关。`native_search_batch` 另检查显式开启的评分分块和末轮选择。三路径完整主配置及定向验证入口见 [V04-1c 参考语义](reference-semantics-v0.4.md)。


## V04-2a 契约回归

CTest `v04_contract` 验证 [静态包与计时计费契约](contract-v0.4.md)。合法两查询夹具覆盖四类边、状态、完整查询索引与尾记录。负例重算分片、索引和包哈希后篡改依赖、状态、查询覆盖、Work、终态及其他字段，另验证内容绑定、截断、诊断格式、单位、CPU 区间互斥及配置组合。此测试是合成契约验证。真实采集、执行门控、模型拟合与精度出口单列。

CTest `native_observation` 用独立搜索 oracle 检查原生四档的结果、Work、静态逐操作摘要及 detail8，重建 CPU 采样位置，并验证缺失、截断、预算与超时门控。SIFT10k 功能试跑及正式验收边界见 [V04-2b 收口](profiler-v0.4.md)。

## V04-3a CPU Profile 回归

CTest `cpu_profile_fail_closed` 覆盖三类模型手算、区间与舍入、身份与哈希、非法文档和缺模型，以及 HostCpu 失败门控、异步服务与槽排空。适用条件和本轮证据见 [V04-3a 收口](cpu-profile-v0.4.md)。

## V04-3c 静态回放回归

CTest `sst_static_replay` 经 SST 验证 Profile 成本切换、零时长服务、真实值与 Work 失配排空、旧包拒绝变更内容及原生重采。ASan 定向验证和完整 SIFT1M tuning32 的语义证据见 [V04-3c 收口](replay-v0.4.md)。


## V04-3d worker 调度回归

CTest `sst_static_replay` 增加同一原生采集包的 1/2/4 worker 回放、手算服务时间线、同刻及零时长、分片重排、配额和 Profile 拒绝、并发失败排空。运行口径及证据见 [V04-3d 收口](replay-v0.4.md#worker-与-cpu-顺序)。

## V04-4d 多 worker 诊断

`sst_static_replay` 核对 v4 绑定表在 2/2/32 和 4/4/32 中的逐调用计费，并拒绝诊断配额失配。`v04_validator` 核对乱序完成、同刻释放、错误接纳、同一 worker 重叠、错误等待时间和诊断性能无百分比门槛。全规模验证和报告入口见 [V04-4d](workers-v0.4.md)。

## V04-5b 兼容与完整继承回归

[本轮入口与证据](regression-v0.4.md)为 tools/verify_v04_regression.py。根 CTest 新增 v04_regression_gates，拒绝缺项、失败、quick、重复 R 点和需要深度回归却未执行的情况。本轮四个完整规模 R 点及 detail8、默认 small、新跑完整 V0.1 AC/原生 oracle/慢功能/继承 ASan、T3/T4/T5、IR 与静态回放 ASan 全部通过，根 CTest 92/92。

该入口遵守本轮用户约束，不执行深度回归。regression-impact.json 逐项记录触发条件，未触发记本轮不要求且未执行，触发时阻塞通过。V0.3 的 52 条与配套效率/Q 梯度入口及断言继续保留原义。V04-5c 总交付由 `tools/verify_v04.py` 汇总，见 [交付记录](releases/v0.4.md)。

## V04-5c 总交付门控

CTest `v04_delivery_gates` 覆盖12配置唯一覆盖、有效参数、完整128查询、模拟与原生重复、全量包、12项AC与证据、失败及深度触发阻塞。总入口没有 `--quick` 或跳过门槛的开关；未知参数由 argparse 拒绝。现有 Validator/采集/Profile 测试继续检查篡改、截断、缺项、原始计时及身份。结构夹具通过仅证明门控，正式交付须以新目录的完整入口结果为准。
