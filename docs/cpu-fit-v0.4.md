# V04-3b CPU Profile 训练拟合与冻结

[冻结 v3 Profile](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/configs/profiles/v04-merge-split.json) 提供拟合模型与既有回放的复现基线。按新版验收矩阵，用下述工具生成绑定单一静态包的 v4 Profile。v4 的 `merge_append` 只使用逐调用实测表，`merge_finalize` 保留非负线性模型，dedup 保留浅层 GBDT。

```text
Profile SHA256  1f52e9333a501a2f078504cf4794099712dd8bdf79c2666e70d8d5b5ef3e66ec
拟合报告 SHA256  928805e997bcc334b19f4d2e4daa880197b7a401137d3dbd996c45a639a0c32c
```

## 逐调用实测表

每个切分及 L/E 点分别准备完整静态包，并独立运行至少三次单 worker 的 `run_native.py --mode cpu_sample --sample-stride 1`，设置对应的 `--split tuning32` 或 `--split evaluation128`。先核验输入、RAM、磁盘、输出预算与时限。每次采集使用不同的输出目录名。将这些 Measurement 与冻结 Profile 绑定：

```sh
python3 tools/bind_v04_merge_append.py \
  --profile configs/profiles/v04-merge-split.json \
  --package <capture>/package --binding <capture>/binding.json \
  --measurement <sample-1>/measurement.json \
  --measurement <sample-2>/measurement.json \
  --measurement <sample-3>/measurement.json \
  --output <new-profile.json>
```

在回放配置的 `cpu_profile.path` 和 `cpu_profile.sha256` 显式绑定新文件。表以包 hash、切分、原始 query ID、对应的原生 merge 操作号及 chunk 分段索引定位，核验 chunk 大小及拼接 Work。服务时间取扣除空探针开销后有符号重复值的下中位数。短调用的低信号重复仍保留，若下中位数为负或有符号和低于一计时刻度则拒绝制表。源 Measurement 保留原始重复及质量标记。正式精度查表使用与采集条件相同的单 worker、单接纳驻留回放。D01/D02 可按绑定包复用同一份单 worker 服务需求作诊断，见 [V04-4d](workers-v0.4.md)。

单点 Validator 使用重复的 `--merge-append-measurement` 参数重建表，并按独立搜索与 Work 核对实际服务。整机原生基准另跑。现有独立模型误差入口仍对应 v3 历史报告，正式矩阵的 Level A 汇总分流待接线。

## 复现

从仓库根目录执行，输出目录必须尚不存在。输入为 [V04-2c 冻结采集](training-v0.4.md) 的 tuning32 四点和 L/E=16/32 补充点。训练目标为扣除空探针开销后的单次调用线程 CPU ns。冻结入口直接从锁定测量重建全部模型和报告。

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 -B tools/fit_v04_merge_profile.py \
  --training-freeze results/v0.4/training-v042c/checks2/training-freeze.json \
  --training-sha256 1752dd9d5e284c0c7878f9a6d40a149c1dd7e9b8537c68c89a7f36fde3cbf1e7 \
  --start-manifest results/v0.4/start-v040/final1/manifest.json \
  --start-sha256 f5f461cb2edc584d5e5b27bb89882c9c26cf7245aa333d7a47189de6e8606110 \
  --output results/v0.4/cpu-fit-v043b/reproduce-v3
```

每个原始 query ID 在五个点的所有样本属于同一折。dedup 沿用 180 棵深度至多 3 的树，叶最少 12 个样本，学习率 0.08，目标为 log(ns)。merge 按原生 Work 区分区间，两个线性模型均使用 `comparisons`、`old_scores` 和 `new_scores`。其余阶段在常数、非负线性和分段表中按留出 MAE 选择。运行时独立核对模型范围，失配即失败。

## 冻结证据

- dedup 的 2,692 条范围内四折留出样本相对误差 P50/P90 为 1.64%/5.90%。merge 的 8,297 条范围内留出样本 MAE 为 983 ns，短调用 MAE 为 25 ns，整体相对误差 P50/P90 为 3.36%/23.03%。merge 训练剔除 3 条低信号样本，留出统计排除 2 条越界样本。
- v3 直接重拟合的九个模型、范围、样本数和训练误差与此前 v3 逐项一致。配套报告更新了复现入口及源码身份，Profile 哈希随之更新。本机证据位于 `results/v0.4/cpu-fit-v043b/v3-only/`。
- evaluation128 的测量未进入本次冻结。此前 dedup 开发前已披露 evaluation128 结果，按现行 AC-04-04 允许复用该批结果并披露既有暴露。两点验收结果见 [V04-4b](level-a-v0.4.md)。适用条件仍由 Profile 绑定到原采集硬件、构建、驻留数据及单 worker。
- 历史冻结和验证分别位于本机 `results/v0.4/cpu-fit-v043b/run1/`、`dedup-gbdt-v2/` 和 `split-merge-v3/`。v1/v2 属于历史证据，现行加载器拒绝这两个版本。

根 CTest 91/91、拟合定向测试 4/4，覆盖 v1/v2 拒绝、缺失模型、范围与身份检查。两次从训练输入生成的 v3 Profile 和报告逐字节一致。SIFT1M tuning32 L/E=32/64 驻留回放与此前 v3 的 13,230 笔服务、语义日志、top-k 和驻留账本逐字节一致。

## v4 接线证据

本机 `results/v0.4/cpu-fit-v043b/measured-append-fixture-2/` 保存三次独立原生采样、逐调用制表、SST 回放和单点 Validator quick 诊断。夹具两条查询共八笔拼接服务，Profile SHA256 为 `d7628648362c7f5c35c4394ba5d8567575ef02b9998136d020027e2fecfa51ad`。根 CTest 91/91，定向 SST 夹具核对了成本切换和缺项失败排空。首轮证据留在 `measured-append-fixture/`，逐样本质量门槛拒绝了短调用，第二轮采用上述聚合规则在新目录复验。完整 SIFT1M 两个切分四点的实测表及整机验收待执行。
