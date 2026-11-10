# V0.4.5 可视化数据契约

适用于独立可视化工具首版。输入均只读。使用版本化产物的内容身份，忽略嵌入的原机器绝对路径。采集结构／语义校验及仿真硬件验证仍由原工具负责。源规范见 [IR/2](../docs/contract-v0.4.5.md)、[图搜索采集](../docs/capture-graph-v0.4.5.md)、[CLI 与报告](../docs/cli-v0.4.5.md) 和 [P19](../docs/ssd-accounting-v0.4.5.md)。

## 输入、身份与对齐

| 产物 | 字段和用途 |
| --- | --- |
| `manifest.json` | `format=ndpsim-static-ir/2`，`files[]` 的路径、字节数和 SHA-256，`provenance.query_ids[]` 的原始 ID，`terminals[]` 的完整阶段键。 |
| `operations.jsonl` | 操作键为 `(query,id)`，阶段键为 `(query,id,stages[].id)`。操作保留 `operator`、`operator_version` 和 `semantic_work`，阶段保留 `kind`、`segment`、`chunk`、`work`、`features` 和 `topological_rank`。计量值保留原单位。 |
| `index.jsonl` | `key={query,op,stage}`，`file`、`offset_bytes`、`length_bytes`、`stage_sha256`、`topological_rank`、`dependencies`。同一操作的多个阶段可共享操作行定位。 |
| `tail.json` | `format=ndpsim-static-ir-tail/2`，`complete=true`，`lost_records=0`，`counts`、`terminals` 与 manifest 一致，`package_sha256` 与重算身份一致。 |
| `report.json` | `format=ndpsim-run-report/1`，`package_sha256`、`execution_succeeded`、`placement`、`profile_sha256`、`context`、`hardware_validation` 及 `details` 中的 CSV、trace 和有效后端快照。 |
| `stages.csv` | `query,op,stage,submitted_ns,completed_ns,dependency_and_resource_elapsed_ns`。只包含成功阶段，时长为两个端点之差。 |
| `services.csv` | `resource,key,submitted_ns,start_ns,end_ns,queue_wait_ns,service_ns`。`key` 可含 JSON 编码的阶段键，单个阶段可有多条服务。 |
| `trace.jsonl` | 首条 `configuration`、末条 `summary`。正式 SSD 快照的 `snapshot.config` 和 `snapshot.config_sha256` 给出运行配置与身份。请求的 `query,admitted,visible,drained,success` 提供各查询的时间基准和状态。 |

重算包身份使用 SHA-256，输入为 ASCII `ndpsim-static-ir/2`、一个 NUL 字节、manifest 原始文件 SHA-256 的 32 个原始字节、index SHA-256 的 32 字节及 operations SHA-256 的 32 字节，按此顺序拼接。逐文件实际字节数和散列与 manifest 一致后才能绑定运行报告；读取 trace 时交叉核对其有效快照中的包身份和报告对应字段。阶段行散列使用该阶段对象的紧凑排序 ASCII JSON 及结尾 LF。文件字节、索引定位或派生行变化均须生成相应新身份，缓存不能跨身份复用。

依赖从阶段的 `dependencies[]` 读取，核对 index 副本。每条边保留 `source.key`、`source.port`、目标阶段、`input`、`kind`，方向是源到目标。`DATA`、`ADDRESS`、`CONTROL`、`ORDER` 各自保留。操作折叠按 `(query,op)` 分组，折叠边保留原始阶段边集合。文件行序与 op 字符串顺序没有执行语义。原始 query ID 可为 uint64，序列化到浏览器或交给 SQLite 时须保留精确值，可使用十进制文本。

两次比较必须绑定同一重算包身份及同一原始 query，保留各自 placement、profile、context 与有效配置身份。时间以各自 `request.admitted` 为零点，保留源 SST 纳秒。查询窗口用 `[admitted, visible]`，排空时间单独保留。归属于目标查询而早于 admission 的记录保留有符号相对时间，导出如采用显示偏移，两次运行使用同一偏移且记录该值。

## 轨道和可归因范围

