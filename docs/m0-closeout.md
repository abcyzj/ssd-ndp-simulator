# V0.2 T0 / M0 实证与接续

日期：2026-10-30（Asia/Shanghai）。本页只记录 T0 的准备与可行性检查；不推进 T1～T8，也不替代 V0.2 AC 或完整 V0.1 AC。源码与输入身份、实际命令、退出码、断言见本机 `results/v0.2-m0/`。该目录及 `.cache/v0.2-m0/` 被 Git 忽略，新检出须重新获取原始数据和运行探针。

## 工具与复跑

`configs/benchmarks/m0-builder.lock.json` 固定 Faiss 1.12.0 完整 commit、源码归档 SHA256，LLVM OpenMP 20.1.8 与配套 LLVM CMake 模块归档 SHA256、许可证和 Apple 系统工具链身份。Faiss 仅用于离线探针，不链接正式 SST 查询算子。CPU/generic、GPU/Python/C API 关闭，Apple Accelerate 提供 BLAS/LAPACK，不需要 NumPy 或 Faiss Python 包。OpenMP 只安装到仓库缓存，不安装系统软件、不修改第三方子模块。

将锁文件列出的三个归档按原名放入下载目录，再用锁定 Python 执行（目录名须未使用）：

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/m0/build_builder.py \
  --archives .cache/v0.2-m0/downloads --work .cache/m0-rebuild --output results/m0-rebuild
cmake -S . -B build/delivery -DBUILD_TESTING=ON
cmake --build build/delivery -j2
ctest --test-dir build/delivery --output-on-failure
```

`build_builder.py` 在解包前校验锁定归档，使用安全 tar 解包；SDK 身份不同直接拒绝，保留输出供明确评审后重新锁定。首次手工构建中的缺少 LLVM CMake 模块、缺少 OpenMP C 参数和 CMake imported target 作用域错误均有失败日志；可复跑入口包含这些修正。锁的是外部源码包与当前系统工具链，不提供 macOS/SDK 的可分发二进制镜像，也不保证不同构建目录的可执行文件逐位相同。

完整原始文件检查示例（只检查和锁定，不转换数据）：

```sh
python3.13 tools/verify_m0.py inspect .cache/v0.2-m0/data/sift/sift_base.fvecs \
  --format fvecs --count 1000000 --dimensions 128 --output results/m0-sift-base.json
python3.13 tools/verify_m0.py inventory --output results/m0-inventory.json
```

检查覆盖整个文件的长度、各行或全局头、little endian、有限 FP32、真值 ID 范围，并同时增量计算 SHA256；没有仅靠首部或 URL 认可文件。T1 仍须实现正式 schema、转换、映射和错误输入集成测试。

初始化探针用根 CMake 构建后的 SST 元素库；只有探针局部 XML 修改 blocks/plane，普通 `native-small` 及正式图运行器不增加新 profile。2×1×2×1、64 pages/block、4 KiB/page、7% OP，其余参数沿用锁定 XML。仍为 SST SelfLink / EventBridge 驱动 MQSim，逐页普通写、逐次排空；启用现行 Observation 保留初始化历史，完成时验证写入地址/次数、实际 namespace、无在途请求和后端事件，保留 CMT，不重置模拟时间。

```sh
python3.13 tools/m0/run_probes.py init --output results/m0-init-2g
python3.13 tools/m0/run_probes.py init --blocks 16384 --output results/m0-init-16g
python3.13 tools/m0/run_probes.py init --sizes 1024000000 --output results/m0-init-sift
python3.13 tools/m0/run_probes.py init --blocks 16384 --sizes 10240000000 --output results/m0-init-deep
python3.13 tools/m0/run_probes.py builder \
  --base .cache/v0.2-m0/data/sift/sift_base.fvecs --query .cache/v0.2-m0/data/sift/sift_query.fvecs \
  --format fvecs --dimensions 128 --output results/m0-builder-sift
