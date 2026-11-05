# V0.4 规划依据与定向源码复核

日期：2026-11-03（Asia/Shanghai）。本页保留规划时的静态核查，规划时未执行构建、CTest、原生基准、完整 AC 或精度评价。后续V04-0开工冻结已完成，实际执行及全量输入/参考复核见[冻结记录](start-freeze-v0.4.md)；本页其余内容保留规划时状态；后续任务与总交付见[实施清单](implementation-plan-v0.4.md)和[V0.4交付](releases/v0.4.md)。

本页此前规划以动态IR为新增执行路径；同日用户要求改为原生搜索插桩采集静态IR，当前需求以修订[PRD §6.4](prd-v0.4.md#64-静态-ir-输入依赖执行与资源调度)及实施清单为准。以下原始核查与hash保留为历史证据，不作为静态路径已实现的证明。

## 来源、版本与复核边界

- 正式需求为 [V0.4 PRD](prd-v0.4.md)，首次规划快照的文件 SHA256 为 `475572b907fed049dda83776987fbc0f29cbf609a8faf2dca97d5f2260f254a8`。
- 既有源码核查、M1～M4 建议及后续内存计费讨论来自聊天“评估图搜索 Profiler 可行性”，聊天 ID `01a1a116-553b-7bd1-9f92-234d1e0d19ee`。核查命令实际记录主仓 `484ca836952c2fe2ffc61d7cc8a5e94b1607a84a`；本轮 `git rev-parse HEAD` 相同，`git diff <核查提交> HEAD --name-only` 为空。规划采用正式 PRD §9 的 **M1～M5**（将 Validator 与下沉交付拆开），不沿用早期 M4 的混合阶段名。
- 本轮开始仅有 `docs/README.md` 已修改、`docs/prd-v0.4.md` 未跟踪；执行、构建、配置、schema 和测试文件相对 HEAD 无变化。保留这些已有文档，不覆盖 PRD，不把 V0.3 历史规划中“尚无 IR”等旧快照当现状。
- MQSim fork 为 `8f7ddf6d8cafdb7c783b3fcfd4693f15c6c5854b`，原生 oracle 为 `51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16`；SST Core 为 `ffd71527bc26a1f2a4fb685fdeb2ca0799997d78`。两个子模块均干净，与此前核查相同。MQSim 相对原生的实际 diff SHA256 为 `f38fc06069ee252ebbf83cdc2e566768d74a255fbb2ff1980ba76b0f2a7ec044`，没有 patch 栈或依赖升级。

因此不重复全量架构审计、微基准或历史完整验收。仅补充影响任务拆分、或此前缺少完整证据的关键结论：

| 结论 | 定向依据 | 本轮结论及实施影响 |
| --- | --- | --- |
| 纯算子已有一部分，搜索驱动仍依赖模拟服务 | [算子](../src/operators/operators.cc)、[搜索接口](../src/search/graph.h)、[根构建](../CMakeLists.txt) | L2/candidates 已可复用；GraphSearch 引入 HostCpu/Runtime。选择与去重在 [direct](../src/search/graph.cc) 和 [IR mapping](../src/ir/mapping.cc) 中各有实现，尚非完整纯函数库。M1 提取 IR/原生共用核心，保留 direct 冻结实现及独立核对器。 |
| 没有仅原生根构建路径 | `CMakeLists.txt` 的 SST 准备/查找在目标定义前执行；`ndpsim_search → ndpsim_host → runtime/backend` | 仅增加 executable 或指定 target 仍会准备 SST。M1 必须在 SST 准备之前分流配置，并验证配置、编译、链接和运行均无 SST/MQSim 依赖。CMake 的 `VERSION 0.1` 也不代表产品能力只有 V0.1。 |
| `content_backend=memory` 不是无 SSD 驻留 | [GraphHost](../src/sst/graph_host.cc) 在两种内容后端后均创建 OrdinaryDevice；`read()` 提交真实请求；[运行器](../tools/run_graph.py) 仍写 `data_residency=SSDStorage` | M3 须显式增加目标系统的内存数据提供路径，不能更名原内容后端或把 MQSim 延迟设零。沿用事件桥不等于查询必须产生 MQSim 请求。 |
| 当前 CPU 接口不足以选择阶段模型 | [HostCpu 头文件](../src/model/host_cpu.h)、[公式与队列](../src/model/host_cpu.cc) | 只有 Distance/Control、setup/rate 和原 Work；需新增阶段/特征及 Profile 选择，复用原 FIFO/slots、服务完成和发布门控。旧默认公式与事件顺序继续保留。 |
| 当前多 chunk 提交不等于原生每 worker 串行 | `Mapping::dispatch` 的 host 分支在循环中提交各 chunk；[原生目标约束](prd-v0.4.md) FR-04-S05 | 单 slots 只能限制同时服务数，不能表达 worker 占有整查询和跨 chunk 顺序。M3 显式配置下增加必要依赖/worker 配额，旧 SSD 默认映射不串行化。 |
| CPU 边界尚未冻结，不能直接套已有粗测 | [squared_l2](../src/operators/operators.cc) 包含 decode、分配、ID 检查及输出编码；[粗测工具](../tools/bench_host_cpu.cc) 重复使用预备字节缓冲 | 需在完整 base 的真实搜索中测取数到得分的整个阶段，再做互斥细分与闭合检查；阶段模型不能重复收费。内存访问条件与 SSD 返回缓冲条件须分开声明适用性。 |
| 已有比较器可继承，精度 Validator 尚缺 | [等价比较器](../tools/compare_engines.py)、[统计导出](../tools/experiment_output.py)、[总交付门槛](../tools/verify_v03.py) | 继承 strict 结果/Work/账本；新增 native/sim 的稳定语义关联、CPU 数据和 Level A/B/C 判断。新 Profile 的时间差异不能交给旧“全部时间严格相等”规则判错或泛化豁免。 |

## 历史证据的可用范围

此前聊天记录有三个复用既有构建的定向 CTest 通过（`content_layout_handles`、`operators_host_cost`、`ir_graph_frontend_causality`），以及粗测 L2 的 measured/model 为约 5.2～10.8。记录未提供一次完整重建、可复核的测量 manifest/二进制身份或 V0.4 原生程序；这些仅支持“需要分算子建模”的诊断，不证明本轮构建可用、任何精度出口通过或具体误差可复现。本轮不重跑这个缓存热微基准。

本机存在 direct 冻结、V03-7 总交付及完整 SIFT 准备 manifest；本轮仅核对这些文件自身 hash（总交付 manifest 与交付文档所记 hash 相同），未递归验证其全部产物、输入 payload 或可执行文件。V04-0 必须重新核验实际输入及冻结参考，不能直接复用历史 passed。

此前硬件查询有 `sysctl ... Operation not permitted`，CPU 型号/实际可用配额证据不完整。历史“16GiB”不是本轮资源预检；这不阻止接口与小夹具开发，阻止的是未预检的大规模正式采集/验收。频率或核绑定不可控可记 Unsupported，不新增硬件采购或精细调度模型。

## 本轮静态证据

本机忽略目录 `results/v0.4/planning-20261103/` 保存 `repository-snapshot.json`、两个子模块逐文件 SHA256 清单、MQSim 相对原生 diff 和开工文档 diff。快照 SHA256 为 `61579675cf59015b2bbd914a91b7bcc6d286da38a37431de47abfabadb34ffa0`；执行文件清单摘要为 `b79dcaa83993d59928046b016ae370a845fe9ed7eaa6a85a7b599e0712c5c969`。

清单算法：根仓 `git ls-files -z` 中 `src/tools/tests/configs/schemas/examples` 下的普通文件，加根 CMake、锁文件与 `.gitmodules`，逐文件计算 SHA256；路径到 hash 的 JSON 按键排序、无空白编码后再计算摘要。子模块对全部跟踪普通文件采用同算法。它是静态内容身份，不是编译身份；未跟踪文件另以开工 `git status --short` 记录。大产物不加入 Git，新检出可按本页 Git 命令及该清单算法重建静态快照。

本轮文档检查及源码前后核对结果写入同目录 `documentation-checks.json`；仅覆盖文档链接、需求/验收映射、任务字段和改动范围，不代替功能测试。

## 静态 IR 路线变更的定向核对

本次仅修改需求及协作文档，未修改代码、schema、测试或依赖，未运行构建/CTest/完整AC。定向源码依据为：`schemas/ndpsim-ir-v1.schema.json` 的 `execution_mode` 固定为 `dynamic`；`src/ir/schema.cc` 拒绝提交的 `workload.counts`，实际计数归受信 evaluate；`src/search/ir_graph.h` 在可见结果后生成后继，`src/ir/mapping.cc` 已承接真实L2/控制函数及普通/盘控读取。

因此可复用执行后端与映射，但不能直接将旧events.jsonl输入现有engine。需新增完整包契约、原生采集器、校验/依赖执行入口、状态重建与期望值/Work核验；期望计数单独保存，不放宽旧提交计数校验。静态包只适用于已冻结逻辑语义，数据/算法变化重采；旧动态前端及独立核对继续保留。全量包/索引/校验临时文件需要重新pilot预算。此处为基于源码的可行性判断，不是PoC或功能验收结论。

本次文档修订及静态检查证据保存于 `results/v0.4/static-ir-prd-20261103/run1/`，包含需求来源、修订前后文档hash、实际源码/依赖身份、变更diff、检查命令/退出码、链接/需求编号/18任务/14配置/精度门槛断言。构建及功能/精度验收标为未执行，不更新任务完成状态。

## 后续矩阵调整

同日用户确认将后续常规验收减少为14个模拟配置；现已同步 [PRD](prd-v0.4.md)、[任务清单](implementation-plan-v0.4.md)及 [矩阵规范](validation-matrix-v0.4.md)。PRD因此与上列首次快照hash不同，原快照仅作历史来源身份。14配置替代的是V0.4的常规全规模交叉运行要求，52条保留按需深度回归；功能/负例断言和精度门槛保留，定向覆盖不宣称等同旧全交叉覆盖。源码与旧工具未变，未执行任何功能验收；本轮静态证据另存 `results/v0.4/matrix14-planning-20261103/`，不覆盖首次规划证据。
