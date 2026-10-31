# SIFT1M 盘控／Host 算力参数扫描

2026-10-31（Asia/Shanghai）。这是 T6 后的单因素敏感性实验，不修改 T6 原矩阵或模型，不启动 T7/T8。结果为本机实际运行，产物在忽略目录 `results/v0.2-t6-compute-ratio/`。

## 参数与复跑

固定完整 SIFT1M base、T6 NSG 图、独立评价集128条、L/E=128/256、k10/B1/chunk16、并发1、ann-2g-v1、mmap。固定 Host distance_rate=4 FLOPs/ns，盘控 flops_per_ns 扫描1/2/4/8/16，即 rate 比0.25/0.5/1/2/4。其余参数继承 T6，包括 Host setup3ns、盘控 launch1ns、16KiB缓冲与16B/ns端口。每次完整预装载并排空；重跑 Host 基线，不复用旧仿真结果。

**比值只指 rate 参数**：Host rate 含加载，盘控缓冲访问独立计费；rate比1不是统一口径的硬件等算力。没有修改计费语义，也没有校准真实CPU/盘控性能。

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/scan_compute_ratio.py \
  --prepared results/v0.2-t6/preparation/prepared \
  --build build/delivery --output results/v0.2-t6-compute-ratio
```

需要已有构建与经验证的T6完整准备产物，输出目录须不存在。入口校验完整数据/索引/真值、查询列表、准备文件哈希；顺序运行6次，保留配置、命令退出码、模型及输入身份、原始结果、资源统计和断言。不是重新建图或完整AC入口。

## 实测结果

均为平均接纳后模拟时延；QPS为批量窗口吞吐，非宿主墙钟。

| 放置 | 盘控／Host rate比 | 平均时延 ms | QPS | 相对Host吞吐倍数 |
| --- | ---: | ---: | ---: | ---: |
| Host | — | 525.392838 | 1.903338 | 1.000000 |
| controller | 0.25 | 428.479094 | 2.333836 | 1.226181 |
| controller | 0.5 | 428.103470 | 2.335884 | 1.227257 |
| controller | 1 | 427.915680 | 2.336909 | 1.227795 |
| controller | 2 | 427.821788 | 2.337422 | 1.228065 |
| controller | 4 | 427.774858 | 2.337678 | 1.228199 |

全部Recall@10=0.9984375，top-k/距离/扩展数/决策摘要与Host一致。六次命令退出0，90项检查全部通过，覆盖实际完整初始化、结果、credit、回收、缓冲和墙钟/RSS预算。额外交叉核对Host和4倍盘控的全部模拟统计与T6原点完全一致（只排除墙钟）。五档盘控wire总量均为27,625,256B，Host为357,288,720B。

盘控rate从1提升到16 FLOPs/ns，端到端时延仅减少0.704236ms，低速档比高速档高约0.1646%；扫描范围内未发现性能交叉点，不外推更低算力的临界值。当前正整数rate接口最低为1 FLOP/ns。

独立流式汇总`service.csv`与`controller_service.csv`：128条查询Host距离服务累计0.038354715s，Host控制服务累计35.229328265s；盘控算术服务累计从0.153056201s降至0.009596921s。距离算术在当前模型中占比小，可以解释本次对rate变化不敏感；这些是累计服务量，不是可直接相加的关键路径分解。结果不支持将原有约22.8%吞吐收益主要归因于盘控4倍算术rate，但仍不能把全部收益唯一归因于PCIe。

## 验证与限制

原始证据：`plan.json`、各运行`manifest.json`和receipt、`scan.json/csv`、`assertions.json`、`status.json`。额外核对记录在`cross-checks.json`，流式服务汇总在`service-totals-ns.json`；二者为运行完成后的独立核对产物。入口与最终`git diff --check`退出0。本轮未重新编译或跑CTest/完整AC，因为仅新增实验驱动，没有修改模拟模型、元素库或依赖。

只覆盖一个L/E点、并发1和固定128查询；没有额外重复、详细事件重放或新硬件校准。沿用T6官方ID-only真值的并列边界限制；不能代表全10k查询或稳定p99。资源检查沿用wait4/RUSAGE_SELF高水位口径，不是分阶段或同时整树RSS硬隔离。本次未测连续磁盘峰值。当前结果是固定其他参数后的rate敏感性，不是公平硬件优劣的完整结论。

## 控制rate=4、距离rate相等的补充实验

2026-10-31按用户要求实际重跑两放置，产物`results/v0.2-t6-control-rate4/`。使用相同完整base/图/评价128条、L/E=128/256、并发1；两放置Host control_rate均改为4工作单位/ns，Host distance_rate与controller flops_per_ns均为4。检查/比较权重均为1，启动开销、缓冲、存储和传输配置不变。不把抽象工作单位当成CPU指令或周期，不宣称4单位/ns已校准。

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/scan_compute_ratio.py \
  --prepared results/v0.2-t6/preparation/prepared --build build/delivery \
  --control-rate 4 --controller-rates 4 --output results/v0.2-t6-control-rate4
```

| control_rate | Host平均模拟时延 ms / QPS | controller平均模拟时延 ms / QPS |
| --- | ---: | ---: |
| 1（上一轮距离rate同为4的点） | 525.392838 / 1.903338 | 427.915680 / 2.336909 |
| 4（本轮） | 318.971394 / 3.135077 | 221.494235 / 4.514789 |

本轮盘控相对Host吞吐1.440089倍，时延降低30.56%。两种放置相对各自control_rate=1的点，时延分别降低39.29%和48.24%。每查询平均控制服务由275.229ms降至68.808ms，距离服务工作量不变；不能将总时延按control_rate同比缩放。

两次命令均退出0，30项逐运行断言全部通过；另4项核对确认实际配置rate正确，新旧参数间完整top-k/距离/扩展数/决策摘要不变。两放置Recall@10仍为0.9984375。完整初始化/排空、缓冲、credit、终态回收及RSS/墙钟预算均通过。证据在新目录的`status.json`、`assertions.json`、`cross-checks.json`、`service-totals-ns.json`和各运行manifest/receipt。新增CLI拒绝零控制rate、负盘控rate和重复扫描点的检查全部符合预期，最终`git diff --check`退出0；未重编译或重跑完整AC。

新增入口选项保持默认control_rate=1和五档扫描不变；首次扫描的脚本SHA为历史运行版本，本轮SHA记录在新plan/status中。未修改T6原正式配置和原产物。本轮为未校准参数情景，不替代原矩阵或证明目标硬件性能；Host加载与盘控独立缓冲的计费差异继续保留。
