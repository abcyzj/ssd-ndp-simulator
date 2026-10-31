# T2 图与真值准备

T2 接受 T1 的 `ndpsim-dataset-v2`。离线 Faiss 与 SST 查询实现独立；不将建图或真值成本计入模拟时间。图来自 NSG 不表示项目搜索忠实复现 NSG。T3 仍负责正式仿真接线；现有 v1 随机图、真值和模拟 CLI 保持原行为。

锁定的本机 macOS/arm64 构建（Python 3.12+ 安全 tar；示例使用锁定 3.13）：

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/m0/build_builder.py \
  --archives .cache/v0.2-m0/downloads --work .cache/t2-builder --output results/t2-builder
python3.13 tools/benchmark_artifacts.py build prepared index \
  --receipt results/t2-builder/index-builder-receipt.json --threads 1
python3.13 tools/benchmark_artifacts.py check-index prepared index
python3.13 tools/benchmark_artifacts.py exact prepared exact-truth --k 100 --block-rows 1024
python3.13 tools/benchmark_artifacts.py official prepared-with-sift-groundtruth official-truth
python3.13 tools/benchmark_artifacts.py check-truth prepared exact-truth --k 10
```

所有输出目录必须不存在。锁定归档/版本、许可证、系统工具链要求继承 [M0](m0-closeout.md)。构建工具现在同时生成 M0 探针、正式 `ndpsim_index_builder` 及 receipt；正式调用验证 receipt 的 lock、适配器源码、可执行文件和实际 OpenMP 库哈希。receipt 是可复现身份记录，不是可信签名。Faiss 不链接进 SST。原始归档仍需用户本地提供，不自动下载或安装系统软件。

`ndpsim-index-v1` 的 `index.json` 绑定 base 内容、shape、dtype、字节序、mapping、metric、preprocessing；query/learn 不参与建图。图为 little-endian `(degree, IDs[64])`，无效槽补零。局部节点 ID 等于规范化 base 行号，`nodes.ids.u32` 保留其原始行号映射。导出入口、完整 Faiss commit、依赖锁/二进制 receipt、有效线程和 RNG 策略、R/GK/L/C/build_type/search_L、图/映射哈希、图检查结果。NNDescent 固定 R/GK=64、L=200、C=500、build_type=1；不虚构 seed=42。线程可选 1..4，不承诺重建逐位一致。

图检查扫描全部节点：长度/覆盖、degree、ID、入口及有向可达性（必须 100%）；输出度数直方图、重复边、自环和总边数。保留原有效邻接顺序、重复和自环，不去重或截断。BFS 使用 bytearray 和队列，图行按需从文件读取；其内存仍随 N 增长。导出的 Faiss search_L=256 诊断 IDs 单独保存并绑定 query，可与独立精确真值比较；它们不作为模拟 trace。

`ndpsim-truth-v2` 的 `truth.json` 绑定 base/query 的内容/映射/shape/type/endian 和 metric/preprocessing，记录最大 K、生成方法、累加精度、并列规则、校验版本和工具哈希。ID 文件为无头 little-endian uint32，精确距离文件为 little-endian FP64。`check-truth --k` 接受 K≥k，拒绝错版本/身份/映射、越界或重复 ID、截断/额外字节、负值/非有限距离及不符合 `(distance, local ID)` 的排序。哈希证明一致性，不替代重新计算数学正确性。

`exact` 逐查询遍历整个已选 base，按固定维序用 Python binary64 减、乘、加，不调用模拟算子、Faiss 或 BLAS。base 按块读取，默认最多 8 MiB 字节块，单行更宽时按一行读取；只保留 top-K 堆，内存 O(block×d+K)，不为每个 base 节点建立 Python dict。按 `(distance, local ID)` 排序，K 边界同分已解决。每个 query 重读一次 base；当前纯 Python 实现有界内存，但未证明能在全规模 Deep 真值预算内完成。

`official` 只接收 M0 内容锁匹配的完整 SIFT base、query、groundtruth。T1 已按 query 原始行号选择相同顺序的真值行；此处进一步绑定三者完整源哈希。只提供 IDs、不制造距离，保留官方顺序；有限 top-100 的严格 FP64/ID 并列边界统一标记 `unresolved`。base 子集必须重新精确计算，不能过滤完整库真值。Deep 全库真值不接受为 10M 真值；其他来源可扩展新显式方法，当前不提供任意来源的通用“官方”声明入口。

`quality(returned, truth, k, result_r)` 分别给出 `recall_at_k`（前 k 集合交集数/k）、`one_recall_at_r`（真值第一近邻是否在返回前 R）、`insufficient_results`。R 是结果截断长度，和图度数无关；空/不足 k 的返回分母仍为 k，重复结果 ID 拒绝。正式仿真输出接入归 T3；T2 验证器输出原生建图器诊断的两种指标。

输出通过临时目录校验后改名；失败保留为 `<output>.failed`（不作为可复用产物），完整输出用 check 重新验证。输入在准备期间应不可变，产出前再次检查 dataset。建图先核对冻结本机预算与存储量下界、20 GiB 磁盘余量，实际子进程用 wait4 记录 RSS/墙钟并完成后核对 RSS；超时终止进程组。下界不能证明峰值可行，也不是硬 RSS 限制。完整 Deep10M 当前下界已超过本机上限，启动前明确拒绝；T7 资源门槛仍保留。

实际验证见 [T2 收口](t2-closeout.md)。复跑真实 SIFT 小子集（10k base，调换原始行 0/1；诊断 query 原始行 0..31）及完整 SIFT 官方真值导入：

```sh
python3.13 tools/verify_t2.py --source-dir .cache/v0.2-m0/data/sift \
  --receipt results/t2-builder/index-builder-receipt.json --output results/t2-real
```

产物含规范化数据、index、exact/official truth、逐查询诊断、manifest、assertions、AC 部分映射及每条命令的退出码/墙钟/RSS/stdout/stderr。验证目录被 Git 忽略，新检出须复跑；它不运行 T3 或全规模查询矩阵。