| trace 事件 | 显式关联 | 观测边界 |
| --- | --- | --- |
| `stage` | 顶层完整阶段键 | 成功时 `submitted → completed`，覆盖输入、资源与必要返回。 |
| `cpu`、驻留 `service` | `key` 解码为 JSON 对象，取 `query,op,stage` | `submitted → start` 排队；`start → end` 服务。 |
| `controller_compute` | `key` JSON 内的完整阶段键，附带 `physical_chunk`、`profile_sha256` | 同 CPU。多个块可对应同一逻辑阶段，保留每个区间。 |
| `local_read`、`local_plan` | 顶层完整阶段键 | `local_read.completed` 是完成点；`local_plan` 无时间戳。没有完整 SSD 服务区间。 |
| `ordinary_read`、`nand_transaction` | 无 query 或阶段键 | 前者仅 `completed`，后者仅 `time`，可按窗口显示。LBA、事务 ID、kind 或相邻行不能提供归因。 |
| `ordinary_submit`、`ordinary_plan` | 无 query 或阶段键 | 无端点时间，保留覆盖信息。 |
| `pcie` | 运行内 `id` 可关联包的各端点；无直接阶段键 | 对同一包明确配对 `enqueue`、`start`、`arrive`，按 `layer`、`destination` 等分组。首版可在窗口独立展示；`platform_id=0` 无上层关联。 |
| `protocol` | 明确 `query,task,chunk`；无直接阶段键 | `submitted → arrived` 为协议观察区间，和 PCIe 包服务分层。首版支持 query 归属，避免将所有协议记录当作 stage 服务。Execute/Result 物理块可经源契约的 `(query,chunk)` 对应物理需求，但 Open/Close 等记录无此阶段映射。 |
| `buffer_access` | 任务访问键编码 `query:op:stage:访问名`，query 写入为 `query-write:query:task` | `submitted` 为提交观察点，`duration_ns` 是服务需求；端口 start/end 未发出，不由相加构造实际区间。若解析任务键，仅接受与完整已知阶段键及发出端后缀（input-write、compute-read、result-write、result-read）唯一匹配的记录。 |
| `credit` | 无 query 或阶段键 | `time` 的资源全局用量，可按窗口展示。 |
| `demand`、`logical` | demand 的 `detail` 或逻辑事件顶层有阶段键 | Work 和估计服务需求可展示于阶段详情。它们不是实际服务端点。 |

窗口事件可与阶段在同一时间范围出现，界面及导出须保留“仅时间窗口”“仅 query”或“完整阶段键”的区别。区间并发时使用独立轨道或可正确表达交叠的事件表示。保留同刻、零时长及原始纳秒；可视化单位转换与相对时间不得覆盖源数值。SSD、PCIe、Controller 沿用 `configured_unvalidated`。

## 失败、缺失和未支持

- 未知必需产物格式、包身份不一致、重复阶段键、悬空依赖、错误引用或端点倒序属于输入错误，应指出产物和键。
- Capture-only 中时序标为未提供。缺少一侧阶段记录时用缺失状态。已有提交但无成功完成记录时用未完成；`success=false` 用失败。失败记录中的 `completed=0` 或 `visible=0` 是哨兵，不生成成功区间。无法确定 admission 的运行不可参加按 admission 对齐的比较。
- 原 trace 无纯依赖 ready 时间、普通／本地 SSD 读无完整服务端点、端口实际排队与服务端点未发出，标为 `Unsupported`。个别缺失或未完成记录与整类未观测分别统计。部分 PCIe 端点可按时间点保留，并标明覆盖不完整。
- P17 教学样本用 capture-only。其演示运行使用 `ndpsim-block-sum-run/1` 专用配置与简化 trace，缺正式阶段键和逐 query admission；不把简化事件推断为通用运行报告。
- 未识别的可选资源事件可登记类型和未覆盖原因；不推断时间、资源所有者或阶段关联。报告中的 `full_acceptance_passed=false` 是验证层信息，和 `execution_succeeded` 分列。

## 样本证据

[evidence/v01-samples.json](evidence/v01-samples.json) 登记本机源路径和实际 SHA-256、query ID、边类型、阶段时间、两侧 admission 与覆盖计数。运行以下独立审计，输出与登记证据对比。该脚本只读取既有输入。

```sh
python3.13 visualization/evidence/audit_v01.py > /tmp/ndpsim-v01-samples.json
diff -u visualization/evidence/v01-samples.json /tmp/ndpsim-v01-samples.json
```

教学例的 `left/sum` 与 `right/sum` 为两条独立分支，各以一条 DATA 边进入 `join/sum`。正式例选择 P19 B01/C01 的第一个原始 ID 2579，审计该查询的完整阶段、边与两份 CSV/trace 的逐阶段和逐服务端点一致性。未重新运行仿真或重新采集。
