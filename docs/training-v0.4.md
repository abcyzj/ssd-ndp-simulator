# V04-2c 训练采集与 A/B/A

依据 [V0.4 PRD §6.4.4、§8.1、§10](prd-v0.4.md) 和[实施清单](implementation-plan-v0.4.md)。入口复用 [V04-2b Profiler](profiler-v0.4.md) 和 [V04-2a 冻结契约](contract-v0.4.md)，仅运行完整 SIFT1M、锁定 NSG 图和 tuning32。输入预检重验 V04-0 锁定 payload、原始 ID 顺序、真值和 builder 身份。evaluation128 的文件完整性及 ID 隔离在预检中核对，搜索进程只接收 tuning32 的查询和真值。

## 复现

使用仓库锁定的 Python 和原生构建。输出目录必须位于 `results/v0.4/` 下且尚不存在。

```sh
cmake -S . -B build/v042c-native -DNDPSIM_NATIVE_ONLY=ON -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
cmake --build build/v042c-native -j2
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/collect_v04_training.py \
  --binary build/v042c-native/ndpsim_native \
  --prepared results/v0.2-t6/preparation/prepared --raw .cache/v0.2-m0/data/sift \
  --builder results/v0.2-t2/builder \
  --frozen-inputs results/v0.4/start-v040/final1/inputs.json \
  --output results/v0.4/training-v042c/run2
```

先在 L/E=256/512 分别试采完整静态包和 stride 8 CPU 样本。以 pilot 的完整目录字节加 25% 冻结单次输出预算，并以最大实际分片的两倍冻结单查询缓冲，两者分别有 64 MiB 和 4 MiB 下限。目录字节包含采集分片、暂存包、索引和报告。校验流式读取暂存包，通过后原地重命名。全程检查卷剩余空间、内存压力、原生与 launcher 峰值 RSS、单次时限和查询窗口 major fault。原生 RSS 使用 wait4，Python 后处理使用 RUSAGE_SELF，二者均为进程峰值。总盘预算按逻辑文件大小核对，保留失败目录。

正式采集固定一 worker、接纳上限 1、k=10、B=1、chunk=16。四个 L/E 点为 32/64、64/128、128/256、256/512，CPU 按桶调用顺序 stride 8，静态包每点覆盖全部 32 条查询。另以 L/E=16/32、stride 1 增加低工作量形状，并观察无抽样缺口时的桶外 CPU。各点分别执行低干扰基准、CPU 采样与静态采集，逐查询核对结果、Recall、决策计数和 Work，再从静态操作流独立重建采样位置。采样和静态采集的 QPS 仅用于报告插桩开销。

L/E=128/256 顺序执行三组 A/B/A，每组保留前后无插桩 A、低干扰基准 B，以及 QPS、延迟、缺页和 A 漂移。门槛是三组 `abs(B - mean(A_before, A_after)) / mean(A_before, A_after)` 的中位数 ≤5%。分类规则先写入 `plan.json`，再由四个训练点的 CPU 样本冻结主要、短和次要桶。报告保留每桶可用样本数、形状范围及估算 CPU 占比。低信号样本保留供审计，训练排除。估算占比仅用于分类，实际 CPU 训练目标来自独立采样。

现行 [验收矩阵](validation-matrix-v0.4.md) 将 AC-04-03 的 A/B/A 主配置改为 64/128。下列 128/256 结果保留为历史证据，64/128 待补测。

## A/B/A 实测

本机 Apple M4、16 GiB、macOS arm64。L/E=128/256 的 QPS 及相对扰动如下。

| 组 | 前 A QPS | B QPS | 后 A QPS | B 绝对扰动 |
| --- | ---: | ---: | ---: | ---: |
| 1 | 11.6798 | 11.6329 | 11.6813 | 0.41% |
| 2 | 11.6370 | 11.6670 | 11.6239 | 0.31% |
| 3 | 11.6769 | 10.4718 | 11.4694 | 9.52% |

中位数为 0.41%，通过 ≤5% 门槛。第三组单独超过 5%，保留全部样本和延迟，未归因于某个具体系统事件。核绑定和频率未控制。

## 采集与验证结果

| L/E | 静态操作 | 依赖 | CPU 样本 | 低信号 |
| --- | ---: | ---: | ---: | ---: |
| 32/64 | 9,776 | 23,114 | 1,656 | 0 |
| 64/128 | 17,829 | 42,239 | 2,896 | 0 |
| 128/256 | 34,090 | 80,858 | 5,225 | 2 |
| 256/512 | 66,773 | 158,474 | 9,666 | 0 |
| 16/32（补充） | 5,816 | 13,709 | 8,217 | 2 |

正式运行含 pilot 共 25 次，全部完成且查询窗口 major fault 为 0。五点结果、Recall、决策计数与 Work 跨模式一致，静态包与 CPU 样本位置核对通过，评分区间审计间隙均为 0。stride 8 的未采样与桶外 CPU 合计约 87.5%～88.3%。补充点 stride 1 的桶外 CPU 为 8.44%，仅代表该点。峰值原生 RSS 约 762 MiB，Python 自身约 86 MiB。最大 pilot 的输出总量为 399,051,724 字节，发布包 216,503,747 字节，索引 11,364 字节，暂存分片 182,447,412 字节，额外校验临时文件为 0。正式输出、单查询缓冲和时限分别冻结为 476 MiB、16 MiB、300 秒。

最终分类将 merge（估算 CPU 占比 84.19%）与 dedup（12.41%）列为主要桶。merge 同时保留短调用标记，init 列为短桶。训练内核查发现初始分类给短调用标记优先级，会把混合时长且占 CPU 大头的 merge 移出主要桶。最终改为分别标记主要桶和短调用，保留原阈值、样本、估算占比及初始报告。最终训练冻结入口为 `results/v0.4/training-v042c/checks2/training-freeze.json`，绑定采集数据集和修正分类。原 `run2/plan.json` 的索引单列误为 0，索引字节已包含在总预算。独立核验和冻结入口记录了实际索引字节，后续复跑使用修正后的采集器。

`results/v0.4/training-v042c/run2/` 保留原始采集数据集、包、覆盖、九次 A/B/A 样本、输入/配置/构建/源码/依赖身份、命令、退出码及 SHA256。根配置、构建、CTest 88/88，原生独立构建和链接检查、6 项 A/B/A 报告断言、25 次运行与 664 个产物哈希复核均通过。先行 `pilot1/` 的系统计时包装因读取 kern.clockrate 被拒而退出 1，其原生静态包校验成功。`run1/` 保留外层 RSS 误计入原生子进程峰值而触发 512 MiB 检查的失败。正式 `run2/` 单独采集 Python 自身峰值，使用新目录完整重跑。

本轮关闭训练采集及 AC-04-03 计时与扰动门槛。采集基于当前 Mac 的内存驻留、单 worker 条件。核绑定与频率未控制，采集 QPS 仅作开销诊断。训练范围是否足以覆盖独立评价仍待验证。本轮范围止于训练数据冻结，CPU Profile 拟合、独立评价采集、模拟静态执行、CPU 精度、14 配置、完整继承 AC 和 52 条深度回归未执行。
