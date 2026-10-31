# T3 / M1 真实小数据闭环

T3 将 T1 dataset-v2、T2 index-v1 与 truth-v2 接到现有 SST GraphHost。算法仍是 **NSG 图 + ndpsim-ranked-expansion-v1**，不是 Faiss/NSG 搜索复现。候选、整批等待、按 ID 去重、扩展预算和计费保持 [搜索说明](search.md) 的原语义；控制成本标识 `ndpsim-candidates-v1`，布局 `independent-sector-v1`，固定维序 FP64 平方 L2、距离/局部 ID 排序。wire schema 不变。

在原有 experiments/host.json 配置上增加以下字段，将 search.entry 设为 index.json 的 entry，其余搜索和资源字段保持原 schema：

```json
{
  "format": "ndpsim-experiment-v2",
  "index": "/absolute/path/to/index",
  "truth": "/absolute/path/to/exact-truth"
}
```

index/truth 指向目录，相对路径以输入配置文件目录为基准。显式提供 `--data`，不在模拟阶段生成真实图或真值。运行器在启动 SST 前检查全部数据、图、映射、入口和真值身份；入口不匹配拒绝，不静默覆盖。保留旧 v1 路径和默认参数。

```sh
python3 tools/run_experiment.py --config real.json --data prepared --build build/delivery --output results/real --event-log
python3 tools/compare_placements.py results/real/host results/real/controller --output results/real/comparison.json
```

SST 的 vector_file/query_file/graph_file 参数绑定验证后的实际二进制文件，不需要复制到旧数据目录。功能内容仍通过 ContentStore、Layout、Runtime 与 Handles 的普通/本地读取门控取得；全布局预装载排空后才能接纳查询，T4 已提供 mmap/公式布局及受控大 profile，见 [内容/profile](t4-content-profiles.md)。离线真值与原生搜索诊断不传给 GraphSearch，不生成正式访问 trace。

v2 results.json 和 queries.csv 增加 original_query_id、one_recall_at_r、result_r、insufficient_results。当前结果长度 R 固定等于搜索 k，明确区别图 max_degree；Recall@k 用真值前 k 的集合交集，1-recall@R 用真值首项。K≥k 与官方 ID-only 走同一质量接口，不要求真值距离；官方并列边界 unresolved 继续保留，不制造距离。模拟返回距离始终来自实际算子。manifest 保存完整数据/索引/真值身份、原始映射、搜索/成本/布局/数值版本、实际代码及依赖哈希；运行结束再次校验输入身份。

对照器独立从输入向量与邻接重放每批候选、扩展和 top-k，距离容差 abs 1e-9 + rel 1e-7；核对普通读完成与决策可见性、本地读/评分/返回因果、命令和数据 PCIe 唯一计费、盘控向量 D2H=0 与 credit 归零。两放置必须绑定相同完整输入身份和搜索/主机配置。

复跑本轮小规模验收（复用 T2 的已校验产物）：

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/verify_t3.py \
  --prepared results/v0.2-t2/real-final --build build/delivery --output results/v0.2-t3/repeat
```

验证原始真实 10k SIFT、32 条预留诊断 query，再交换两个实际参与首轮评分的 base 向量。扰动保留图、查询、ID 和资源参数，更新数据/索引绑定并重算精确真值；该索引显式标记 counterfactual，不声称为扰动后重新构建的 NSG。两份输入分别跑两放置，验证初始扩展相同、后续扩展和实际读取改变。失败时 manifest 保持 passed=false，不能作为成功缓存。

该验证只覆盖 M1 和 AC-02-05 的小规模出口；不运行 T4～T8。资源观测仍使用完整历史容器；详细日志和独立对照器会占用随事件数增长的内存。wait4 为子进程峰值，不是进程树同时驻留监控，RSS 超限在完成后判断；磁盘仅产物快照。硬件参数仍 configured_unvalidated，不承诺绝对性能或盘控更快。
