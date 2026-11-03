# V03-0 direct 基线冻结

2026-11-03（Asia/Shanghai）。本任务只关闭 direct 参考证据与预算冻结，不实现 IR，不启动 V03-1。当前运行代码仍为 `GraphSearch` 回调状态机及 `GraphHost` 的 host 普通读取 / controller Open、Score、Close 接线；配置尚无 engine 选择键，冻结 manifest 显式记录有效 `direct`，不伪造 IR manifest。

## 依赖与身份

开工读取 AGENTS、README、V0.3 PRD/IR/实施清单、文档索引、V0.2 交付记录、SIFT1M、测试、契约及限制。当前 HEAD 为 `88a1d81f87da4504890add3139bea09b61cef82c`；相对清单原快照 `1d5e0404db9ccf543c68febf6d6c6c99d97b51de`，`src/`、根 CMake、原 `tests/` 和运行器未变。MQSim `8f7ddf6d8cafdb7c783b3fcfd4693f15c6c5854b`、SST Core `ffd71527bc26a1f2a4fb685fdeb2ca0799997d78` 均未修改。原生 MQSim 身份仍为锁文件中的 `51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16`，不是本次 fork 身份。

本轮对 `.cache/v0.2-m0/data/sift` 原始四文件重新核对大小/SHA256；对 `results/v0.2-t6/preparation/prepared` 全部准备文件重算 hash，并重新执行数据、NSG 图、真值及固定查询绑定检查；建图器可执行文件、适配器与 lock 身份通过。严格复用准备数据，没有重新抽查询或建图。历史通过标记不参与本次成功判定。

主证据位于本机忽略目录 `results/v0.3/direct-baseline-final/`：

- `manifest.json`、`assertions.json`：任务范围、有效 engine、命令、退出码、全部冻结产物 SHA256 和本轮断言。
- `source-{start,end}.json`、`workspace.patch`、`untracked-source/`：完整主仓文件清单/hash、未提交 diff、MQSim 相对原生 diff hash/实际源码 hash；运行前后执行源码和依赖一致。运行后仅新增本收口文档和更新入口/任务状态。
- `sst-source.json`、`build-identity.json`、`CMakeCache.txt`、`ndpsim-flags.make`、`sst-paths.json`、`sst-receipt.json`：SST 实际逐文件源码、元素与 SST 可执行文件、真实构建参数和缓存凭据。`capture-build.py` 及其 command receipt 可重新采集这些补充身份；该采集不改变仿真。
- `inputs.json`：原始、准备、builder、数据/图/真值及查询身份、矩阵和原预算锁 hash；各运行另存有效配置和输入 manifest。
- `small-*`、`evaluation-*`、`detail-*`：新运行的配置、真实 SST 结果/Recall、全部模拟统计/CSV、初态、资源、墙钟/RSS及 command receipt/log；两个 detail 目录含详细事件/决策/传输账本。
- `small-comparison-report.json`、`detail-comparison-report.json`：现有独立核对器重新计算搜索批次、真实距离、访问和普通/本地/PCIe/credit 账本。
- `budget-decision.json`、`physical-ram.txt`、`disk-measurement.json`：完整 base 运行前冻结的预算、16 GiB RAM 实测输出、磁盘采样。

证据不随 Git 分发。未来共享重构后，direct 与 ir 必须分别对照这份独立锚点；同 placement 的结果、时间、工作量及账本遵守 PRD §7.1，不用两条路径互相比对替代冻结证据。比较以原始 query / iteration / semantic batch / 角色 / 块关联，不能宽泛排除资源、时间或所有任务字段。版本化 IR 比较器仍属于 V03-5。

## 复现与检查

锁定 Python 为 `/opt/homebrew/Caskroom/miniconda/base/bin/python3.13`。以下输出目录必须不存在；完整命令、参数、stdout/stderr、退出码以证据 receipt/log 为准：

```sh
# 正常环境会直接读取 sysctl；受限沙箱可先单独采集实测 stdout。
/usr/sbin/sysctl -n hw.memsize > /private/tmp/v03-ram.txt
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/freeze_direct_baseline.py \
  --prepared results/v0.2-t6/preparation/prepared \
  --source-dir .cache/v0.2-m0/data/sift \
  --ram-evidence /private/tmp/v03-ram.txt \
  --build build/delivery --output results/v0.3/direct-baseline-final
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tests/test_direct_baseline.py
```

入口依次执行根 CMake 配置/构建、全量当前 CTest、小规模两放置详细运行与独立核对；冻结预算后，新运行完整 1M base 的 evaluation128（L128/E256、c1、statistics）和 detail8（同参数、detail），共四个 SIFT 运行。随后核对评价两放置精确结果/决策摘要，详细样本用 `compare_placements.py` 独立核对。全体输入 hash、源码/依赖身份前后复查；任一步失败都保留失败 manifest 并非零退出。入口不接收运行结果复用选项。

