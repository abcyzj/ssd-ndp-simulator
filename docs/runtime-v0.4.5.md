# P07 元数据 Runtime 与资源服务

日期：2026-11-06（Asia/Shanghai）。依据 [PRD FR01/07](prd-v0.4.5.md)、[P01 时间/令牌/资源契约](contract-v0.4.5.md#5-时间令牌消息和资源)及[实施计划 P07](implementation-plan-v0.4.5.md)。本次只关闭 P07 基础接口和定向验证；新 AC03/10 尚需 P08/P11/P12/P20 等出口，当前可运行交付仍为 V0.4。

## 实现与接口

- [Runtime](../src/timing/runtime.h)／[实现](../src/timing/runtime.cc)：使用 SDK 的 Key、Counters、Output 元数据类型；Operation 显式提交 Work、features、输出描述、依赖 token、服务资源和整数 duration_ns。没有 evaluate、业务 Bytes、功能值读取或逻辑长度占位数组。`describe` 只在 Visible 后返回元数据；原始 query ID=0 合法。
- [资源接口](../src/timing/resources.h)／[实现](../src/timing/resources.cc)：FifoResource 接收显式 ns，HostCpu 复用同一 slots/FIFO 所有者；排队时间独立于 duration。BufferModel 仅记录容量 credit，不分配 payload，提供 FIFO reserve、排队取消、owner release 和 pin/unpin。owner 释放后在途 pin 仍占容量，最后 pin 释放后经 phase1 回收。
- 复用原有 [EventBridge](../src/backend/event_bridge.h)，无第二套生产时钟；Backend→ResourceCompleteRelease→Publish→ContinueSubmit，同刻逆 phase 进入下一 delta，不增加1ns。依赖只消费 Visible 或排空后的 Failed。零 duration、空依赖及0B credit 都经事件桥，不同步放行后继。
- input/return adapter 只提交后端服务并回调 Done；输入实际到达后才申请计算，必要返回实际到达后才能 Visible。运行中的服务不伪取消，排队服务可以撤销；FailedDraining 等待实际输入/计算/返回回调，phase1 清理 pin，再 phase2 发布根因失败，后继只收到失败且不提交服务。
- CompletionToken 的复制共享同一 owner lease；`retain` 建独立 lease，重复 release、跨 Runtime token 和跨查询依赖拒绝。在途任务、依赖 pin 和回调防止提前回收；最后 owner/pin/回调/服务归零后移除 task 并清空已释放 token 持有的操作元数据。History 支持流式 sink 或仅计数，关闭留存后不积累事件明细。

实际模块采用 `src/timing/` 和 `ndpsim_timing`，与旧 `src/runtime/`／HostCpu／Controller 类型隔离，以便迁移期独立证明无功能链接。实施计划 §2 允许按实际依赖调整落点；这是 P07 接口落点调整，不是保留旧引擎的永久兼容承诺，旧路径由 P20 清理。P01 头文件是设计示意，MappingPlan 由后续映射层降低为本接口的服务和 adapter，不直接引入尚未实现的映射执行器。

`ndpsim_timing` 只链接 MQSim 与标准库，并以 SDK 头文件共享元数据；不链接 ndpsim_content、功能算子、图搜索或旧 Runtime。独立的 SST 测试模块 `ndpsim_timing_validation` 也采用这一依赖边界。PlatformOnly 不创建设备、不初始化 MQSim，不要求数据集或 payload。链接 MQSim 代码不表示产生 SSD 服务。

## 检查与证据

最终证据：`results/v0.4.5/p07/run2/`，包含依赖核验、命令/cwd/退出码、stdout/stderr、构建/源码/依赖身份、符号检查和 hash。P01 design-final1 与 closeout1 的登记产物及 manifest 重新核对，未把历史根 CTest 的通过当作本次结果。

- 根配置 `cmake -S . -B build/delivery -DBUILD_TESTING=ON`、根构建 `cmake --build build/delivery -j2`；完整 `ctest --test-dir build/delivery --output-on-failure` **102/102**。
- [元数据探针](../tests/timing_runtime_probe.cc)：5组输入/计算/返回手算（含全零与分别增加100ns）；slots=1/2/4 的8项同刻 FIFO；多输入汇合及后项失败优先传播；依赖等待/排队/运行中/输入在途/返回在途5类故障；失败不发后继；超大逻辑输出描述；retain/重复释放/提前释放/回收；容量 FIFO、取消、owner/pin/0B、在途失败保持 credit；空字段/单位/重复端口、跨Runtime/跨查询、服务时间溢出、重复回调和停滞诊断；四 phase/逆 phase delta。断言直接使用手算值，不调用生产成本公式自证。
- [SST 接线夹具](../src/sst/timing_validation.cc)和[配置](../configs/regression/timing_runtime.py)：5项实际 SST 测试（基线、输入延迟、计算延迟、返回延迟、全零），完成时间分别手算，后继必须等待返回；终态 task/pin/service 全部清零。只是集成测试入口，不提前实现 P08 DAG 或 P11 用户运行入口。
- `NDPSIM_SANITIZE=ON` 定向构建/CTest **1/1**，执行相同元数据探针的全部断言；P01设计测试 **11/11**、P00清单门控 **3/3**。
- `nm -C` 检查独立探针无 content/operators/search/旧runtime/L2 符号；保存实际链接命令。迁移审计覆盖 **342文件/102根CTest**，新增源码/配置/测试都有职责和删除门槛；`git diff --check` 通过。MQSim/SST 不修改。

开发记录保留 `dev1/` 和 `run1/`：初次开发构建/6项定向测试通过；首轮根102项、ASan通过，但补充P01检查使用了系统Python，因缺jsonschema未能启动，run1不记最终通过。最终改用锁定Python3.13，在run2重跑。审阅时补充多输入失败优先扫描与测试，run2重新构建并验证最终源码，不继承早期二进制结果。

## 限制与后续边界

本层消费受信且已经校验的元数据服务需求；只做基础类型/名称/生命周期检查，不负责新包解析、算子/单位注册、输出大小规则、模型适用性、四种边的端口语义或循环证明。这些分别由 P02/P05、P08 和映射接线承担。Work 不重算，也不从 duration 反推；Profile→duration 的正式接线归 P11/P12。没有实现包级 DAG 执行器、Layout、size-only PCIe、Controller映射或统一CLI。

adapter 必须在已发服务/credit真实排空后恰好回调一次；同步回调会被异步化，重复回调拒绝，抛异常只表示尚未接纳服务。无法安全排空、回调后抛错、资源回调抛错或排队后时间溢出均终止实验，不能制造成功或清空在途资源。Runtime不能替外部adapter证明硬件排空；P12须在实际I/O/传输中履行契约。Runtime、资源、adapter所有者必须活到事件桥排空，不提供析构取消/故障恢复。

等待依赖目前扫描活跃记录，是 P07 小规模服务底座；包索引/窗口/接纳/worker与大Q预算由P08处理，尚无任意规模性能承诺。测试默认保留明细，生产接线应选择流式sink或仅计数。单rank/thread；ASan仅定向原生夹具，不是SST或上游完整泄漏审计。未执行正式SIFT采集/精度、SSD对拍或新AC总验收，硬件仍为configured_unvalidated。

P07范围到此，不启动P08及任何其他任务，不提交或推送Git。