```

`--builder` 可指定重建入口返回的可执行文件；Deep 使用 `--format fbin --dimensions 96` 及对应的完整锁定 base/query 文件。默认建图 N=10k/100k/1M、线程数 4、各三次；另以 `--threads 1 --sizes 10000 --repetitions 1` 验证小图导出接口。R=64、GK=64、NSG L=200/C=500，强制 `build_type=1` 使用 NNDescent，不用隐式暴力 kNN。保留上游 RNG 默认：NSG 0x0903、局部 init_graph 0x1234、内部 NNDescent 2021；IndexNSG.add 没有外设内部 NNDescent seed 的参数，不声称 seed=42 建图。

图输出为原 ID、原有效邻接顺序的 degree+64 槽 little-endian uint32，每节点验证边界、负值 sentinel、degree、覆盖和入口有向可达率 100%，报告边数、min/max degree、重复边及自环。真实图确有重复邻接；首轮探针把重复边当错误，100k 退出 1。修正为计划要求的“报告重复”，不去重、不截断、不改上游图；非法 ID/超度/不可达仍失败。32 条诊断 query 为原查询前 32 行，匹配当前前缀 base，独立逐维 FP64 全枚举真值核对建图器 search_L=256 的 Recall@10≥0.95。正式查询矩阵的显式 ID 列表单独冻结，不把此诊断样本当正式评估。

资源记录使用独立子进程的 `wait4.ru_maxrss`，macOS bytes（Linux KiB 转 bytes）；不是 Python 父进程、不是累计 RUSAGE_CHILDREN，也不是整进程树同时驻留之和。墙钟包含进程启动、加载、建图、导出、图检查、独立真值及诊断搜索，是建图阶段成本上界，不等同于纯 index.add 时间。超时杀进程组，保留失败日志；RSS 预算为完成后的检查，不是硬内存限额。正式大规模任务仍需 T4/T5 的容量、内容和生命周期改造及资源预检。

## 资源与范围门槛

提权只读 `sysctl -n hw.memsize` 退出 0，实测 17,179,869,184 bytes，即 16 GiB；沙箱内同命令被系统拒绝，inventory 如实保存失败，另以硬件证据记录实际值。每进程有效上限为阶段预算与物理 RAM 60% 的较小者，本机约 9.6 GiB。完整阶段预算、至少 20 GiB 卷余量和查询矩阵冻结于 `configs/benchmarks/v0.2-budgets.json`；显式查询 ID 见 `v0.2-query-selection.json`。

Deep 的 32 GiB 建图预算不能在本机使用；不因初始化探针通过或 1M 样本能运行而关闭完整 10M 建图资源门槛。T7 仍需足够资源或经过评审的有界内存方案；不自动减小 base 或换随机图。初始化样本不加载规范化内容文件、不运行查询，因此不代表完整 ANN 进程 RSS，不替代 T6/T7 的实际输入、profile、状态与查询联合验证。此次不需要启用快速初始化。

公开数据下载不等同于项目 MIT 许可证适用于数据。数据锁记录实际观察到的来源、引用要求及未找到独立 dataset license 的事实；不附带或推断数据再分发授权。原始数据和构建依赖保持在忽略缓存，不纳入本次源码变更。

## 实际检查结果

**T0/M0 已收口，T1～T8 未启动。** `tools/m0/summarize.py --evidence results/v0.2-m0` 退出 0，64 项证据核对通过；核对原始文件与内容锁、建图重建锁、各矩阵完整性、日志/产物哈希、导出 degree 直方图、初始化断言、RAM 和磁盘保留空间。`assertions.json`、`ac-matrix.json`、`manifest.json` 分别保存断言、前置 AC 映射及最终源码/证据身份。没有将未完成的正式 AC 标通过。

| 本轮实际命令 / 检查 | 结果 |
| --- | --- |
| 根 `cmake -S . -B build/delivery -DBUILD_TESTING=ON`、`cmake --build build/delivery -j2`、`ctest --test-dir build/delivery --output-on-failure` | 均退出 0；最终 CTest 53/53，日志 `cmake-*-last.log`、`ctest-last.log`。SST 命中现有锁定缓存，不声称干净重建 |
| `python3.13 tests/test_m0.py` | 退出 0，五类测试：完整源文件/坏长度和头部、非有限值/非法 ID、子进程失败/超时/RSS 越限、锁定归档损坏时解包前拒绝、调参/评价/诊断 ID 隔离与锁绑定 |
| `cmake -S . -B build/m0-external-smoke -DBUILD_TESTING=ON -DSST_ROOT=<既有缓存安装>` 后 `ctest -R m0_evidence_tools` | 均退出 0，1/1。修复新增 M0 测试注册早于 Python 发现时，在显式 SST_ROOT 路径中可能缺少解释器的问题 |
| `m0/build_builder.py` 的五条配置/编译/本地安装命令 | 全部退出 0；来自已校验归档的新工作目录，逐命令墙钟/RSS/日志在 `builder-rebuild/`；重建产物实际运行了 Deep 单线程及梯度探针 |
| 六个完整原始文件检查 | SIFT base/query/learn/groundtruth、Deep base/query 均通过。SIFT base 完整校验约 1.86 s / 89.4 MiB，Deep base 约 20.50 s / 138.3 MiB。它们是原始文件检查成本，不是 T1 转换成本 |
| 建图器单线程 10k 小图，两个数据集各一次；四线程 10k/100k/1M，各三次 | 共 20 次成功。全部节点覆盖和入口可达率 100%，无非法 ID/超度；32 条诊断 query 的独立 FP64 真值 Recall@10 均为 1.0；不推广到正式评价样本 |
| SST 初始化：2/16 GiB 探针几何，各 64 MiB/256 MiB/1 GiB 三次；两个目标布局各三次 | 共 24 次成功。实际 namespace 分别为 3,900,701 / 31,205,621 sectors，写入精确覆盖布局后排空；不生成同尺寸宿主内容文件，不含查询 |
| 非页对齐 512 B 布局负例 | SST 退出 255，明确 `invalid M0 probe geometry/layout`，在创建设备前拒绝；这是预期失败，不作为成功模拟 |
| Python/JSON 语法、Markdown 本地链接、`git diff --check`、源码与子模块核对 | 通过；用户已有暂存修改保留，MQSim fork/SST 未改，不提交、不推送 |

每组三次实测范围如下，RSS 为组内最大值，GiB=2³⁰。部分建图样本与其他 M0 工作重叠，墙钟不作为空闲机器的隔离性能测量。

| 数据 / 样本 N | 墙钟范围 s | 最高 RSS MiB |
| --- | ---: | ---: |
| SIFT d128 / 10k | 1.66–2.28 | 70.9 |
| SIFT d128 / 100k | 20.71–22.20 | 566.1 |
| SIFT d128 / 1M | 367.49–505.06 | 3210.0 |
| Deep d96 / 10k | 3.32–3.92 | 69.0 |
| Deep d96 / 100k | 37.21–38.11 | 540.4 |
| Deep d96 / 1M（前缀诊断） | 280.27–495.19 | 3888.5 |

目标布局 SIFT 1,024,000,000 B：250,000 次写，进程墙钟 1.03–1.13 s、最高 236.7 MiB；Deep 10,240,000,000 B：2,500,000 次写，9.22–10.40 s、最高 1409.0 MiB。初始化梯度 64/256/1024 MiB 的最高 RSS：2 GiB 几何为 59.1/95.4/240.4 MiB，16 GiB 几何为 173.3/209.7/354.5 MiB。

百万 SIFT 三次图的重复边最多 40,559、自环最多 14,473；百万 Deep 前缀最多 51,125/299。导出保留这些邻接，完整直方图、图哈希及每次图检查在 `assertions.json` 和 `faiss-*/`，不要求多线程重复建图逐位相同。T2 必须记录真实图身份及明确的邻接策略，正式项目搜索质量仍待验证。

完整 V0.1 AC、ASan 和独立原生 oracle 本轮未运行；没有修改 MQSim、SST 或既有后端路径。磁盘仅有可用空间和产物大小快照，未连续监控峰值；RSS 越限是完成后判定，不能防止 OOM。正式查询能力仍为 V0.1 基线，硬件参数仍是 `configured_unvalidated`，完整 Deep10M 建图与正式矩阵尚无通过结论。下一任务为 T1，本轮不自动开始。
