# P14 统一 CLI／报告

日期：2026-11-06（Asia/Shanghai）。依据 [PRD FR06/10/12](prd-v0.4.5.md)、[实施计划 P14](implementation-plan-v0.4.5.md)及 P04/P06/P11/P13 接口。P14 实现统一安装入口与报告，不关闭 AC07/11，不启动 P15；当前正式交付仍为 V0.4。

## 实现

- [安装声明](../python/pyproject.toml)提供 `ndpsim`，`python -m ndpsim` 使用同一入口。[CLI](../python/ndpsim/cli.py)只依赖当前通用模块及显式独立图适配器，无历史 tools、数据准备、旧总验收 import。包携带两个小型 SST 接线脚本；运行不依赖源码仓库的 configs 路径。
- `capture` 便捷调用独立图工程，capture、sample、benchmark、uninstrumented 分别显式执行；`check` 区分无 payload 的结构检查和显式原生数据的语义/Work核对。图特定元数据规则留在独立工作负载；通用校验器不内置图语法。独立图适配器新增 `structure` 模式并核验 registry/规则实现身份。
- `fit --manifest --budget` 复用 P06，包含 fit/table/evaluate/check 四种独立动作，不把 evaluation 回流拟合；manifest 自带 recipe/measurements 引用，取代计划中的两个扁平参数。
- `simulate` 读取显式 experiment/run 配置和已有构建，以 SST 运行 resident Host、SSD Host 或 SSD Controller。所有引用相对声明文件解析；CLI 路径相对启动 cwd 解析。覆盖低层输出/scratch 为新目录内路径，复制完整元数据包、Profile、registry、结构/语义receipt、Layout/XML，保存有效配置、原始配置、源码/二进制/输入 hash、命令、退出码及宿主资源。
- [执行模块](../python/ndpsim/execution.py)复用旧运行工具中有用的职责，独立实现资源预检、wait4 RSS、日志、超时与失败记录，不导入旧脚本链。检查当前内存/磁盘、输出/SQLite预算；轮询失败终止并回收子进程组。没有隐式构建或验收，缺构建直接失败。
- [报告模块](../python/ndpsim/reporting.py)流式读取实际 trace，SQLite 计算 nearest-rank 时延分位数；输出吞吐、阶段总耗时、服务/排队等待、服务来源/位置、逻辑访问引用/输出描述字节、分方向分层流量、可用峰值、排空状态。逻辑访问引用不等于唯一业务数据，协议/PCIe/sector/NAND 不相加。SSD trace 只增加 submitted 观察字段，成本来源 trace 增加 location，不改调度/计费。参考 top-k 与模拟预测分开；目标硬件与执行宿主分开；Unsupported 不填0，设备/链路/盘控仍 configured_unvalidated。

完整命令、配置和预算例子见 [CLI 使用说明](../python/CLI.md)。旧入口留待 P20 删除，没有创建旧命令兼容转发。

`compare` 核验报告身份、不同路径、相同包/目标后做描述性比较，没有精度或收益通过结论。`verify` 保留计划命令形状并返回3及失败状态；三层 Validator 属 P15，完整日程/聚合属 P21，本任务不提前实现。全部正常结果均明确 `full_acceptance_passed=false`。

## 检查与证据

最终证据位于 `results/v0.4.5/p14/run1/`；包括可重放编排、直接依赖 gates、命令/cwd/退出码、原始输出、隔离安装/采集/拟合/模拟产物、源码/构建/依赖身份及 hash。直接依赖 P04 run2、P06 run4、P11 run2、P13 run1 分别重新核验312、148、322、403个登记产物，全部匹配；这证明既有证据完整性，不继承为本轮 AC 或采集精度。

定向测试 [test_v045_cli.py](../tests/test_v045_cli.py)验证：

1. 仅复制 python/ 到空格隔离源目录，离线安装到独立目录，从 `/tmp` 使用安装后的 `bin/ndpsim`；包内 SST 脚本、相对参数/配置引用、中文/空格路径正常。
2. 内存合成 DAG 的16阶段、4请求，1/1/1、每阶段3ns，独立手算48ns、源分类16项与0 SSD请求；核对报告/CSV和输入快照，不覆盖既有输出。
3. 全部预算字段、缺构建、hash篡改失败；真实等待进程超时后被终止/回收，退出码与错误保留；失败不留有效模拟报告或有效拟合Profile。
4. generic/graph结构校验及独立全查询语义核对分列，外部包hash失配拒绝。
5. 从11查询小图实际采集并独立核对，另跑原生 benchmark；删除全部payload后同包执行SSD双放置。Controller向量D2H=0，所有查询及服务报告正常，无重编译。
6. 独立 fit/table/evaluate 从安装包执行，缺 fixture 授权拒绝，预测与表来源及冻结/评价边界继承P06。
7. 两份报告描述性比较，verify非零拒绝且不出总通过。

实际运行根配置 `cmake -S . -B build/delivery -DBUILD_TESTING=ON`、构建 `cmake --build build/delivery -j2`、完整 `ctest --test-dir build/delivery --output-on-failure`：**137/137通过**（96.98秒）。P14定向及最终源码对应复验均为 **7/7通过**；最终复验目录为 `final-directed3/`，观察记录为 `final-directed-observation.json`。

`NDPSIM_SANITIZE=ON`配置后构建graph_ssd_probe、memory_plan_probe、ndpsim_native，定向 `ctest --test-dir build/delivery-asan --output-on-failure -R 'graph_ssd_v045|memory_plan_v045'`：**2/2通过**。P01设计 **11/11**、P00门控 **3/3**；迁移审计 **398文件/137CTest通过**。最终文档链接、源码/依赖/产物hash和 `git diff --check` 通过。源码只在Python报告/状态hash处作最终修订时，仅重跑受影响的7项安装CLI检查，没有重复无关根回归。命令及结果保存在run1的commands/manifest和最终观察记录中。

## 限制

仅做小夹具和当前根回归，没有运行正式 SIFT1M 采集、训练、benchmark、精度、P16 MQSim对拍、P19正式SSD账本或新AC。不得把7项测试、根CTest或描述比较计为正式精度/总验收。语义receipt是外部可信生产者证明，hash不是恶意来源认证。

资源限额为可观察检查：磁盘/超时50ms轮询，RSS在子进程退出及CLI报告完成时检查；不是硬RSS或全进程树沙箱，OS缓存及输出目录外SQLite临时空间不计入。瞬时写入可能超额，失败诊断也可能超过极小预算。输入须保持不可变；不提供恶意并发路径攻击防护。纯依赖ready等待缺单独trace字段，明确Unsupported；阶段总耗时包括输入、服务和返回，不能当纯CPU需求。正式规模需重新预检预算。

最早一轮在沙箱内因 sysctl 权限失败，沙箱外复验通过；开发记录保留 dev1 及 run1 中初次和最终定向结果。P14没有修改MQSim/SST源码，没有提交、推送或开始后续任务。

报告器与独立验证器对 trace 的首条配置记录采用 64 MiB 上限，以容纳完整的有效输入快照。其余事件单条上限为 16 MiB，整次运行的输入、输出及 RSS 预算仍分别检查。
