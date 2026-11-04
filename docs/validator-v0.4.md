# V04-4a Validator 框架

对应 [PRD §6.5](prd-v0.4.md#65-validator) 和 [实施清单 V04-4a](implementation-plan-v0.4.md)。前置格式和参考语义来自已完成的 [V04-2a](contract-v0.4.md) 和 [V04-1c](reference-semantics-v0.4.md)。入口读取 [原生观测](profiler-v0.4.md)、[CPU Profile](cpu-profile-v0.4.md) 和 [驻留回放](replay-v0.4.md) 的现行产物。

## 输入与运行

一次核对一个搜索点。所有运行使用同一 dataset-v2、图、真值、原始查询顺序和搜索参数。原生无插桩、静态采集及独立 CPU 采样各用单 worker、单接纳和同一原生构建。用显式 `uninstrumented`、`static_capture`、`cpu_sample` 模式产生清单，CPU 采样使用 stride 1。采集目录中的 `package/` 必须覆盖全部查询，可附带 `--detail8`。只有无插桩批次进入基准时间比较。

模拟回放使用 1 worker、1 slot、单接纳、Host、`host_memory`、显式 Profile 和 `event_log: true`。提供同搜索点的单接纳 Host direct 和动态 ir 原始产物，两条参考路径均开启 `semantic_log`，并与回放使用同一模拟库构建。

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/validate_v04.py \
  --scope evaluation --capture CAPTURE --baseline NATIVE_UNINSTRUMENTED \
  --samples CPU_SAMPLE --profile CPU_PROFILE.json --fit-report FIT_REPORT.json \
  --replay RESIDENT_REPLAY --direct DIRECT_HOST --ir DYNAMIC_IR_HOST \
  --output NEW_REPORT_DIRECTORY
```

`evaluation` 要求锁定的 tuning32/evaluation128 原始 ID、完整 base 形状、生产接纳及冻结拟合报告。`quick` 使用 `fixture` 切分和夹具 Profile，训练 ID 仍须与核对查询隔离。运行时保留原始输入文件和回放引用的产物。定向夹具由 CTest 入口自行生成，复现时可传入一个新目录保存所有子命令、日志和报告。

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 \
  tests/test_v04_validator.py build/delivery results/v0.4/validator-v044a/recheck
```

## 断言与报告

- 先核验整包、分片、依赖、状态、输入及构建身份，再用独立 Python 搜索重算固定维序 FP64 分数、排序、扩展、去重、访问和 Work。逐操作比较独立重算值、采集值与回放实际核验事件。按原始 query ID、批次、算子出现顺序及 CPU 阶段和分块关联，任务 ID 只用于各日志内部关联。
- 逐阶段核验原生样本覆盖、特征、Work、独占计时及质量标记。用独立重算的特征检查 Profile 范围和逐任务服务时长，核对发布时序、驻留字节、CPU 忙时和资源排空。分别对 direct 和动态 ir 核对实际评分、访问、路径和 Work。
- 输出 [JSON schema](../schemas/ndpsim-validator-report-v1.schema.json) 对应的 `report.json` 和 `report.md`。JSON 保存命令、退出码、输入身份、SHA256、检查状态和逐查询偏差。单次 QPS、P50/P95、CPU 总量及占用使用各自的模拟或实测窗口。Level C 的单次门槛标记同时要求 QPS 和 CPU 总量符合门槛。
- 所需证据缺失、失配或越界时首错退出 1，保留已完成的断言和定位。核对完成退出 0，点报告状态为 `diagnostic`。Level A/B/C 保留 `not_run`，其他运行模式保留 `not_applicable`。小夹具可能测得零线程 CPU，相关相对误差留空。

## 本轮验证

本轮只执行自包含合成夹具与根构建、CTest。夹具通过真实原生程序、SST 驻留回放和两条 SSD 参考路径核对 2 个查询、70 个操作、18 次逻辑读取和 62 段 CPU 服务。16 个对抗用例均按预期拒绝，覆盖重封包后的数值与 Work 篡改、依赖及状态错误、错构建、切分重叠、错模型哈希、模型范围、样本缺失、实际读取与 Work、服务时长及排空异常。QPS 接近但 CPU 总量超标的合成指标无法满足 Level C 单次门槛。

根 CMake 配置和 `-j2` 构建均退出 0，CTest 91/91 通过，独立夹具复跑和 `git diff --check` 均退出 0。

本机证据见 `results/v0.4/validator-v044a/final2/`。`evidence.json` 的 SHA256 为 `ccf1e4e9d526abc39f9f57a9cfcbd081260edcac5a32433505f5286cf63d3c55`。失败尝试与最终复跑分别保存。完整规模评价、采样精度判定、重复/波动汇总、多 worker 和 B/C 下沉仍由各自任务完成。Validator 当前按点分组读取事件，内存占用随该点详细日志增长。SSD、PCIe 和 Controller 仍为 `configured_unvalidated`。本次生产代码新增离线读取入口，模拟器、默认执行路径及原产物格式无改动，根 CTest 覆盖本次继承检查，完整继承 AC 与 52 条深度回归未执行。
