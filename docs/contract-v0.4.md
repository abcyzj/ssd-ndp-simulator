# V04-2a 静态包与计时计费契约

适用于 [V0.4 PRD §5、§6.2–6.4、§7](prd-v0.4.md)。本页与版本化 schema 和校验器一起冻结。V04-0 的输入与基线身份及 V04-1a 的核心语义是前置条件。包验证覆盖结构、绑定与可静态推导的语义。原生真实采集、本次运行值与 Work 核验、Profile 估计接口见 [V04-3a 收口](cpu-profile-v0.4.md)，精度验收由后续任务完成。

## 格式和预检入口

四种 JSON Schema 使用 Draft 2020-12。静态包的顶层 schema 描述 manifest，`$defs` 给出 index、operation、tail 和独立 binding 的记录 schema。封闭对象拒绝未注册字段，校验器另拒绝重复 JSON 键、非有限数、整数位置的浮点写法及跨记录失配。工具使用与现有 IR schema 回归相同的 Python `jsonschema`。

| 格式 | Schema | 合法夹具 |
| --- | --- | --- |
| `ndpsim-static-ir/1` | [静态包](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/schemas/ndpsim-static-ir-v1.schema.json) | [manifest](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/examples/v04-contract/package/manifest.json)、[index](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/examples/v04-contract/package/index.jsonl)、[operations](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/examples/v04-contract/package/operations.jsonl)、[tail](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/examples/v04-contract/package/tail.json)、[独立 binding](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/examples/v04-contract/binding.json) |
| `ndpsim-measurement/1` | [Measurement](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/schemas/ndpsim-measurement-v1.schema.json) | [Measurement 样例](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/examples/v04-contract/measurement.json) |
| `ndpsim-cpu-profile/3` | [CPU Profile](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/schemas/ndpsim-cpu-profile-v1.schema.json) | [v3 夹具](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/examples/v04-contract/cpu-profile.json)、[V04-3b 冻结 Profile](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/configs/profiles/v04-merge-split.json) |
| `ndpsim-effective-config/1` | [有效配置](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/schemas/ndpsim-effective-config-v1.schema.json) | [配置样例](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/examples/v04-contract/effective-config.json) |

```sh
python3 tools/v04_contract.py package examples/v04-contract/package --binding examples/v04-contract/binding.json
python3 tools/v04_contract.py measurement examples/v04-contract/measurement.json
python3 tools/v04_contract.py cpu-profile examples/v04-contract/cpu-profile.json
python3 tools/v04_contract.py effective-config examples/v04-contract/effective-config.json
```

成功返回 JSON 和退出码 0，内容错误返回带根因的 JSON 和退出码 1。调用方必须独立提供预期 binding，来源为实际运行选定的输入和允许的采集构建，不能从待验证包反推。binding 文件路径可相对 binding 所在目录。验证器比较构建、源类型与全部语义身份，逐文件核验长度及 SHA256。预检依赖调用方在验证到使用期间保持输入和包不可变，执行入口还须绑定本次实际打开的内容。完整规模 I/O 和 RSS 预算须通过采集 pilot 另行确认。

## 静态包

manifest 给出采集运行 ID、采集构建和逻辑语义身份。包来源区分 `native-capture` 与 `synthetic-fixture`，正式运行只允许前者。对象目录固定为 base、graph、queries。内容 SHA256 同时充当对象内容版本。向量文件为连续小端 FP32，width 为维度。图文件按节点 ID 索引，width 为最大邻接长度，每记录有一个小端 uint32 degree 和 width 个小端 uint32 槽，逻辑访问只计 degree 及其有效 ID。queries 文件依 manifest 的原始 query ID 顺序连续存放，原始 ID 到文件行号由该顺序建立。

`index.jsonl` 每查询一行，采集器默认按 manifest 查询顺序写入。只要索引与分片同步重排、连续偏移和所有完整性哈希重新核验，就允许改变物理排列。运行的 FIFO 顺序取自独立绑定的查询清单。byte offset 从零开始，length 包含各操作行末 LF。偏移连续且覆盖整个 `operations.jsonl`，每分片属于一个查询。尾记录封包条件包括完整查询覆盖、操作和依赖计数、终止原因、分片哈希、决策摘要和最终结果摘要。生产端先写临时包，在计数、预算、验证与无丢失检查后写 tail 并原子发布。预检以单条记录和当前查询活跃元数据流式检查，元数据单文件及单行上限 16 MiB。全量操作流无需载入内存。

