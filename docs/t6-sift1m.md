# T6 SIFT1M 实验入口

`tools/verify_t6.py` 执行完整 1,000,000×128 SIFT base 上的锁定 Faiss NSG 图和项目 `ndpsim-ranked-expansion-v1` 搜索。配置为 `configs/experiments/v0.2/sift1m.json`，查询身份和预算继续使用 M0 冻结的两个 JSON，不重新抽样、不用评价集选择配置，也不启动 T7。

分开准备与运行（锁定 Python；目录必须不存在）：

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/verify_t6.py \
  --source-dir .cache/v0.2-m0/data/sift \
  --receipt results/v0.2-t2/builder/index-builder-receipt.json \
  --prepare-only --output results/v0.2-t6/preparation
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/verify_t6.py \
  --prepared results/v0.2-t6/preparation/prepared \
  --source-dir .cache/v0.2-m0/data/sift \
  --build build/delivery --output results/v0.2-t6/matrix
```

也可省略 `--prepare-only` 一次执行两阶段。建图器 receipt 按 [T2](index-truth-v2.md) 从锁定归档构建；已有目录、M0 探针或只检查目录存在均不构成可复用产物。复用前逐文件核对准备清单 SHA256，再重新检查完整数据、图和真值绑定。建图使用四线程、R/GK64、L200、C500、NNDescent；不承诺多线程重建逐位一致。`benchmark_artifacts.py build --timeout` 允许使用冻结的 8 小时建图预算。

准备阶段对 tuning32、evaluation128、concurrency32、detail8 分别导入完整 base 和显式查询顺序，按同一原始行号导入官方 top-100 ID 真值。只在 tuning32 上运行原生 search_L256 图质量诊断。四份 base 是显式存储成本，受同一 15 GiB 数据集磁盘预算约束。

正式矩阵共 26 个放置运行，顺序执行 host/controller；各次均重新逐页预装载整个 1,024,000,000 B 布局。两放置保持非 placement 配置、图、查询顺序和数值语义一致：

- tuning32 和 evaluation128 各执行四点 L/E=32/64、64/128、128/256、256/512，两放置共16次。
- evaluation128 的主配置128/256再运行一遍，两放置共2次；比较完整结果和全部模拟统计，排除墙钟。
- 固定评价集前32条在128/256补做并发2/4，两放置共4次；对照并发1的相同原始查询结果/路径摘要。
- 固定评价集前8条在128/256各跑 statistics/detail，两放置共4次；比较结果/模拟统计，并对detail独立重放每批搜索及核对读取、PCIe、credit。

独立核对器使用按行读取的向量、查询及邻接文件，避免全 base 展开为 Python 浮点对象；其事件和决策仍按选定8条详细子集保留内存，不宣称对任意详细日志恒定内存。小规模旧 oracle 和所有账本断言保留。

GraphHost 仅在 `PreloadWindow` 确认全部页写入完成、后端无事件且普通请求为零后记录 `initial_state`：实际完成 sectors、origin、后端 pending/drained、CMT retained、未重置时间。该记录进入 statistics/resources 和 CSV；每次扫描另保留初态解释。查询 start 必须不早于 origin，end/drain 时间不倒退。没有快初始化或状态恢复。

每个运行保留配置、输入/索引/真值/代码/依赖身份、结果/距离/质量/决策摘要、资源统计及 SST/后处理测量；扫描输出 `scan.json`、`scan.csv`、`quality-matched.json`、`report.md`、`assertions.json`、`ac-matrix.json` 与 manifest。质量匹配按调参集达到0.80/0.90/0.95的最快平均接纳后时延点选参数，独立评价只报告同参数实测质量；无点满足时标 unmatched。全部扫描点均保留，图质量和最高搜索点门槛失败不因选择其他点而消失。

资源预算：导入1GiB/30min、建图8GiB/8h、真值2GiB/2h、初始化4GiB/1h、每点查询4GiB/2h、后处理512MiB/10min，另受60%物理RAM限制。完整SST peak同时约束初始化/查询；Python父进程的RUSAGE_SELF生命周期peak作为后处理的保守上界，不是分阶段RSS实测。SST RSS通过wait4、Python父进程RSS通过RUSAGE_SELF完成后检查，不是硬隔离；超时杀进程组。磁盘每秒采样输入/准备/输出的逻辑文件大小及卷余量，记录采样峰值、15GiB预算与20GiB余量断言；可能遗漏亚秒瞬时峰值，不是allocated-block或全进程树scratch监控。

官方真值不虚构距离，严格FP64/ID并列边界仍unresolved。128条评价为固定样本，不能称全10k查询或稳定p99；硬件参数仍configured_unvalidated，模拟ns/QPS与宿主墙钟/RSS分开。实际执行与阶段状态见 [T6收口](t6-closeout.md)；入口存在不等于规模验收通过。


需要重新后处理已成功的26个独立放置产物时，可在第二条运行命令增加 `--reuse-runs <原始运行目录>` 并使用新的 `--output`。入口逐项检查有效配置、完整准备身份、元素/模型源码、MQSim/SST身份、所有产物SHA及原命令receipt/log；只允许不进入正式仿真路径的独立离线核对器单独修订并记录旧/新SHA。缺失/不匹配即拒绝，不能把失败汇总或目录存在当成成功缓存。复用引用用符号链接保留原产物，原产物不可移动/改变；另存`*-reuse.json`及`*-initial-state.json`。核对报告与测量分别为`detail-comparison-report.json`和`detail-comparison.json`。
