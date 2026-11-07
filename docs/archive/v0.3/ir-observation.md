# V03-5 观测与独立比较器

2026-11-03（Asia/Shanghai）。本任务只收口 V03-5，不启动 V03-6，不切换默认 engine。范围与判据为 [PRD §7](../../prd-v0.3.md#7-等价性的精确定义)、[IR §10](../../ir-v0.3.md#10-诊断文件与比较)及[实施清单](implementation-plan.md)。

## 输出与接口

- 必需 `observation.json`（`ndpsim-observation/1`）记录有效 engine、实际 IR/schema/operator catalog/mapping 身份、statistics/detail、逐查询决策摘要及初始比较状态 `not_checked`。direct 的 IR 专属字段为 null，不伪造 IR 执行。源/依赖/配置/数据/图/真值/查询身份继续在 manifest 中；新产物登记 hash。
- IR 的 `statistics.json.resources.ir_summary` / `ir_summary.csv` 记录七算子（含未使用算子的零值）的 submitted/completed/failed、活动/峰值 op/value；这是管理统计，不是额外目标服务。终态活动数必须为零。原结果、Recall、时间、资源、初态、Unsupported、测量口径保留。
- detail 在原 `events.jsonl` 内输出版本化 IR 记录，不另建一份 DAG 或张量文件。闭合 envelope 定义在 [诊断 schema](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/schemas/ndpsim-ir-diagnostics-v1.schema.json)，operation 直接复用 `urn:ndpsim:ir:1` 的 Operation schema。JSON Schema 检查使用 jsonschema 4.26.0 / referencing（本机锁定 Python 环境已有），不安装或升级软件。
- `ir_operation` 在 submitted / visible / failed / reclaimed 写出操作结构、端口、依赖、scope、实际 Runtime 父 task 和模拟时间。Visible 时每端口只有序列化长度与 FNV-1a64 摘要，不复制完整状态/张量；FNV 仅为诊断摘要，不是密码学身份。Runtime 阶段由 `ir_runtime` 的原状态枚举 0～7 说明。
- `ir_binding(op, task)` 连接真实读取、CPU 或 Controller 评分批；`ir_task_edge(parent_task, task)` 连接 Controller 评分批与实际 chunk；`ir_session(query, open|close, task)` 连接一次 Open/Close。经 read_submit/read_complete 的 request、local_request 的 transaction、message 的 task/id 以及 link 的 platform_id 继续追踪。value 由 `(producer_op, port)` 标识，state_ref 保留 query/state_id/version。
- scope.query 为现有运行期 query ID，query_ordinal 为输入行序号；原始 benchmark ID 由 results/observation 的 original_query_id 显式关联，固定输入身份和顺序参与比较，不把运行序号冒充原始 SIFT ID。

所有 sink 同步写流，不增加事件、服务或模拟费用。statistics 不设置 detail sink，也不保留隐藏历史；每算子计数只有七项。日志与必需记录共享既有 `OutputBudget`，耗尽失败，不能静默截断。新增真实 IR 预算耗尽回归检查失败退出及缺少成功 manifest。Controller SCORE 可见记录补齐原 `score_visible`，供既有独立数值/决策/流量核对器使用。

## 独立比较与规范化 v1

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/compare_engines.py \
  results/example/direct-host results/example/ir-host \
  --output results/example/equivalence.json
```

`tools/compare_engines.py` 不导入执行器、映射、GraphSearch 或旧测试的比较函数。同 placement 的 direct/ir，以及同 engine 的 statistics/detail 均可核对。退出0仅代表报告内实际比较范围通过；一侧没有 detail 时 `detail_compared=false`，不能作为完整逐批验收。比较报告另存，不改写已登记 hash 的运行产物；单次实验的 `not_checked` 不自动变为通过。

`ndpsim-equivalence/1` 的语义锚点为固定输入对应的 query（经 original_query_id 关联）、资源/算子角色及该角色在查询内的有序出现序号；元素/块仍按原顺序比较。IR task 进一步通过 bindings/edges 关联 iteration、semantic_batch、operator 和块；差异报告输出 JSON 字段路径、资源流/序号、query、原始 query ID 和可用 IR scope。不要求跨 engine 的 op/task 数值相同。

规范化是保留引用关系的有类型重命名，不删除 task 字段：

| 字段/域 | 规则 |
| --- | --- |
| task | 按实际 CPU service 角色、协议 message、local_storage/controller_service 及读取的 query/有序序号建立语义锚点；所有引用使用同一映射 |
| read request | ordinary 与 local 分域，按各自实际请求顺序分配规范编号；LBA、sectors、route、节点 ID 和完成时间保留 |
| message/link/credit | 各域的首次实际出现规范编号，后续引用复用；platform_id 使用 message 域；0 保持0 |
| local transaction | 按请求内原顺序规范编号；bytes、time 和全部数组顺序保留 |
| 距离 | JSON `100` 与 `100.0` 按同一 binary64 数值相等；没有数值百分比/epsilon 容差 |

所有 top-k/顺序/距离、终止/扩展/摘要、offered/接纳/结果/排空/每查询时间、Work、服务次数、等待、LBA/route/各层字节、消息、缓冲/credit、初态、SSD interval 和全部其他模拟资源字段逐字段比较。每个资源/事件流保持原顺序，不按时间排序来消除同刻 FIFO 差异；共享 PCIe link 与 NAND interval 流也参与比较。新出现的普通字段/资源/事件不会因前缀被丢弃。

唯一排除清单（代码 `WHITELIST` 与报告同步版本化）：

1. config 中已验证的 execution_engine 与 event_log；engine/IR 专属身份、诊断模式、run 标识不作为模拟差异。输入、顺序、模型配置、实际 element/source/dependency 身份必须匹配，不能据此跳过错误输入或不同构建。
2. statistics.wall_seconds；独立测量产物中的宿主墙钟/RSS、文件大小及路径不当作模拟量。产物 hash 仍逐一验证。
3. `lifecycle.runtime_peak_tasks`、`lifecycle_sample.runtime_tasks/waiters`：含 IR 管理任务/continuation 的总数。最终资源清零仍检查；IR 累计/活动/峰值和每算子完成守恒单独验证。同 engine 的 ir_summary 仍必须一致。
4. 仅跨 engine 排除 IR 专属 ir_summary；detail 仅枚举 `ir_operation/ir_binding/ir_task_edge/ir_session/ir_runtime/ir_summary` 六类管理记录。它们先检查 envelope、生命周期、提交计数、父操作存在、Runtime 可见、资源 task 覆盖及 query/operator 归属，不能因被排除而缺失或伪造关联。

不排除“所有时间”“所有 task”“所有资源”或任意 `ir_*` 前缀。delta 管理差异未作为目标费用；原可比较物理时间、资源流顺序和发布因果保留。事件和 IR 历史在比较器的临时 SQLite 中分组；V03-6已将statistics资源数组也改为流式SQLite，仅保留必要ID关联；全规模实测预算见[V03-6](ir-matrix.md)，不宣称任意输入下RSS常量。

既有 `compare_placements.py` 继续从真实向量/查询/图独立重算搜索与 FP64 数值，并核对协议、chunk、SQ/CQ/DMA/link、普通/本地流量和向量零 D2H；不是两个共享实现互比的替代。该独立核对器的既有跨工具链容差不替代本机 direct/ir 的严格比较。

`verify_t6.verify_cached_run` 在其他缓存判断前检查 config/manifest 的有效 engine；缺省历史配置按 direct，跨 engine 一律拒绝；IR 还核验实际 contract hash。比较器接受不同 engine 是对照验证，不是成功运行缓存复用。

## 本轮实际证据

复现入口：

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/verify_ir_observation.py \
  --build build/delivery --output results/v0.3/observation-v035/new-evidence
```

出口证据为 `results/v0.3/observation-v035/final2/manifest.json`；同目录保存输入/实际源码/未提交 diff/完整依赖身份、命令、退出码、log、断言及产物 hash。每轮输出必须为新目录，失败关闭，不以历史成功替代本轮运行。

本轮成功 manifest SHA256：`6747057a2fdc39980fcf374be99eb3db3bc2480b0855e5756ddc297d10781e68`，登记 1,235 个产物。以下十条命令阶段全部退出0：根 configure、build、完整 ctest、synthetic、sift10k、frozen-host、frozen-controller、generated、baseline-tests、diff-check。

- 根 `cmake -S . -B build/delivery -DBUILD_TESTING=ON`、`cmake --build build/delivery -j2`、`ctest --test-dir build/delivery --output-on-failure`：**79/79**。原78项保留，新增 `ir_observation_comparator`；生产 frontend 回归改用正式比较器，未弱化断言。
- 显式合成8次新运行：N16/d260/R4/Q4、B2/E7/chunk2、并发2、vector_sector_bound1536；direct/ir × host/controller × statistics/detail。8项同placement/观测模式对照、两engine独立搜索/数值/全流量账本全部通过，跨页/多chunk/同刻竞争继续覆盖。两放置各92 IR ops，峰值10 ops /14 value slots，终态0。
- `synthetic/mutations/mutation-report.json`：**19项**，包括墙钟允许、一致 task 重命名允许、时间/字节/工作量/结果/引用/FIFO差异拒绝、未知普通及诊断字段拒绝、缺 operation/runtime/binding/chunk edge/session 拒绝、错误父操作归属拒绝、未登记日志拒绝和跨engine缓存拒绝。独立 `budget-assertion.json`：2048B真实IR日志预算耗尽，子命令退出1且无成功manifest，诊断包含 budget exhausted。
- 真实SIFT10k（10000×128、Q32、原准备图/精确真值、L32/E64、并发1）8次新运行；同样8项对照和两engine独立搜索/账本通过，全部IR诊断记录通过Draft2020-12 JSON Schema检查。两放置各6,144 IR ops，峰值5 ops/7 value slots，终态0。不是完整SIFT1M矩阵。
- 两放置冻结direct smoke各新跑一次，完整results和模拟statistics严格相同，events.jsonl与service.csv SHA256仍与V03-0一致。新增观测仅写独立observation.json，原direct事件流没有被重写。
- V03-0/1/2/3/4登记成功manifest及全部产物hash重新复核；schema生成检查、3项冻结证据测试通过。源码与两个子模块在验证期间未变；最终文档收口在运行完成后更新，不改变已测执行代码。

开发记录保留在 `results/v0.3/observation-v035/development/`：首轮frontend比较将JSON整数形式的距离与小数形式误判，修正为相同binary64严格数值比较；首轮跨页夹具沿用512B单向量sector上界，被后端正确拒绝，夹具修为1536B；旧开发CTest因前一数字格式问题为77/78，最终79/79完整重跑。初次字节码预检查因系统Python尝试在沙箱外写缓存失败，不作为代码检查通过证据。初版final验证通过后又增强父操作归属、缺失日志登记和预算测试，当前收口仅使用增强后的final2。

## 限制

- 本轮只接受 V03-5。未执行完整 AC、独立原生 oracle、ASan、完整 SIFT1M 52条矩阵、Q/RSS 梯度或效率门槛；这些不标通过。默认仍 direct。
- MQSim/SST 子模块与硬件成本模型未改。原共享 NAND command hook 不含 request/transaction 参与集合，普通 NVMe 内部 transaction ID 仍 Unsupported；已有本地请求 transaction 快照可沿 IR 父链追踪。不能凭 channel/die 时间区间伪造普通请求归因。
- 诊断不是可回放 IR 包；不保存 base 或全部历史状态。运行失败产物仅供诊断，比较器只接受完整成功并已排空的运行。
- 硬件参数仍 `configured_unvalidated`，不声称绝对性能校准或盘控必然更快。小样本 p99 不代表稳定尾延迟。
