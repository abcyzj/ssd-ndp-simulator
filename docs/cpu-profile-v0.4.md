# V04-3a CPU Profile 模型接口与失败关闭

需求来自 [PRD §6.3、§6.4 和 AC-04-09](prd-v0.4.md)。实现使用 V04-2a 冻结的 [Profile 契约](contract-v0.4.md)。V04-2c 已完成 tuning32 训练采集，本项使用合成夹具验证接口。

## 加载、估计与计费

- `CpuProfile::load(path, expected_sha256, actual, allow_fixture=false)` 校验同一份原始字节的 SHA256、版本化 JSON Schema 和模型内部约束。调用方必须独立提供目标硬件、系统、内存、频率、亲和性、原生构建和编译选项、输入维度/记录数、准备上下文、worker 数。所有适用条件精确匹配。夹具须单独授权。
- `profile.estimate(op, stage, work, extra)` 成功返回适用的整数服务 ns，失败抛带根因的 `content::Error`。七项逻辑计数来自实际 `operators::Work`，维度来自已绑定的适用条件，其余声明特征由受信适配器提供。每项声明特征，包括常数模型的特征，都须落在闭区间。缺失特征、计数覆盖、未知算子/阶段和缺模型均拒绝。
- 线性模型按声明顺序以 binary64 累加乘积，关闭 FMA 和 fast-math，按 `ceil-u64` 向上取整。加载时检查整个声明域的最大成本。分段表选第一个覆盖特征值的闭上界，后段从下一整数接续。常数与表项的 JSON 无符号整数保持精确，支持 `UINT64_MAX`。线性计算若在 binary64 中已舍入到 2⁶⁴，按溢出拒绝。
- 在 `HostConfig.cpu_profile` 显式绑定模型后，HostCpu 的每次提交都须指定 `CpuStage`。输入可见且本次功能计算完成后，适配器可从实际输入和结果计算附加特征。估计失败经 Runtime 传播，结果不可见且释放占用的服务槽。合法估计继续由既有 FIFO、EventBridge 和完成回调调度，包括零时长服务。旧公式保留为未配置 Profile 时的默认路径。
- `resident_random` 将两类插入计入对应准备阶段，加载器拒绝在此条件下另设 `state` 费用。`JOIN` 保持零目标服务。各静态算子阶段的实际 Work 与数据准备归属按 [V04-2a 契约](contract-v0.4.md#cpu-桶资源和窗口) 接入。

Graph CLI 的 direct 和动态 IR 拒绝显式 Profile。[V04-3c](replay-v0.4.md)在 SST GraphHost 的显式驻留 replay 接入 Profile 和阶段特征。加载器检查训练来源字段的形状与 tuning32 条数。Measurement 内容、原始 ID 切分、采样完整性及全路径模型覆盖须在后续 Validator 和执行路径中另行核验。

## 本轮验证

根配置、构建及 CTest 89/89 通过。新增 `cpu_profile_fail_closed` 用真实 Runtime 和合成 Profile 核对手算、负例及服务槽排空。继承测试包含默认路径、direct/动态 IR 的结果、Work、时间及账本对照。

| 模型 | 手算夹具 | 服务 ns |
| --- | --- | --- |
| 常数 | ceil(2.25) | 3 |
| 多变量线性 | ceil(0.25 + 2 × 0.5 + 6 × 0.25) | 3 |
| 分段查表 | elements 为 [0,1]、[2,3]、[4,5] | 0、8、UINT64_MAX |

同一评分 Work 切换 Profile 得到 4 和 3 ns。缺模型的任务和依赖任务失败，独立任务完成 3 ns 与 0 ns 两笔服务，失败任务无 CPU 服务账本记录。另用锁定 Python 预检三模型 Profile 和 replay 有效配置，并分别通过 Graph CLI、SST 对省略 engine、显式 direct、显式动态 ir 验证六个拒绝实例。

证据目录为 `results/v0.4/cpu-profile-v043a/run2/`，保存命令及退出码、构建和测试日志、负例输出、输入/源码/依赖/构建及产物 SHA256。证据范围为接口、失败关闭和继承回归。真实拟合、独立精度、全量静态执行及完整 AC-04-09 仍待相应任务。

## SSD 返回缓冲条件预测

[V04-5a](offload-v0.4.md)在显式 SSD 静态路径启用 preparation_context=ssd_return_buffer。准备与 state 阶段的配置假设用 samples=0、error=null、明确的 selection_rule 标识，允许的阶段为 score_prepare、adjacency_prepare、state。Python 和 C++ 拒绝其他阶段、驻留上下文或 sample/error 不一致的组合。已有实测模型继续保留正样本数与误差对象。模型的构建、硬件和特征范围仍须匹配。
