# V04-4b Level A 算子评价

依据 [PRD §6.4.4、§6.5、§8](prd-v0.4.md) 和 [实施清单](implementation-plan-v0.4.md)。依赖 [V04-2b Profiler](profiler-v0.4.md)、[V04-3b 冻结 v3](cpu-fit-v0.4.md)、[V04-4a Validator](validator-v0.4.md) 及 V04-0 完整输入。沿用冻结分类和模型，不用评价 Work 或时间调参。

## 复现

用与训练绑定的原生二进制和锁定的 Python。从仓库根目录运行，采集与评价输出均使用新目录。采集入口复核完整 SIFT1M 输入、锁定 NSG 图及查询顺序、构建、Mac 芯片及 RAM、压力和磁盘预算。每点三次独立进程，按重复轮次交替遍历四点。每次使用一个 worker 和一个接纳槽，固定 k=10、B=1、chunk=16。每桶从第一个调用开始每 16 次计时，四点同一规则。CPU 计时独立于静态 IR 和基准运行。

```sh
py=/opt/homebrew/Caskroom/miniconda/base/bin/python3.13
start=results/v0.4/start-v040/final1/manifest.json
profile=configs/profiles/v04-merge-split.json
fit=results/v0.4/cpu-fit-v043b/v3-only/final/fit-report.json
prepared=results/v0.2-t6/preparation/prepared
"$py" -B tools/collect_v04_level_a.py \
  --profile "$profile" --start-manifest "$start" --binary build/v042c-native/ndpsim_native \
  --prepared "$prepared" --raw .cache/v0.2-m0/data/sift --builder results/v0.2-t2/builder \
  --output results/v0.4/level-a-v044b/recollect
"$py" -B tools/evaluate_v04_level_a.py \
  --profile "$profile" --fit-report "$fit" --start-manifest "$start" --prepared "$prepared" \
  --collection results/v0.4/level-a-v044b/recollect --output results/v0.4/level-a-v044b/recheck
```

评价入口从独立 Python 搜索核对逐查询 top-k、计数和 Work，并独立投影算子阶段、完整调用清单和应采样位置。逐条核对原生样本的 shard、时钟区间、算子、阶段、形状和 Work，所有真实调用均检查冻结特征范围，包括未计时位置。数据缺失、身份失配或越界失败关闭。所有越界位置单列；范围内误差继续报告，用于定位偏差。

每个采样位置以三次修正线程 CPU 时间的中位数作为点估计。单次短调用阈值是时钟分辨率与空探针开销两者中较大值的四倍，跨重复以阈值中位数与多数低信号标记分类。短调用使用绝对误差并保留原始质量；相对误差采用 nearest-rank，逐点判定主要算子及有相对样本的主要阶段。混合时长 merge 保留拼接与批末阶段，任何主要算子缺乏可用于相对误差的样本都无法通过。其他阶段仍报告样本、质量与误差。采样 CPU 占比仅描述被采样调用。

`level-a.json` 含逐点、算子、阶段、形状、质量、逐次波动及身份；`residuals.csv` 保留全部计时重复和中位估计的带符号残差；`unsupported.jsonl` 列出预测拒绝。退出 1 表示证据错误或数值门槛失败，退出 2 表示数值门槛通过但仅具诊断资格。现行 v3 在开发 dedup 前已见同批 evaluation128 的结果，按冻结披露保留诊断资格，AC-04-04 保持未通过。

## 2026-11-04 实测与验收状态

在 Apple M4、16 GiB、macOS arm64 上用冻结 v3 和绑定的仅原生二进制运行。输入为锁定 NSG 图、完整 1,000,000×128 base 与 evaluation128。四点各 3 次独立进程共 12 次全部退出 0，测量窗 major fault 均为 0。原生峰值 RSS 约 741 MiB，最长原生进程约 122 秒。首轮 stride 1 在 32/64 触发 16 MiB 样本记录上限，采集入口退出 1，保留 `run1/`。用新目录 `run2/` 固定 stride 16 重跑，记录 38,913 个采样位置、116,739 条原始区间。独立 oracle 核对所有重复的逐查询结果、Work 和采样位置，四点共检查 622,419 次调用的模型范围。

| L/E | 采样位置 | 越界调用 | Merge 算子 P50 / P90 | Dedup 算子 P50 / P90 | 逐阶段判定 |
| --- | ---: | ---: | ---: | ---: | --- |
| 32/64 | 3,327 | 0 | 1.17% / 17.30% | 1.40% / 4.20% | 数值门槛通过 |
| 64/128 | 5,779 | 0 | 0.87% / 9.80% | 1.40% / 3.53% | merge_append 超标 |
| 128/256 | 10,454 | 4 | 0.43% / 2.86% | 1.28% / 3.45% | merge_append 超标，邻接准备和 dedup 越界 |
| 256/512 | 19,353 | 1,016 | 0.31% / 1.51% | 1.64% / 8.74% | merge_append 超标，邻接准备、dedup、select 和 merge_finalize 越界 |

表中分位数仅计算范围内非短样本，越界单独阻断。除 32/64 外，merge_append 只有 7、15、14 个非短采样位置，对应 P50 约 33%，P90 约 40%～54%。各点短拼接调用 MAE 为 20.86～25.86 ns。评价入口对有相对样本的主要阶段额外应用同一数值门槛，防止算子汇总掩盖阶段偏差。四点数值总判定失败，既有查询暴露也使本批仅具诊断资格。未修改 Profile 或使用本批结果拟合。

本机证据在 `results/v0.4/level-a-v044b/`。`run2/collect.sh` 记录本次实际运行的采集命令，使用与复现入口相同的预算和采样参数。`report1/level-a.json` 的 SHA256 为 `da5e2b819468265e93bb652941a9540a9fba3d4ddd3bcdeb282a5675e4c7e5a7`，评价退出 1 表示数值门槛失败。根 CMake 配置、`-j2` 构建、CTest 91/91、Python 语法检查及 `git diff --check` 均退出 0。错冻结 Profile 定向检查按预期退出 1。CTest 的 Validator 夹具新增真实样本核对、修改原始计时字段、缺样、范围拒绝和短样本误差断言。

本批数据仅适用于记录的内存驻留单 worker 条件，核绑定和频率未控制。固定间隔采样留下未实测形状，报告单列它们并继续检查模型范围。本轮改动集中在离线采样编排、评价和测试，完整继承 AC、52 条深度回归、全查询静态 IR 与 Level B/C 属于其各自验收范围。
