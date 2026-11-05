# V04-3c 静态执行与内存驻留

对应 [PRD §6.4](prd-v0.4.md#64-静态-ir-输入依赖执行与资源调度)、[静态包和计费契约](contract-v0.4.md)及实施清单 V04-3c。入口为 `tools/run_graph.py`。依赖 V04-2b 的完整采集包、V04-3a 的 Profile 接口，以及 V04-3b 冻结的 tuning32 模型。

## 运行入口

在已有图实验配置中显式设置以下字段。文件引用相对配置目录解析，数据、图、真值及搜索参数仍由原入口选择。binding 来自独立选定的输入及允许的采集构建，须与实际运行路径、搜索参数和原始查询顺序一致。

```json
{
  "execution_engine": "replay",
  "placement": "host",
  "data_provider": "host_memory",
  "order_policy": "worker-fifo/1",
  "native_workers": 1,
  "max_inflight": 1,
  "ir_binding": "binding.json",
  "ir_input": {"path": "package", "sha256": "<package_sha256>"},
  "cpu_profile": {"path": "cpu-profile.json", "sha256": "<file_sha256>"}
}
```

`host.slots` 为 1。保留继承的 `host` 配置字段，显式 Profile 启用后由阶段模型计费。可用 `event_log` 导出逐操作和读取时间线。合成夹具须显式设置 `allow_replay_fixture` 和独立的 `fixture_host`。正式路径查询本机型号、物理内存及系统身份，取不到时失败。生产包验证同时绑定原生构建和本次打开的逻辑内容。SST 参数及 admission 文件为内部接线，由运行器生成。

## 执行边界

- 运行器在私有临时目录流式复制并预检完整包，保存包、binding、Profile 和配置身份。预检校验拓扑、索引、依赖类型、状态版本、内容及尾记录。SST 逐条消费同一快照，输入文件在执行期间须保持不可变。
- 每个 worker 的查询依预检拓扑串行放行，当前查询只保留活跃 rank、状态、本批邻接或得分、一个语义 chunk 和当前操作记录。后继地址来自本次已核验的选择和去重结果。实际访问、输出摘要、顺序、Work 和终止条件失配时停止放行，释放 Runtime 引用后报告失败。
- 在 SST 的 EventBridge、Runtime 和 HostCpu 上等待服务并发布。评分分块串行执行取数准备、L2 和拼接，状态插入计入对应准备阶段。JOIN 和状态令牌无独立 CPU 费用。Profile 特征由本次真实输入和算子结果提供。账本分列逻辑内容字节、算子 Work 和 CPU 服务。
- 内存提供路径不创建 OrdinaryDevice 或物理 LBA 映射，EventBridge 只服务平台事件。功能内容仍使用选定的 `mmap` 或 `memory` 后端。mmap 在启动时校验并扫描输入，页面是否持续驻留由操作系统决定。此处的驻留指目标仿真数据路径。
- 旧动态 IR 和 direct 继续使用现有 SSD 路径及其批内并行。B/C 静态 SSD 映射、返回接线及成本适用性由 V04-5a 验证。

## 验证范围

本机证据目录为 `results/v0.4/replay-v043c/run1/`。夹具覆盖正时长和零时长服务、两个内容后端、CPU 成本切换、实际值和 Work 失配排空、旧包拒绝变更内容和原生重采。完整 SIFT1M 的 tuning32 L/E=32/64 回放使用 V04-3b 冻结模型，另以独立 Python 搜索和计数公式逐查询核对决策、得分、Work、逻辑访问与 top-k。该结果属于训练侧语义验证。Level A/B/C 独立评价、14 配置矩阵和完整 AC 仍待对应任务。

- 根 CMake 配置、构建及 CTest 90/90 通过。夹具的两条查询在每阶段 2 ns、7 ns 和 0 ns 时分别用 34 ns、119 ns 和 0 ns，top-k 一致。定向 ASan 通过，使用已插桩的平台模块及原生程序，检测范围为地址错误。
- SIFT1M tuning32 核验 9,776 个操作及 23,114 条依赖。共 36,228 次逻辑读取、18,109,656 个逻辑字节及 0 SSD 请求。执行峰值为 1 个会话和 2 条 Runtime 任务记录，终态无任务、等待者和内容租约。SST 子进程峰值 RSS 为 814,465,024 字节。
- 静态包格式不接受采集时戳，回放时间只取本次 SST 服务。驻留夹具验证准备和计算服务的发布门控。SSD 返回延迟的定向覆盖在本轮继承 IR CTest 中，静态 SSD 返回待后续接线。


## Worker 与 CPU 顺序

V04-3d 实现 [PRD §6.4 和 §7.1](prd-v0.4.md#64-静态-ir-输入依赖执行与资源调度) 的驻留调度。前置契约、HostCpu 模型接口、单 worker 回放及原生 worker 行为分别由 V04-2a、V04-3a、V04-3c、V04-1b 提供。

- `worker-fifo/1` 支持 1、2、4 个 worker，`host.slots` 等于 `native_workers`，`max_inflight` 至少为 worker 数。严格单线程配置为 1/1/1，D01/D02 的诊断配置为 2/2/32 和 4/4/32。
- 批次开闸时所有查询已供给，按绑定顺序接纳到上限。每有一条结果可见就接纳下一条。空闲 worker 从队首领取并占有查询直到结果可见。初始空闲 worker 按编号领取，同刻完成的 worker 按 EventBridge 的 continuation 顺序归还。原生线程的实际分配由 OS 决定，原生和模拟分别记录分配。
- 每个 worker 同时只有一个待完成的 CPU 阶段。评分准备、L2 和拼接逐 chunk 串行，整批得分核验后才允许后继。静态分片按索引读取，活跃查询状态受 worker 数限制。采集线程及物理分片次序独立于运行调度。
- `results.json` 和 `queries.csv` 记录实际 worker、接纳、领取、结果可见和 worker 等待。驻留 `latency_ns` 从接纳算起，包含 worker 等待。`worker_summary` 汇总配额、峰值、累计 CPU 服务/依赖/排队时间、worker 等待和排空，导出至 `observation.json` 与资源统计。首次失配保留原始 query、批次和 op，清空待领取查询，在途 CPU 服务排空，其他查询停止后继及结果发布。
- 多 worker 使用冻结单 worker Profile 的阶段服务需求，仍检查来源、身份及模型范围。报告记录 `profile_workers=1` 和 `profile_use=diagnostic`。该配置不建模 OS 抢占、缓存争用或异构核分配。正式 2/4 worker 诊断见 [V04-4d](workers-v0.4.md)，独立 CPU 精度验收和 B/C 静态 SSD 实验见任务清单。

本轮使用 11 查询合成夹具，原生 4 worker 采集后由独立 Python oracle 核对，再以 1/1/1、2/2/3 和 4/4/5 回放。常数夹具每条查询 28 段 × 2 ns，三种批次窗口为 616、336、168 ns，累计 CPU 服务均为 616 ns，累计 worker 等待分别为 0、280、112 ns。夹具核对同 worker 串行、整批门控、同刻分配、零时长、重排后记录逐字节一致、配额和 Profile 失配、并发中止及排空。根 CTest 覆盖继承的 HostCpu 依赖与 SSD 映射。本轮证据见 `results/v0.4/workers-v043d/run1/`。