文件和分片 SHA256 按原始字节计算。`decision_sha256` 对该查询按拓扑序的操作逐行摘要，行编码为键排序、ASCII JSON、分隔符 `,` 和 `:` 无空白、LF 结尾。`result_sha256` 等于 FINISH 端口摘要。包 ID 为 SHA256，输入为 ASCII `ndpsim-static-ir/1`、一个 NUL，再拼接 manifest、index、operations 三个 SHA256 的原始 32 字节。尾记录计数与实际值独立核对。执行配置记录此包 ID，Profile 引用使用文件原始 SHA256。

### 查询内拓扑和状态

操作 ID 在查询内从 1 连续递增。iteration 与 batch 都从 0 开始，每完成一次非空扩展的 DEDUP 后递增。固定语义语法为 INIT、入口评分批、SELECT，循环执行扩展及后续评分，空 SELECT 后 FINISH。即使耗尽扩展预算，已取得的邻接仍去重，新 ID 仍评分与合并，然后执行最终空 SELECT。空去重保留 DEDUP 与下一次 SELECT，跳过评分及合并。

每条边指向本查询的前序 producer、命名端口和类型。ADDRESS 提供可见地址，DATA 提供可见值，CONTROL 许可本分支，ORDER 绑定当前状态。全部边为 AND。端口 `out` 的类型见下表，INIT 另有 `query:fp32`，来自绑定的 query 行。ORDER 依赖有状态版本的生产者，`state.before/after` 在 INIT 为 null/0，随后每次状态转换递增一。查询局部 op 和状态版本由执行入口映射到本次作用域，Runtime ID 不复用。

| 操作 | 输入边及固定顺序 | `out` 与状态含义 |
| --- | --- | --- |
| INIT | 无，固定访问原始 query ID | state，初始化查询私有 scored、expanded、rank |
| MARK_SCORED | ORDER 当前 state、CONTROL INIT 或 DEDUP，若来源是 DEDUP 再加 DATA DEDUP | state，评分提交前纳入新 ID |
| SCORE_L2 | DATA INIT.query、CONTROL MARK_SCORED，非入口再加 ADDRESS DEDUP | scores_id_f64，完整评分批的输入顺序 |
| MERGE_CANDIDATES | DATA SCORE，再加 DATA 上次 MERGE（若存在） | scores_id_f64，全体 rank |
| SELECT_FRONTIER | DATA 当前 MERGE、ORDER 当前 state、CONTROL INIT 或最近 DEDUP | ids_u32，按 rank 选前 L 中至多 B 和剩余 E 个未展开 ID |
| MARK_EXPANDED | ORDER 当前 state、CONTROL SELECT、DATA SELECT | state，非空 SELECT 可见后、邻接提交前记展开 |
| READ_ADJACENCY | ADDRESS SELECT、CONTROL MARK_EXPANDED | adjacency，每个选中位置一条，位置由本批 READ 的序号确定 |
| JOIN | 按选择顺序的 DATA READ 集合 | adjacency_batch，原顺序汇合，零目标服务 |
| DEDUP_NEIGHBORS | DATA JOIN、ORDER 当前 state | ids_u32，升序且未评分的新 ID |
| FINISH | CONTROL 空 SELECT、DATA MERGE、ORDER 当前 state | scores_id_f64，rank 前 k，数量可小于 k |

状态摘要为小端 uint64 版本令牌的 SHA256，状态转换的 ID 列表单独核验。得分摘要输入为连续 packed 小端 uint32 ID 加 binary64 距离。ID 向量为小端 uint32，邻接输出为 degree 加有效 ID，JOIN 为相同编码的记录按顺序连接。空 ID 输出按零字节摘要。JOIN 的 count 为行数，单行的 count 为 degree。终止原因在已展开数达到 E 时为 `budget`，其余为空选择的 `exhausted`。

`access` 中的 ID、字节、所有 `expected` 值和 Work 是核验目标。运行时地址来自可见 ADDRESS 端口，只有入口与已绑定 query 可以用固定值。算子和本次真实输入生成实际 Work，采集计数不得用于费用结算。SCORE 的包内 Work 是按语义 chunk 的原七字段求和，query 的逻辑 Work 读字节每语义 chunk 计一次，和有效 base 访问字节分列。预检只核对能从包结构推导的字段，如 L2 的 FLOP、端口形状、选择次序和终态。距离摘要、真实邻接、排序和其他数据相关 Work 仍须在本次执行时重算比对。匹配且资源服务及必要返回都完成后才允许后继，失配即停止并安全排空。

