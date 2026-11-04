# V04-3b CPU Profile 训练拟合与冻结

[现行 v3 Profile](../configs/profiles/v04-merge-split.json) 是 V04-3b 唯一受支持的冻结版。它使用浅层 GBDT 估计 dedup，使用两个非负线性模型分别估计 chunk 得分拼接和批末候选合并。其余阶段沿用原冻结模型。将 `cpu_profile.path` 指向该文件，并填写对应的 `cpu_profile.sha256`。相对路径以配置文件所在目录解析。

```text
Profile SHA256  1f52e9333a501a2f078504cf4794099712dd8bdf79c2666e70d8d5b5ef3e66ec
拟合报告 SHA256  928805e997bcc334b19f4d2e4daa880197b7a401137d3dbd996c45a639a0c32c
```

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
- evaluation128 的测量未进入本次冻结。此前 dedup 开发前已披露 evaluation128 结果，复用该批查询的结果仍作为诊断。独立 Level A 与 AC-04-04 待验收。适用条件仍由 Profile 绑定到原采集硬件、构建、驻留数据及单 worker。
- 历史冻结和验证分别位于本机 `results/v0.4/cpu-fit-v043b/run1/`、`dedup-gbdt-v2/` 和 `split-merge-v3/`。v1/v2 属于历史证据，现行加载器拒绝这两个版本。

根 CTest 91/91、拟合定向测试 4/4，覆盖 v1/v2 拒绝、缺失模型、范围与身份检查。两次从训练输入生成的 v3 Profile 和报告逐字节一致。SIFT1M tuning32 L/E=32/64 驻留回放与此前 v3 的 13,230 笔服务、语义日志、top-k 和驻留账本逐字节一致。
