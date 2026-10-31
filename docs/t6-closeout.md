# T6 / M2 SIFT1M 实现与规模验证

2026-10-31（Asia/Shanghai）。已读取根 AGENTS、README、V0.2 PRD/实施计划、相关契约/数据/图/观测/测试说明，并核对 T2/T4/T5 收口和本机依赖产物。开工工作区干净；MQSim/SST 子模块未改，未自动提交、推送、升级或初始化 Git。任务范围止于 T6，T7/T8 未启动。

当前状态：**T6/M2 已完成**。完整1M真实图、26次正式放置运行、473条T6断言、预算、重复性、并发和独立详细核对通过；M2的SIFT1M出口关闭。T7/T8未开始。

实现与复跑接口见 [SIFT1M 入口](t6-sift1m.md)。新增固定矩阵配置与独立准备/复用/扫描入口；保留原搜索语义、SST/MQSim 时间与唯一计费边界。完整图由锁定 Faiss 重新构建，原生图诊断只使用调参集，不向 SST 提供访问 trace。逐批核对器按需读取向量/邻接行，扫描汇总流式读取资源 CSV 和统计哈希，避免完整 base 或资源数组展开为 Python 对象。初始化记录实际完成 sectors、后端排空和 origin；另采集 Python 父进程自身 RSS，区分 SST 与后处理预算。

## 输入和预注册矩阵

完整 SIFT base=1,000,000×128 FP32，原始 ID 顺序0..999999；使用 M0 内容锁和查询显式列表。图为 NSG R/GK64、L200/C500、NNDescent、四线程，项目搜索仍为 `ndpsim-ranked-expansion-v1`。图入口123742，29,977,545条边，节点覆盖/入口有向可达均1,000,000/1,000,000；40,366条重复边、14,473条自环保留并报告，无非法ID/超度。原生 search_L256 在 tuning32 上的 Recall@10=1.0，不等同于项目搜索结果。

官方 ID-only top-100 真值按 query 原始行号导入并绑定完整 base/query/mapping。tuning32 与 evaluation128 不相交，另从评价集固定前32条和前8条用于并发与详细验证。两放置共享同一图、查询和非放置配置；k10/B1/chunk16，受控 ann-2g-v1、mmap、16KiB盘控缓冲。

矩阵26次：两组查询各四点两放置16次；128/256主配置评价重复2次；固定32条并发2/4两放置4次；固定8条statistics/detail两放置4次。全部扫描点保留。质量匹配目标0.80/0.90/0.95只依据调参集选参数，评价集只报告对应固定参数结果。

## 实际检查与命令

```sh
cmake -S . -B build/delivery -DBUILD_TESTING=ON
cmake --build build/delivery -j2
ctest --test-dir build/delivery --output-on-failure
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tests/test_t6_matrix.py
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/verify_t6.py \
  --source-dir .cache/v0.2-m0/data/sift \
  --receipt results/v0.2-t2/builder/index-builder-receipt.json \
  --prepare-only --output results/v0.2-t6/preparation
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/verify_t6.py \
  --prepared results/v0.2-t6/preparation/prepared \
  --source-dir .cache/v0.2-m0/data/sift \
  --build build/delivery --output results/v0.2-t6/matrix
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/verify_acceptance.py \
  --build build/delivery --output results/v0.2-t6/v01-bounded
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/verify_t6.py \
  --prepared results/v0.2-t6/preparation/prepared \
  --source-dir .cache/v0.2-m0/data/sift \
  --reuse-runs results/v0.2-t6/matrix --build build/delivery \
  --output results/v0.2-t6/final-evidence
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 results/v0.2-t6/checks/summarize.py
git diff --check
```

根配置/构建通过，复用锁定 SST 缓存，非干净 SST 重建。CTest **67/67**；新增矩阵、统计、按需读取、复用和链路审计六项 Python 测试通过，包括固定26点与查询分割、拒绝改变冻结k、统计比较只排除墙钟且保留资源差异、百万行稀疏夹具按需读取且Python堆低于2MiB、截断/索引边界；缓存配置/输入/模型/输出/元素篡改拒绝；3万行日志流式读取和四类链路错误拒绝。完整 V0.1 **AC-01～11** 在最终 `v01-bounded/` 退出0，包含锁定原生 MQSim QD1/QD32/小CMT两次对照、快/慢功能两放置和既有 **17/17 ASan**；不继承 T5 的历史通过，也不宣称整个 V0.2/T8 交付完成。

准备阶段9条测量命令均退出0。原生建图327.839s、RSS3,363,717,120B（3.133GiB）；含完整校验/导出的包装阶段347.26s，均在冻结预算内。四份规范化完整base与小query/真值显式计入磁盘，不用缩小base或M0探针替代正式数据。准备采样峰值2,905,175,941B，卷余量至少96,392,433,664B；每秒逻辑文件大小采样，可能遗漏亚秒峰值。

证据为本机忽略产物 `results/v0.2-t6/`：`preparation/prepared/`保存规范化数据、真实图、官方真值、builder质量和每条准备命令；`matrix/`保存26次成功的独立放置产物及初轮失败汇总；`final-evidence/`保存逐项验证复用的引用、配置、初态、扫描CSV/JSON、质量匹配、可读report、独立comparison、通过的manifest/assertions/AC映射；`v01-bounded/`保存完整回归；`checks/`保存配置/构建/CTest日志及最终身份核查。新检出不附带数据与结果，须按接口重建或复用经校验的产物。

## 完整规模结果与资源

以下为独立评价集128条、并发1；两放置top-k/距离/决策摘要一致，质量仅相对官方ID真值。模拟时延为平均接纳后时延，QPS是整批测量窗口吞吐，不能与宿主墙钟混用。