语义图无采集时间、线程号、全局跨查询边、设备分块、LBA 或信用记录。查询文件顺序绑定查询集合；本次执行根据有效配置决定 worker 和资源仲裁。Host 内存策略使用固定语义 chunk 和 worker 串行。SSD 策略保留 Host 普通读和 Controller 容量分块。包已知未来地址不授予读取、预取或信用预占。SST 是唯一时间权威，继续使用原异步 continuation 和可见性阶段。

## CPU 桶、资源和窗口

桶按实际执行的半开线程 CPU 区间归属。采样只用于估计目标 CPU 服务。排序/去重函数内部发生的容器遍历、分配和释放归函数桶，调用侧另做的工作归下表所列桶。拷贝到另一个目标对象时只在实际发生的阶段记录一次。

| 桶 | 原生 CPU 边界 | 显式 Profile 目标服务 |
| --- | --- | --- |
| init | Query 取数和复制、初始 state 与入口列表构造 | HostCpu |
| score_prepare | 按语义 chunk 取 ID、随机寻址、分配与复制向量工作集。resident_random 包含同循环中的 scored 插入 | 内存 HostCpu。SSD Host 只用返回缓冲条件下实际准备的工作，等待和 DMA 由 MQSim；Controller 由本地读及 BufferModel 覆盖 |
| score_l2 | 整个 squared_l2，包括其内部 FP32 decode、参数检查、输出分配和编码 | HostCpu 或原 Controller 算术模型。Controller 的独立缓冲端口服务单列 |
| merge | 本批 packed 得分拼接、旧/新候选合并排序、相关结果替换与容器释放。每个 chunk 拼接后的工作集销毁也归此桶 | HostCpu |
| select | SELECT 核心、rank decode 和输出 ID decode | HostCpu |
| adjacency_prepare | 按选择顺序寻址、有效邻接 decode、行对象分配与填充。resident_random 包含同循环中的 expanded 插入 | 内存 HostCpu。SSD 的设备读和传输由 MQSim，CPU 仅承担实际返回缓冲 decode/准备 |
| dedup | 核心去重、输出 ID decode、fresh 列表替换与邻接行销毁 | HostCpu |
| state | ssd_return_buffer 下单独发生的 scored/expanded 插入。resident_random 下两种插入分别归对应的准备桶 | HostCpu |
| finish | top-k 切片/解码、结果构造和剩余查询私有容器销毁 | HostCpu |

