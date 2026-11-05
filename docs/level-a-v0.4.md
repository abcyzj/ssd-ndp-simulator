# V04-4b Level A 算子评价

依据 [PRD §6.4.4、§6.5、§8](prd-v0.4.md) 和 [实施清单](implementation-plan-v0.4.md)。依赖 [V04-2b Profiler](profiler-v0.4.md)、[V04-3b 冻结 v3](cpu-fit-v0.4.md)、[V04-4a Validator](validator-v0.4.md) 及 V04-0 完整输入。沿用冻结分类和模型，不用评价 Work 或时间调参。

## 现行验收范围

AC-04-04 正式评价仅覆盖 L/E=32/64 和 64/128。两点均须通过实测表覆盖、计时质量、身份、Work、唯一计费以及其余模型阶段的预测误差门槛。128/256 和 256/512 的 Level A 结果作为诊断。实测表采集与评价入口按现行两点范围运行。历史 v3 诊断入口和旧四点报告沿用原口径。修订后两点的模型数值门槛和两切分的唯一计费均已通过。按现行口径，既有评价暴露须披露，允许复用同批结果。AC-04-04 已通过。tuning32 和 evaluation128 仍按原始 query ID 分离，评价数据禁止回流拟合。

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

## v4 实测表重测入口

v4 沿用 v3 冻结的其余模型，将 `merge_append` 改为按完整静态包和切分绑定的逐调用实测表。先用上文相同的锁定 Python、输入与原生二进制运行采集命令，额外传入 `--measured-append` 并指定新输出目录。两个切分各采 32/64、64/128，每点独立采一个完整静态包和三次 stride 1 线程 CPU 样本。采集使用 64 MiB 单查询缓冲、2 GiB 静态采集和 1 GiB CPU 采样单次输出预算、4 GiB 原生 RSS，静态采集与后处理共用 900 秒超时，CPU 采样使用 300 秒超时，开跑前复核锁定输入、构建和本机资源。正式采集及回放要求 macOS 主机身份查询可用。

评价命令的 `--collection` 指向独立于制表的 stride 16 评价样本，另用 `--measured-collection` 指向 v4 采集目录。加 `--replay-build build/delivery` 检查驻留单 worker 回放中的计费。失败的静态采集可在新目录按原配置重跑，并通过 `--capture-retry` 传入重跑目录。缺表、采样质量不足、身份/Work 不符或模拟失败均保留分点原因。每个成功制表点会产出 v4 Profile，报告单列实测调用数、重复质量、实测 CPU 及样本 CPU 占比，并使用 SST 服务账本逐调用核对需求、Work 和唯一归属。原采样次序中的 `merge_append` 记录仍核对覆盖和 Work，其时间从模型残差、算子误差和模型采样 CPU 占比的分母中移除。两个正式点分别判定其余冻结模型。此次用 `run2` 的独立采样重新判定模型分支，复用的是历史采样，既有评价暴露仍须披露。

### 2026-11-05 暂停时的诊断结论

按用户指示暂停完整验收。两个切分的八个静态包和 24 次逐调用采样已齐备。evaluation128 的模型分支复用历史独立于制表的 `run2` 样本，证据为 `results/v0.4/level-a-v044b/v4-model-check/level-a.json`。32/64、64/128 的模型数值门槛通过。128/256 有 4 次特征越界，256/512 有 1,016 次。范围内主要阶段 `dedup` 和 `merge_finalize` 的相对 P50/P90 在四点均达标。范围拒绝分布为 `dedup` 374 次、`merge_finalize` 347 次、`select` 291 次、`adjacency_prepare` 8 次。邻接准备和收尾的范围内误差偏高，按冻结分类属于次要阶段诊断。

实测表分支的中间结果见 `v4-report/table-points.json`。已确认 tuning32 的 32/64、64/128 和 evaluation128 的 32/64 唯一计费通过。tuning32 两个大点已制表，回放分别因邻接准备和去重模型超范围停止。暂停时仍在进行的核验不计通过或失败。暂停时按旧口径未关闭验收。根 CMake 配置、构建和 CTest 91/91 已通过。最大静态包保留预算超限与后处理超时记录，最终在 2 GiB、900 秒的新目录重跑成功。

### 两点范围补验

复用 `run2` 的独立于制表的算子样本、v4 已核验的表及三份已通过的计费收据。只补跑 evaluation128 的 64/128 驻留 SST 回放，使用已锁定的静态包和 Profile。回放及逐调用账本检查退出 0，18,878 次 `merge_append` 共计 4,247,213 ns，占该回放 CPU 服务时间约 0.1754%。两个正式点在两个切分的查表覆盖、质量、Work 与唯一计费均有通过证据。两点模型数值门槛均已达到。

本机 `results/v0.4/level-a-v044b/v4-two-point/` 保存复用检查、回放命令、退出码、配置、源码快照、账本和汇总。汇总 `level-a.json` SHA256 为 `08517e5aebac2ece7fa4493fff96837fa634aef8402eb7008bc53d1fec367468`，该历史汇总退出 2，反映当时的独立性门槛。按现行规则复核同一份证据，两点技术门槛均通过，AC-04-04 关闭。评价入口仍保留历史退出语义。定向 CTest `sst_static_replay`、`v04_validator` 及 `git diff --check` 通过。