本轮实际结果和测量见下表。

| 本轮检查 / 测量 | 结果 |
| --- | --- |
| 根 CMake configure / build | 退出码均为0，复用锁定SST缓存 |
| CTest | 67/67通过，退出0 |
| 新 direct 运行 | 小规模两放置 + 完整base evaluation128两放置 + detail8两放置，共6次成功 |
| 独立详细核对 | 小规模及SIFT样本均通过；SIFT为8查询、1,066批，退出0 |
| 评价128查询 | 两放置 top-k/顺序/FP64距离/扩展与决策摘要一致；Recall@10均为0.9984375 |
| SIFT详细8查询 | 两放置Recall@10均为1.0，不作为全查询质量结论 |
| evaluation-host | 初始化 4.868s / 查询 44.099s / 后处理 18.510s；SST peak 825,720,832B；Python peak 79,216,640B |
| evaluation-controller | 初始化 4.833s / 查询 21.435s / 后处理 25.355s；SST peak 852,180,992B；Python peak 78,643,200B |
| detail-host | 初始化 4.839s / 查询 3.519s / 后处理 14.716s；SST peak 825,622,528B；Python peak 76,496,896B |
| detail-controller | 初始化 4.848s / 查询 1.701s / 后处理 15.313s；SST peak 827,817,984B；Python peak 72,925,184B |
| 磁盘 / RAM | 16GiB RAM；采样峰值3,514,770,629B <25GiB，最小卷余量93,319,942,144B >20GiB |
| 身份、预算、产物与差异检查 | 12项本轮断言全true；源码/依赖运行前后一致，预算hash不变；git diff --check退出0 |

冻结 manifest SHA256：`d7f78b1076bc003043866174442d9cf2ef91452ce994a93c68c322033162dc11`。

本轮 `tests/test_direct_baseline.py` 3/3 通过，覆盖通过证据、缺失/篡改/失败/空清单、路径越界和 RAM/小规模 RSS 门槛拒绝；不属于原 67 项 CTest。补充采集第一次因不存在 `compile_commands.json` 失败，随后改为采集实际生成的 `flags.make` 成功，失败日志保留。首次冻结 `results/v0.3/direct-baseline/` 因沙箱禁止 `sysctl` 而失败，未启动完整 base 运行；独立读取得到 `17179869184` bytes 后，以新目录重跑成功。这些失败没有被标记为通过。

## 冻结预算与限制

小规模 N2048/d32/Q32、L32/E64、chunk16、c1 的两 placement 详细测量在预算决策中保留全部样本；其结果仅用于确认机器有余量，不能预测 IR 全规模开销。采用 PRD §9 默认：

- 完整 evaluation128、L128/E256、c1、statistics，每 placement direct/ir 交替至少三组，以中位查询墙钟计算 ir/direct ≤1.5，保留所有样本。本任务的一组 direct 样本不代替三组补测。
- ir 相对 direct SST peak RSS 增量 ≤max(128 MiB,20%×direct peak)，同时每进程满足 4 GiB 和 60% RAM 上限。
- 固定小 base/参数/并发，Q32/128/512 各至少三次；Q32→Q512 SST peak 增量 ≤max(64 MiB,10%×Q32 peak)，必要查询/结果存储单列，终态非必要活动历史必须回收。
- 原始 SIFT、共享准备输入和 `results/v0.3/` 全部双路径证据（含失败运行）合计 ≤25 GiB，卷余量 ≥20 GiB；不计历史 V0.2 运行、构建和其他缓存。所需原始 SIFT 缓存已计入。
- 每次 required+detail 输出预算 2 GiB；未来 IR 诊断共享此预算，超限明确失败，不能静默丢日志。初始化 ≤3600s、查询 ≤7200s、后处理 ≤600s；SST ≤4GiB、Python 父进程 ≤512MiB。

以上在完整 base 运行之前写入 `budget-decision.json`，结束时 hash 不变。尚无 IR 开销测量或 IR 验收通过结论；V03-6 进入前仍须重新检查磁盘/RAM可用性。磁盘为每秒逻辑大小采样，RSS 为 wait4 子进程及父进程生命周期高水位，均非硬隔离，分阶段/私有/同时进程树 RSS 仍 Unsupported。硬件仍 `configured_unvalidated`，官方有限 top100 的严格 FP64/ID 并列边界仍 unresolved；固定128/8查询不是全10k benchmark。复用现有锁定 SST 缓存，不是干净 SST 重建。本轮未运行完整 V0.1 AC、原生 oracle、ASan、26点/52点矩阵或 IR 相关验收，不能宣称 V0.3 交付通过。