| L/E | 两放置 Recall@10 | host平均时延 ms / QPS | controller平均时延 ms / QPS |
| --- | ---: | ---: | ---: |
| 32/64 | 0.94843750 | 99.018 / 10.099139 | 64.215 / 15.572786 |
| 64/128 | 0.98984375 | 197.198 / 5.071038 | 139.468 / 7.170121 |
| 128/256 | 0.99843750 | 525.393 / 1.903338 | 427.775 / 2.337678 |
| 256/512 | 0.99921875 | 2015.643 / 0.496120 | 1849.028 / 0.540825 |

调参集四点两放置Recall@10为0.953125、0.98125、0.996875、1.0。0.80/0.90/0.95目标在调参集均选择32/64；独立评价为0.9484375，所以0.95目标明确标为未达到，不依据评价集重新选点。原生建图器>=0.95及项目最高预算>=0.90门槛均满足，未降低门槛。

主配置评价集两次运行的完整结果和全部模拟统计一致（仅排除墙钟）；并发2/4固定32条与并发1同原始查询的top-k和决策摘要一致，实际峰值并发分别2/4。盘控缓冲峰值分别16,192/16,224B，均低于16,384B，存在真实credit等待且最终归零。统计/详细模式两放置结果和全部模拟统计一致；8条详细查询独立重放1,066批，距离/候选/扩展、普通/本地读取因果、完整PCIe/NVMe消息账本通过，盘控向量D2H=0。

| 测量 | 26次范围 | 冻结上限 |
| --- | ---: | ---: |
| 全布局初始化墙钟 | 4.772–5.363 s | 1 h |
| 查询墙钟 | 0.745–119.632 s | 2 h / 点 |
| 后处理墙钟 | 14.964–35.351 s | 10 min / 点 |
| SST peak RSS | 786.188–830.703 MiB | 4 GiB |
| Python父进程 peak RSS | 66.328–82.016 MiB | 512 MiB |

修复后的独立详细核对48.80s / 254,967,808B（243.156MiB），扫描器自身80,887,808B，均低于512MiB。初始化实际250,000页/2,000,000 sectors，namespace为3,900,701 sectors；26次实际排空、origin、offered/接纳/结果/drain边界和终态回收均通过。

最终采样文件峰值3,991,120,254B（输入+准备+原始运行+最终汇总），低于15GiB；卷余量最少94,113,472,512B，高于20GiB。非连续磁盘峰值/分阶段私有RSS限制见下。最终50组、473条T6断言全通过；另重新核对13对查询结果/距离/摘要及26份实际初态、源码/元素/产物哈希，`checks/verification-summary.json`通过。

## 初轮失败与恢复

初轮26次独立放置全部退出0，预算与输出完整；汇总父入口退出1：旧独立核对器功能检查退出0，但RSS1,118,060,544B超过512MiB预算，因此没有把初轮汇总标为通过。另静态发现comparison报告与测量receipt同名，改为独立`detail-comparison-report.json`，防止报告被receipt覆盖。失败目录和日志保留。

流式修复不改变搜索、模拟服务或费用。既有大日志上新旧核对报告完全一致，RSS由743,260,160B降至142,180,352B；正式1M详细子集新核对为254,967,808B。保留逐批、服务可见性、native费用等断言；链路逐项验证enqueue/start/arrive后仅聚合计数和必要platform关联，保留完成ID防复用，普通读因果另流式重读。

`--reuse-runs`只复用已完成的独立放置产物，不接受失败汇总为成功缓存。逐项重新校验完整准备身份、有效配置、元素/模型源码、MQSim实际源码和SST提交、所有输出哈希及原命令receipt/log；独立离线核对器不进入run_config仿真路径，其旧/新SHA单列。当前所有模型源码与元素SHA均与26次原始运行一致，仅允许此离线核对器单独修订。最终使用新核对器重新执行全部T6断言，入口退出0，AC-02-07/09/11的T6部分通过。最终完整V0.1回归也在修复后重跑，证据以`v01-bounded/`为准；早先两轮通过记录不替代最终记录。

准备时同步完善了扫描和报告代码；准备产物按最终入口逐文件和schema重新验证。文档收口发生于运行后，不把文档字节身份冒充运行时Git diff；未在两种放置之间修改模型。

## 限制与范围

硬件参数仍为 `configured_unvalidated`，未校准绝对性能，不以盘控必须更快为通过条件。官方有限top-100的严格FP64/ID并列边界仍unresolved；实际top-k距离由正式FP64算子产生。仅固定32/128查询样本，不是全10k query benchmark，p99不作稳定尾延迟结论。没有修改搜索控制工作量或用离线trace替代闭环。

每次逐页完整布局初始化，不使用快初始化；保留CMT、SIMPLE/TURNED_OFF、无应用对象跨查询缓存，不重置MQSim时间/FTL。RSS为SST wait4与Python RUSAGE_SELF高水位，父进程peak是后处理保守上界；分阶段/非文件私有/整树同时RSS仍Unsupported。外层wait4可能包含已回收后代的高水位，不能替代父进程自身RSS。本轮有100MiB子进程探针验证该口径，日志保留于rss-scope-probe；没有把它计作规模或模型验收。

磁盘为每秒逻辑文件大小与卷余量采样，不是allocated-block连续峰值或硬隔离；RSS预算也是完成后检查。详细核对器仍在内存保留固定8条的事件，未承诺任意规模详细日志有界。mmap驻留/必要query与结果、allocator高水位仍须与活动容器分开理解。Deep全量建图资源门槛保留；本轮不开始T7或T8。
