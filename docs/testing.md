# 持续维护的测试与验收

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

- `tests/` 的 probe、scenario 和 fixtures 均保留；根 CMake/CTest 是常规回归入口，当前非 ASan 配置注册 52 项。测试数量以实际发现为准。
- `tools/verify_acceptance.py`、`verify_concurrency.py` 和 `compare_placements.py` 持续维护；CMake/CTest 已同步调用路径。`verify_contract.py` 是独立契约/ABI 账本核查，不在 CTest 中。
- `configs/regression/` 保留所有持续回归探针/分支夹具；`configs/experiments/` 为当前默认实验，`configs/sst/graph.py` 是实际组件接线；`configs/native/` 保持不变。
- 历史阶段入口归入 [tools/archive/v0.1](../tools/archive/v0.1/README.md)，三个旧 smoke 配置归入 [configs/archive/v0.1](../configs/archive/v0.1/README.md)。保留复现能力，不默认执行全部历史阶段。完整 AC 自行导出原生 oracle；历史 macOS 原生工具仍可按需运行。
- `build/`、`.cache/`、`results/`、`docs/evidence/` 是已忽略的生成产物，保留本机历史数据，不承诺新检出存在。清理前确认无运行占用、无唯一证据；本次未删除。
- 历史结果与阶段方法见[归档](archive/v0.1/README.md)；本次验证见[版本交接](releases/v0.1.md)。未运行完整矩阵时不得继承历史通过结论。

常用检查（仓库根目录）：

```sh
cmake -S . -B build/delivery -DBUILD_TESTING=ON
cmake --build build/delivery -j2
ctest --test-dir build/delivery -N
ctest --test-dir build/delivery --output-on-failure
SDKROOT="$(xcrun --show-sdk-path)" /opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/verify_contract.py --output results/contract-check
```

独立契约脚本直接调用 Clang；在本机显式提供 SDKROOT，避免找不到 C++ 标准头。根 CMake 已自动选择 SDK。输出路径须选择未使用目录。环境要求见[README](../README.md)；ASan 是独立探针，不是完整 SST 泄漏审计。

## T2 图与真值回归

CTest 的 `benchmark_artifact_schema` 运行 `tests/test_benchmark_artifacts.py`，覆盖独立完整排序小 oracle 与分块堆 top-K 的数值/同分交叉核对、K≥k、ID/距离/身份错误、全图边界/可达性及重复边/自环保留、两种质量指标和不足 k。真实数据构建与官方 ID-only 的实际验证使用 `tools/verify_t2.py`，见 [T2 收口](t2-closeout.md)；它不是完整 V0.2 AC 或 T3 模拟贯通。

## V0.2 T3 闭环

CTest `simulation_input_schema` 覆盖相对路径、入口匹配、K≥k、未知实验版本、错绑定和坏图的启动前拒绝。`tools/verify_t3.py` 运行真实 10k SIFT 原输入和受控向量交换输入，各自两放置及独立逐批/账本对照；断言后续扩展和实际读取改变。复跑、范围和限制见 [T3](t3-closed-loop.md)。这不是完整 V0.1 AC 或规模验收。

## V0.2 T4 内容与 profile

CTest 新增 `device_profiles`、`mapped_content`、三个 profile 的实际 namespace/资源预检与 namespace 越界测试；当前常规配置共 64 项（以实际发现为准）。原有 V0.1 用例全部保留。`tools/verify_t4.py` 实际交叉运行 memory/mmap × host/controller，覆盖 d128/d96 的并发4、d260 跨页的接纳降额2，以及 T2 真实 SIFT 10k/32 query。比较完整事件、结果及除墙钟外的全部模拟统计，独立核对每批决策、请求完成因果、流量和 credit。参数和证据见 [T4 内容/profile](t4-content-profiles.md) 与 [收口](t4-closeout.md)。

涉及后端，本轮另跑完整 `verify_acceptance.py` 的锁定原生 QD1/QD32/小 CMT 对照及 ASan 矩阵，映射内容探针另外在 ASan 构建下运行。以上不替代 T5 历史事件增长测试或 T6/T7 完整 base 的初始化/查询矩阵。

## V0.2 T5 观测与生命周期

新增 CTest `observation_lifecycle`（10,000 次任务/lease 回收、非复用 ID、失败在途 completion 保留、20,000 条乱序/重叠/跨窗口/零长度区间外排序独立核对）和 `streaming_output`（1,000→30,000 条资源记录，导出字段/CSV 和 Python 堆增长）。常规配置共 66 项，以实际发现为准；既有回归及断言保留。`tools/verify_t5.py` 运行固定 N256/d128/R8/E8/并发4、Q32/128/512 两放置三次重复、统计/详细等价、终态及退休容器计数、RSS 阈值和明确预算失败；可选真实 T2 SIFT 10k/32 query 完整逐批/账本对照。实际证据与未执行范围见 [T5 收口](t5-closeout.md)。


## V0.2 T6 SIFT1M

CTest `sift1m_matrix`运行六项测试：冻结26点/查询分割与参数拒绝、百万行按需读取、完整统计比较仅排除墙钟、成功产物复用的配置/输入/模型/元素/输出篡改拒绝、3万行流式事件堆界及重复/缺阶段/错误wire/已完成ID复用拒绝。当前常规配置67项，原断言保留。`tools/verify_t6.py`独立执行完整SIFT1M准备、26次矩阵和T6 AC-02-07/09/11；它不启动Deep10M或交付任务。最终记录为67/67 CTest、完整V0.1 AC-01～11、原生oracle和17项ASan；实际命令、初轮预算拒绝与严格复用恢复见 [T6收口](t6-closeout.md)。
