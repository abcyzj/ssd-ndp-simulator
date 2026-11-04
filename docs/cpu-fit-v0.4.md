# V04-3b CPU Profile 训练拟合与冻结

本项实现 [PRD 的 CPU 模型与数据隔离要求](prd-v0.4.md)，输入为 [V04-2c 冻结采集](training-v0.4.md)，输出使用 [V04-3a Profile 接口](cpu-profile-v0.4.md)。只读取 tuning32 的四个正式训练点和 L/E=16/32 的训练补充点。evaluation128 只核对锁定的原始 ID 列表，交集为零。训练目标为每次调用扣除空探针开销后的线程 CPU ns，排除四条低信号记录。

## 复现

在仓库根目录用锁定的 Python 运行。输出目录必须尚不存在。本次使用五份既有 Measurement，无需重跑原生搜索或构建模拟器。输入哈希、适用条件、采样完整性或模型结构失配时退出 1，只有完成全部校验才写出 `fit-freeze.json`。

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/fit_v04_cpu_profile.py \
  --training-freeze results/v0.4/training-v042c/checks2/training-freeze.json \
  --training-sha256 1752dd9d5e284c0c7878f9a6d40a149c1dd7e9b8537c68c89a7f36fde3cbf1e7 \
  --start-manifest results/v0.4/start-v040/final1/manifest.json \
  --start-sha256 f5f461cb2edc584d5e5b27bb89882c9c26cf7245aa333d7a47189de6e8606110 \
  --output results/v0.4/cpu-fit-v043b/reproduce2
```

按 tuning32 冻结顺序将原始 ID 轮流分到四折。同一 ID 在各搜索点和补充点的所有样本始终在同一折。常数取中位数，线性模型用固定顺序的 512 次投影坐标扫描拟合非负截距与系数，查表用首特征的最近秩四分位边界及各段中位数。比较各折候选共同范围内留出样本的汇总 MAE，平手依次选常数、线性、查表。按胜出模型在所有可用训练样本上重拟合。每个模型声明训练中观测到的闭区间，分段表仅声明其单个查表特征。误差按运行时整数 ns 舍入后计算。报告按搜索点及首特征形状分桶，给出分位数、带符号平均残差及短调用绝对误差。

## 冻结与验证

冻结产物位于 `results/v0.4/cpu-fit-v043b/run1/`，复跑位于 `reproduce1/`，命令、退出码和独立检查位于 `verification/`。Profile SHA256 为 `170fedeadecde3cf2d6cb08d207690d4a2fb5c9d20206382503f3bd7bb6bd644`。冻结记录 SHA256 为 `d18aab4fb1df971764d84d9bf8dc2cade8ad7357f6ccfa07df617b35e2e6d49d`。

- 两次生成的 Profile、JSON/Markdown 报告和冻结记录逐字节一致。复核 664 个训练采集产物哈希，五份 Measurement 重验通过。八个 CPU 阶段共 27,660 个样本，其中 27,656 个可用于拟合。
- 正式 Profile 选中三个常数模型与五个线性模型。定向 Python 测试 4/4，通过三类拟合手算、查表胜出与分折隔离、低信号排除、训练范围和身份拒绝检查。运行命令为锁定 Python 加 `tests/test_v04_cpu_fit.py`。
- 用 V04-3a 的 C++ 加载器按独立采集身份加载正式 Profile。八阶段共 16 个边界估计与离线计算一致。越界、缺模型、worker 身份失配及哈希失配均拒绝。根配置、构建和 CTest 89/89 通过。
- 训练内四折合计有 13 条可用留出样本超出对应训练折的共同特征区间，分折报告逐项记录。最终重拟合使用全部可用训练样本，独立评价的范围覆盖仍待测。

主要桶 merge 的训练 MAE 为 1,444.6 ns，相对误差 P50/P90 为 239.0%/489.9%，其中短调用 MAE 为 726.7 ns。dedup 的训练 MAE 为 34,746.2 ns，相对误差 P50/P90 为 38.1%/42.8%。拟合已冻结，精度仍不足。适用条件为记录的 Apple M4、macOS、16 GiB、原生构建、完整 SIFT1M 形状、`resident_random` 和单 worker。频率和核绑定未控制。独立 Level A、AC-04-04、静态执行接线及完整规模模拟验收待各自任务。
