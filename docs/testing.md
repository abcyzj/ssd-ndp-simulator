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

`tests/test_v02_delivery.py` 的三类失败关闭回归由 V0.2 总入口执行，覆盖完整适用 AC/N/A、缺项/失败/quick 拒绝、产物篡改/缺失拒绝；也可单独运行，不能因不在 CTest 中而遗漏。

完整 V0.1 AC 仍包含独立锁定原生 oracle 和 ASan；慢功能夹具为 N256/d128/R8/Q8、k10/L16/E16，要求两放置墙钟慢化且全部事件/结果/模拟统计一致。V0.2 AC-02-10=N/A（Deep10M取消），不能混同仍须执行的 V0.1 AC-10。

历史阶段命令、成功与失败证据统一见[V0.2 归档](archive/v0.2/README.md)及[交付记录](releases/v0.2.md)。V0.3 新增 direct/ir 等价、动态依赖、资源回收与52条矩阵等要求见[PRD](prd-v0.3.md)，这些新检查尚未实现或执行。


## V03-0 冻结检查

`tools/freeze_direct_baseline.py` 对锁定准备输入运行新的 direct 小规模、完整base评价128和详细8查询两放置，保存源码/输入/配置/产物身份、命令/退出码、独立核对和预冻结预算；不复用历史运行结果。方法、实测及限制见 [V03-0](direct-baseline-v0.3.md)。`tests/test_direct_baseline.py` 单独执行，覆盖证据缺失/篡改/失败、非法清单及资源冻结拒绝；本轮3/3通过，未注册到原67项CTest中。它们不代替完整AC或IR验收。

## V03-1 结构契约检查

根 CTest 新增 `ir_schema_catalog`（9组Python检查）和 `ir_schema_memory`（C++强类型/variant及往返），当前本轮69/69通过。独立静态入口、schema生成/导出、实际证据和未执行的后续验收见 [V03-1](ir-schema-v0.3.md)。这些检查不证明IR已经执行搜索。

## V03-2 动态依赖与值生命周期

`ir_dynamic_lifecycle` 使用合成结构夹具及受信测试 adapter，覆盖增量接纳、非法引用、ADDRESS/DATA/CONTROL/ORDER、可见值驱动动态后继、版本化状态所有权、空/零时长 JOIN、失败排空、同刻提交顺序和 2,001 JOIN 回收。`sst_ir_base/input/compute/return` 通过真实 SST SelfLink 验证三阶段延迟与非提前可见，输出逐任务因果状态日志。JOIN 是当前唯一有内置执行实现的算子；其余测试 adapter 不替代 V03-3 的真实资源映射。

根 CTest 已包含这些用例。定向 ASan 通过 `-DNDPSIM_SANITIZE=ON` 独立构建，运行 `ir_dynamic_lifecycle`、`runtime_dependencies_visibility` 和 `ir_schema_memory`；不是完整 SST/LSan 审计。实际命令、74/74 CTest、3/3 ASan、冻结输入证据重查及限制见 [V03-2 收口](ir-lifecycle-v0.3.md)。未运行的完整 AC、真实搜索矩阵和 RSS 梯度不计通过。
