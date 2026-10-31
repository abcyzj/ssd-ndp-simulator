# T2 / M1 图与真值收口

2026-10-31（Asia/Shanghai）。T1 与 T0 建图前置已核对，工作区起始无未提交修改，MQSim/SST 的锁定子模块身份和内容未改。T2 完成；M1 还需 T3 闭环贯通。本轮未启动 T3，未提交、推送或初始化依赖。

实现 `tools/offline/index_builder.cc` 与 `tools/benchmark_artifacts.py`：锁定 Faiss CPU NSG 读取规范化 base 并导出原顺序固定槽邻接、入口和节点映射，完整记录依赖/线程/RNG/参数/输入输出身份；index-v1 全图检查及绑定；truth-v2 独立有界内存固定维序 FP64 穷举、严格距离/ID 排序；完整锁定 SIFT 官方 K=100 ID-only 真值绑定与 unresolved 并列边界；集合 Recall@k、1-recall@R 和不足 k 状态。接口及复跑见 [图与真值](index-truth-v2.md)。v1 模拟行为未改，v2 模拟接线仍等待 T3。

实际证据保存在本机忽略目录 `results/v0.2-t2/`：

- `builder/`：从 M0 锁定三份本地归档，在新 `.cache/v0.2-t2-builder/` 重建 OpenMP/Faiss、M0 探针和正式离线适配器，五条 CMake/编译/安装命令均退出 0；manifest、逐条命令日志、RSS 和 receipt 保留。不下载、不安装系统软件，不修改子模块。
- `real-final/`：最终代码的实际验证，含 8 条子进程命令、8 项明确断言、AC 部分映射、源文件哈希、原始输入锁、配置/spec、图/映射、精确和官方真值及逐查询诊断。`real/` 为首次通过，补充资源预检和 OpenMP 哈希校验后以 `real-final/` 重跑，不以首次代码身份冒充最终代码。
- `configure.log`、`build.log`、`ctest.log`、`unit.log`：常规构建/回归与单独工具测试。

实际执行（以下全部退出 0）：

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/m0/build_builder.py \
  --archives .cache/v0.2-m0/downloads --work .cache/v0.2-t2-builder --output results/v0.2-t2/builder
cmake -S . -B build/delivery -DBUILD_TESTING=ON
cmake --build build/delivery -j2
ctest --test-dir build/delivery --output-on-failure
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tests/test_benchmark_artifacts.py
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/verify_t2.py \
  --source-dir .cache/v0.2-m0/data/sift \
  --receipt results/v0.2-t2/builder/index-builder-receipt.json --output results/v0.2-t2/real-final
```

根配置复用 SST 缓存，不是干净 SST 重建。最终 CTest **55/55** 通过；新工具 3 类测试通过。覆盖不同分块与独立小规模完整排序 oracle 的数值交叉核对、同分与 K 边界、K≥k、空/不足 k、两种指标不同、未知 schema、错 base/query/mapping/metric/preprocessing、非有限/负/乱序距离、重复/越界/截断/额外 ID、图超度/非法边/坏入口/不可达、重复邻接和自环保留；错误输入均拒绝。

真实图输入为锁定 SIFT 完整源扫描后选取 10,000 个 base 原始 ID `[1,0,2,...,9999]`，规范化 ID 因而显式区别于原始 ID；32 条 query 为预留诊断原始行 0..31，不使用正式调参/最终评价样本。R/GK=64、L=200、C=500、NNDescent build_type=1、单线程及上游默认 RNG。最终导出入口 3732，186,963 条边，节点/入口可达均 10,000/10,000，无非法 ID、超度；本次小图重复/自环均为 0（夹具另外证明存在时保留）。全部度数直方图与哈希保存于 index.json。

独立精确 top-10 与 Faiss 原生 search_L=256 的诊断 Recall@10 为 **1.0**，32 条 1-recall@10 均为 1；这只证明此小样本原生搜索质量，不代表项目搜索质量。另转换完整 1M SIFT base，query 和官方真值均按原始行 0..31 选取；ID-only K=100 校验和 k=10 使用检查通过，无虚构距离、并列边界 unresolved；拿该真值绑定 10k base 子集被拒绝。

最终运行的单阶段实测（wait4 子进程峰值 RSS；macOS bytes 转 MiB，不是整进程树同时驻留总和）：

| 阶段 | 墙钟 s | 峰值 RSS MiB |
| --- | ---: | ---: |
| 小子集导入（扫描完整源） | 1.891 | 102.5 |
| 建图包装器（含校验/导出） | 5.539 | 69.4 |
| 其中原生 builder | 5.246 | 69.4 |
| 小子集独立精确真值 | 1.674 | 31.7 |
| 图复用检查 | 0.206 | 28.7 |
| 精确真值复用检查 | 0.106 | 26.0 |
| 完整 base 与官方真值数据导入 | 6.273 | 102.3 |
| 官方真值绑定与复核 | 10.137 | 51.4 |
| 官方 K≥k 复核 | 3.446 | 38.6 |

各命令、有效输入/输出路径、退出码、超时、RSS 预算与原始日志在对应 JSON 中；建图包装器和原生进程分开记录，不求和作为 RSS。`real-final/` 产物大小快照约 524 MB，不是磁盘峰值监控。

限制：只完成 AC-02-02/03/04 的 T2 准备部分；未宣称整个 M1、正式 V0.2 AC 或全规模支持。未执行完整 V0.1 AC、原生 MQSim oracle、ASan、Deep10M 正式导入/真值/建图、项目搜索质量或两放置查询矩阵；此次不改 MQSim/模拟服务边界。纯 Python 精确计算虽然有界内存，未证明全规模预算；BFS 队列仍 O(N)，RSS 越限为完成后判断，不能防止 OOM。完整 Deep 建图存储量下界超过冻结本机上限，预检拒绝；资源门槛仍保留。硬件参数仍为 `configured_unvalidated`，无绝对性能或盘控更快结论。

README、文档索引、接口/测试/限制和实施计划任务清单已同步。下一任务是 T3，本轮不自动开始。