原生内存路径的 MARK_SCORED 与 MARK_EXPANDED 仍是独立语义节点，其插入时间分别由对应准备桶计费。SSD 返回缓冲条件须将这些插入归单独状态桶，且实际 Host 准备与 Controller 的本地数据准备各归自己的资源，禁止跨条件套用实测值。评分外层审计从 chunk 取数准备开始，到 squared_l2 产生 packed 得分结束，排除后续拼接与销毁。外层审计只用于闭合。仿真框架特有的内容取用、实际功能函数宿主墙钟、JSON/引用/校验及日志归仿真器开销。继承 [唯一资源归属](contracts.md#4-每项服务与流量的唯一归属)和[盘控阶段](controller.md#生命周期与服务阶段)，默认 legacy 成本公式及计费继续生效。

有限批次所有查询在 offered=0 时可供给。FIFO 接纳进入最多 max_inflight 的队列，FIFO worker 领取并占有至最终 Host 可见。记录 offered → admission → worker_start → visible → drain_end，drain_end 为批次排空界。Latency 为 visible − admission，offered latency 为 visible − offered，worker wait 为 worker_start − admission。QPS 分母为最早接纳到最后可见；加载、哈希、预热、拟合、写报告在窗口外。分位数为 nearest-rank 并报告样本数。CPU 服务、资源等待和查询关键路径分别报告。CPU slots 是服务并发，不从 max_inflight 推断。严格单线程为 1/1/1，2/4 worker 诊断 slots 等于 worker、max_inflight 为 32，仍记录实际使用配置。

Measurement 的时间统一整数 ns，墙钟必须单调，CPU 区间来自线程 CPU 时钟或等价来源，记录时钟来源与分辨率。每个样本带原始 query、worker、局部 op、评分 chunk、区间序号 segment 和桶，批量样本的同桶 calls 必须在一个连续且独占的区间内。按固定查询顺序、局部操作和语义 chunk 排列各桶的 eligible calls。同一 op 可有多个互斥 merge 区间，segment 按实际调用次序从零编号。评分两个子阶段用同一采样选择，从第一个开始每 stride 个采样，保留 eligible/planned/observed 数并拒绝缺报。跨产物验证须独立从真实操作重建 eligible inventory。非 CPU 采样模式保留查询窗口，完整静态拓扑独立落包，CPU 样本不进入包。

空计时探针与样本使用同一时钟和探针协议。每次开销取 empty_probe_ns / empty_probe_count 向下取整，按 calls 扣除。raw=end−start，corrected=raw−overhead，低于 calls × 分辨率为 low_signal，保留负修正值，训练排除低信号值或预先合并同类连续调用。评分审计必须包住恰好两类阶段且间隙不超过外层原始时间的 5% 或两倍分辨率乘 calls，两者取大。原始归因与查询线程 CPU 总量分列，抽样空白和外层未归因分别报告；训练冻结主要桶覆盖与短算子质量规则。任何未归因时间不得用于墙钟残差拟合。记录完整性、丢失数和排空。窗口报告与比较使用时钟来源，实机频率、绑核不可取时标 Unsupported。

## Profile、有效配置和复用

Profile 按算子与桶记录训练 Measurement 哈希、训练 query ID、拟合报告身份、样本数、误差和选型规则。记录 CPU/OS/内存、构建/编译选项、准备上下文、维度、记录数及 worker。特征声明单位与闭区间。线性模型按有序特征数组给出非负系数与截距，分段表使用单特征递增闭上界，从下一整数接续，覆盖全部声明范围。返回有限非负整数 ns，向上取整，检查 uint64 上界，允许零服务异步完成。缺模型、身份不符或越界须在使用点拒绝。预检校验文档内部一致性，Profile 覆盖、训练来源完整性、实际条件绑定及范围门控由模型和 Validator 实现。tuning32 的原始 ID 须与 evaluation128 独立比对，禁止评价数据回流训练。fixture Profile 只用于夹具。新版矩阵规定的逐调用实测表是单独版本化的服务输入。表绑定切分、L/E、静态包、输入、构建和运行条件，运行时按稳定操作键与本次实际 Work 核验，缺项或失配须失败关闭。v4 将表及其原始测量 hash 显式绑定到 Profile，v3 的分段模型表仍按训练来源处理。

`ndpsim-cpu-profile/3` 要求 `DEDUP_NEIGHBORS/dedup` 使用 `log_gbdt`。每棵树按前序存储，数值节点为叶值，分支按零基特征索引取整数值，小于等于阈值走下一节点，否则走显式右子树索引。最多 180 棵树，树深最多 3。依树序用 binary64 累加叶值，计算 `max(1, ceil(exp(initial + rate * sum)))`。加载时检查完整树结构、阈值和服务上界，估计前仍检查所有特征范围。模型使用同一 dedup 桶及同一计费边界。

`ndpsim-cpu-profile/3` 为 `MERGE_CANDIDATES` 声明两个必需的线性模型。在 `SCORE_L2` 内，每个 chunk 的得分拼接和工作集释放使用 `merge_append`；在 `MERGE_CANDIDATES` 内，批末候选合并使用 `merge_finalize`。两段分别检查范围，越界即失败。v1/v2 加载时拒绝。v4 保留 `merge_finalize` 模型并强制 `merge_append` 使用对应静态包的实测表。Measurement 的 `merge` 桶、segment 和 replay 日志的 `merge` 阶段名继续用于区间对齐，Validator 以操作边界独立选择模型。

有效配置 schema 冻结新增控制字段。旧配置仍以动态 ir、SSD 和 legacy 费用为默认。静态使用 replay + ir_input。host_memory 首版要求 replay、Host placement、显式 CPU Profile 和 worker-fifo/1；SSD 使用 legacy-ssd/1，native_workers=1 为未启用 worker 策略的规范化值。content_backend 继续单独表达 mmap 或 memory。CPU Profile 可显式用于 replay SSD，准备条件须与 SSD 返回缓冲匹配。mapping_sha256 引用本次完整资源/布局/设备及映射配置，其他继承参数沿用原有效配置和 manifest。配置校验器检查组合，运行接线须进一步核验引用哈希与实际输入。驻留 2/4 worker 诊断复用经过单 worker 适用性核验的冻结服务需求，并显式记录诊断用途和实际运行配额。

同语义包可改变合法 Profile、worker/slots/接纳、驻留、布局、设备配置和评分放置并重新调度。数据、图、query 内容/集合/顺序、搜索参数（含语义 chunk）、算法、数值规则、容器/计数或其他影响语义的构建变化必须重采。训练与评价独立采集。detail8、旧动态 IR/events 和采集时序均不能替代全查询静态包。真实 SSD、PCIe、Controller 继续为 configured_unvalidated。
