# P08 静态 DAG 执行器

日期：2026-11-06（Asia/Shanghai）。需求依据为 [PRD FR01/03](prd-v0.4.5.md)、[P01 包/拓扑契约](contract-v0.4.5.md#2-包拓扑和信任边界)和[实施计划 P08](implementation-plan-v0.4.5.md)。**P08 已完成，P09～P21 未开始。** 直接依赖 P02、P07 的登记证据重新核验；本任务不关闭 AC02/03/10，当前正式可运行交付仍为 V0.4。

## 接口和职责

- [Package](../src/dag/package.h)／[实现](../src/dag/package.cc)消费原始 IR/2、外部 hash 绑定的 P02 结构报告、包 ID 和 registry 指纹。native-capture 另须有 hash 绑定的独立语义报告，核对包 ID、provenance 和原始 query IDs；fixture 必须显式启用。启动时检查原始文件/身份/预算/tail，建立本次运行独占的新 SQLite scratch 定位索引，失败文件保留。
- SQLite 仅保存 query/stage/rank 到原始 index 行的 byte offset/length，2MiB 页缓存；它不是另一份业务 IR，也不保存 payload。全包索引和排序在磁盘，排队请求只保存查询大小/顺序/终态等描述。接纳前加载完整查询节点/边，验证连续 rank、缺源、跨查询、重复边、环和所有 sink/terminal 闭合，不依赖物理文件顺序。
- stage 详情按原始 operations 偏移随机读取，一次只读一条有界 operation；一 operation 多 stage 允许共享行。逐次核对 stable key、stage hash、rank 和依赖，一次读取上限16MiB，不缓存整查询详情。没有滑动文件窗口，所以远处已就绪分支不会等待前方文件行。只读 metadata 不等于提前预占目标资源。
- [Executor](../src/dag/executor.h)／[实现](../src/dag/executor.cc)用入度、后继表和就绪队列放行阶段；DATA、ADDRESS、CONTROL、ORDER 均仅在源 Visible 回调后满足。多个端口引用同一源仍逐边扣减，独立分支可并行，多终态必须全部完成。lowering 只接收该就绪阶段，提供 P07 Operation；Runtime 再等待实际 input、显式服务和必要返回。禁止 lowering 改写 key/Work/features/outputs 或加入隐藏依赖。
- 原始 query offered 时间与 manifest 顺序决定 FIFO 接纳和 worker 分配；admission、workers 与计算 slots 分属不同所有者。入场前按允许同时接纳的最大查询集合核算 stage/index 配额，不足即拒绝，不降低 worker/admission 静默串行。仅计费模型明确给出的服务，不从采集时戳或宿主墙钟取耗时。
- 首个失败停止后继和新请求，撤销尚未接纳的 offered continuation，取消排队计算；已经发出的输入、计算、返回由 P07 实际排空。最后完成回调返回并释放 token 后，在后续 phase3 回收图、边、worker 和 admission。失败的 visible 字段为0且 success=false，不将其解释为成功零耗时。

新模块为 `src/dag/`、`ndpsim_dag`。新增显式系统依赖 SQLite3，由根 CMake `find_package(SQLite3)` 查找，使用系统库，不下载或引入源码副本。SDK 的标准元数据和 SHA256、已有 JSON 头依赖显式链接；不链接真实内容/功能算子/搜索/旧 Runtime/Replay。

[通用 GraphHost](../src/sst/dag_host.h)／[接线](../src/sst/dag_host.cc)仅负责 SST SelfLink、EventBridge、Package、Runtime、Executor 生命周期及流式观测，编译为 `ndpsim_dag_sst`；具体扩展覆盖 `lower`。P08 的实际 SST 实例由[测试模块](../tests/dag_sst_probe.cc)提供显式合成服务。旧 GraphHost/Replay 迁移期共存，P20 才删除，新增执行器没有它们的运行依赖。完整图 Host 映射、模型接线和用户运行入口仍归 P11/P14，没有以测试入口冒充这些任务已完成。

配套改动：P02 [validate_package](../python/ndpsim/static_ir.py)按稳定键从独立扫描表核对每条 index，不再要求物理 index 按 rank 排列；重复/缺失/定位/摘要/依赖和 scratch 预算仍严格检查。EventBridge 返回平台 continuation ID，Executor 只用其取消尚未接纳的 offered 事件，不撤销已发硬件服务。P07 Runtime 只扫描依赖等待集合，桥非空时不构建停滞诊断，避免无依赖就绪阶段反复全表扫描。相关旧测试均重新运行。

## 实际检查与证据

最终证据：`results/v0.4.5/p08/run3/`。包含可重放命令/cwd/退出码、依赖核验、原始 stdout/stderr、完整合成输入/报告、native/SST/ASan 输出、Q 梯度、迁移审计、源码/构建/依赖身份及 hash。P02 run2 **191** 项和 P07 run2 **81** 项登记产物重新核对；历史证据身份不直接替代本轮修改后的实现验证。

- 根 `cmake -S . -B build/delivery -DBUILD_TESTING=ON`、`cmake --build build/delivery -j2` 和 `ctest --test-dir build/delivery --output-on-failure`：**104/104**。
- [独立探针](../tests/dag_executor_probe.cc)配合[18项测试](../tests/test_v045_dag.py)：手算 fork/join（root=2ns、left=5ns、right=11ns、right返回=7ns、join=3ns，两个 slots，总23ns）；四种边和实际返回门控；全零服务；worker=1/2/4、admission 独立配额、offered FIFO、最大uint64 query ID；多终态/同源多端口；Unicode key、空格路径；操作流/index流随机重排；共享 operation 行的多个 stage；失败停止后继/在途排空/未来 offered 取消；缺 mapping/metadata 失配；配额/SQLite容量/receipt/tail类型和额外字段/文件篡改/环/缺终态/重复索引/重复start负例。
- [SST 检查](../tests/test_v045_dag_sst.py)：通用 GraphHost 下的3个实际变体（串行 worker、并行 worker、全零），三查询手算完成时间为69ns、46ns、0ns；结束时 task/pin/service/active graph 清零。合并为一个根 CTest，不称为3个新增 CTest。
- `NDPSIM_SANITIZE=ON` 定向构建/CTest **2/2**（完整P07元数据探针和相同18项DAG测试）；独立 SDK 配置/构建/CTest **2/2**、P02 **16/16**、P01设计 **11/11**、P00 **3/3**。
- Q=1/64/512、admission=workers=2、slots=4，关闭历史留存：峰值活跃 stage 为 **4/8/8**，峰值活跃 index 编码字节 **1314/2668/2692**，执行后全部 token/pin/边/请求/worker/配额为0；每阶段详情恰读一次。query ID/byte offset 十进制位数影响少量 index 大小，不是已完成图留存。SQLite scratch 分别16384/32768/147456B；正常与ASan的实测 RSS 原值分别保存于 `dag-artifacts/q-gradient.json` 与 `asan-artifacts/q-gradient.json`。
- `nm -C` 检查独立 native 探针及 SST 模块没有 content/operators/search/旧 Runtime/L2 符号；保存实际链接命令。迁移审计 **353文件/104根CTest**，新增项及改动职责已登记。`git diff --check`通过；MQSim/SST源码及相对原生diff不变。

开发记录保留 `dev1/`、`run1/`、`run2/`。早期探针JSON→path隐式转换编译失败、多stage夹具遗漏segment序号、调用受限的系统time命令等均已修正；RSS改由探针getrusage读取，没有调整权限或安装工具。手算故障期望曾误将失败后尚未发出的返回算为在途，已按真实发出边界区分13ns计算排空和20ns返回排空。run1通过后完善就绪队列/Runtime等待集合及Unicode用例，未沿用旧二进制身份。run2中新增tail篡改用例让修正前二进制失败（tail原本未计入包ID）；已改成按manifest推导tail全部字段/类型逐项匹配，run3重新构建并跑完整检查。没有删失败样本或放宽断言。

## 限制

本任务是通用静态执行底座，不是完整用户 simulate。lowering 为受信编译期接口，P08合成服务常数仅在 tests 中；没有实现正式 Host/Controller 映射、Profile→duration、Layout、SSD设备、size-only传输或 CLI。通用 GraphHost 当前使用 PlatformOnly；P12的实际后端接线仍待完成。AC02/03/10仍需后续任务的最终接线/清理和验收，104项根测试不等于新AC通过。

结构和独立语义报告必须由调用方通过受信外部 hash 绑定；hash不是对恶意生产者的认证。C++不重新运行Python完整schema/算子/端口/状态/大小规则校验，P02 receipt承担该信任边界；读取器另做文件身份、tail、完整查询拓扑和逐stage摘要/依赖门控。输入/报告须在校验与运行期间保持不可变。本机无payload证据为合成夹具，不是正式SIFT新包精度或SSD验收。

预算约束为编码行/查询index、stage配额、SQLite页和包字节，不是硬RSS/全部临时文件系统沙箱；单条operation最大16MiB，JSON解析对象有额外开销。Q梯度只证明该夹具活跃图回收，排队查询描述/manifest终态和SQLite磁盘随Q增长；ASan allocator quarantine会增大RSS，不能据其高水位认定活对象泄漏或CPU资源模型精度。正式规模仍须预检资源。默认独立探针可留存明细；通用SST Host默认流式输出，不留全包事件历史。

实验独占一个Runtime，单rank/thread，全部所有者须活到事件桥排空；不支持共享可变对象、跨查询边、任意反事实控制流或析构恢复。队列取消只撤销未执行平台continuation，失效SST唤醒仍按generation忽略，不影响请求visible/drained时间。scratch必须为新路径，失败与成功均留存供核查，由调用方管理清理。目录恶意并发替换防护、全新OS隔离、硬件校准和正式性能承诺不在本次范围。

本次止于P08，不开始后续任务，不提交或推送Git。
