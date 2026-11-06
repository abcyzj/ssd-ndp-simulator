# P15 三层 Validator

日期：2026-11-06（Asia/Shanghai）。依据 [PRD FR09～12、§6.1](prd-v0.4.5.md)、[实施计划 P15](implementation-plan-v0.4.5.md)及 [P01 冻结契约](contract-v0.4.5.md)。**P15 实现及夹具出口已完成，P16 未开始，全部新 AC 仍未验收，当前可运行正式交付仍为 V0.4。**

直接依赖 P04/P06/P11/P13/P14 已核验完成状态及全部登记产物：分别312、148、322、403、2041项 hash 全部匹配。它们只作为依赖证据完整性，不沿用为本版正式精度或 AC 通过。

## 实现与边界

- [三层核对器](../python/ndpsim/validation.py)与[独立图SSD账本](../python/ndpsim/graph_validation.py)由安装后的 `ndpsim verify` 显式调用。新 `ndpsim-validation/1` spec 分 capture、simulate、accuracy；路径相对 spec，所有文件引用携带 hash。预算预检、子进程限时/退出码/RSS、日志、新目录及失败保留沿用统一 CLI，不隐式构建、采集或拟合预测模型。没有导入历史 tools/旧总验收。
- capture 层消费**可信的独立全查询语义／Work receipt**，复核包schema、文件/index/tail身份与完整性、操作/阶段/依赖计数、rank/终态闭合、registry、来源和全部原始 query ID、参考报告 hash。保留原生参考结果及语义 Work，不声称模拟重算 top-k。真实图搜索/数值/Work oracle 继续在 P04 采集侧执行，hash 不替代独立语义证明。
- simulate 层核验成功 run 的 status/命令/产物和有效输入快照，按稳定键检查全阶段/请求恰好一次、四类依赖与终态可见性、输入/计算/返回门控、排空、服务时间和来源、Host/Controller slots及worker/admission配额。纯模型公式与 C++ 资源服务独立核对；实测表按完整 binding、键、Work/特征精确查找；表索引和模型结构校验缓存只建一次，输入门控用O(N log N)区间匹配，避免逐调用全量扫描，不切分猜测、不重复计费。
- SSD 核对器独立实现从逻辑访问到 sector并集/页内合并、LBA请求、完整L2 item分块与Work、Host/Controller准备特征、向量D2H、NAND字节、PCIe事件生命周期/wire、协议大小/命令/结果、缓冲输入/计算读/结果写/返回读及全部credit的大小/取得释放时间。没有调用被测 C++ mapping、Controller或Layout实现自证。设备/链路/Controller仍为 `configured_unvalidated`。
- accuracy 层至少3次原生无探针benchmark和2次确定性内存Host 1/1/1模拟，核对包/构建/输入/语义/目标/上下文和全部 query ID。分位数 nearest-rank，原生各次统计取中位；P50/P95/QPS/CPU阈值固定15%/20%/10%/15%。显式重跑P06评价，要求模拟的预测规则与冻结模型完全一致，逐阶段P50≤10%、P90≤20%；如有表，另从完整合格独立样本重建表并逐字典比较实际模拟Profile。删样本、低信号、表缺项/篡改及训练评价隔离仍由P06独立质量断言约束，不把表误差计模型误差。
- 各层独立生成报告；accuracy保留capture子报告、每次simulate子报告、阶段需求、逐查询稳定键比较及预测/表质量报告。层级通过始终 `full_acceptance_passed=false`；quick、未知/总验收spec、重复路径/不足重复、失败状态、缺项不可能生成总通过。12项AC及最终矩阵聚合仍属P21。

核对发现并修正 P14 报告的窗口口径：主 latency 改为 `visible-admitted`，单列 offered latency；QPS窗口改为 `last_visible-first_admitted`，最终drain仍独立保留。资源服务和调度未改变。拟合测试夹具新增可选 context 参数，默认语义不变，使同一独立质量夹具可通过真实SST消费。没有新增运行依赖或修改MQSim/SST。

完整spec、运行命令、文件/身份及信任边界见[使用说明](../python/VALIDATION.md)。

## 实际检查与证据

最终本机证据为 `results/v0.4.5/p15/run2/`，包含依赖gates、命令/cwd/退出码/原始日志、源/构建/依赖身份、安装隔离及新生成的真实SST/原生采集产物、正负例、hash和限制。最终增补的Python断言及测试复验在 `final-directed3/`；未继承旧results作为本轮执行成功。

实际运行：

1. 锁定Python `/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tests/test_v045_validation.py build/delivery`：首轮7项；最终补强后 **8/8通过，16.745秒**。只复制python安装到带空格隔离路径，从 `/tmp` 使用安装CLI；新生成原生小图采集与独立oracle receipt，删除payload后运行真实SST的内存、SSD Host/Controller。SSD使用592B block bound触发完整item细分。内存1/2/4 worker、零服务、手算四查询各12ns/总48ns、offered P50=24ns核对。
2. 显式合成精度夹具通过实际fit/table/evaluate、真实SST两次模拟和三份标记合成的benchmark记录，覆盖三层报告和数值门槛；没有将合成benchmark当原生实测精度。用两份超阈值合成记录验证精度失败，保留错误。
3. 负例涵盖删阶段/样本/缓冲服务、重复服务/模拟运行、少重复、重封hash后的Work/表/参考/依赖/本地读/NAND/PCIe/协议/credit/返回时间篡改、未排空、失败run、quick、scope和预算不足。错误退出非零且不保留有效层级成功报告/总通过。
4. `cmake -S . -B build/delivery -DBUILD_TESTING=ON`、`cmake --build build/delivery -j2`、`ctest --test-dir build/delivery --output-on-failure`：配置/构建退出0，**138/138通过，93.60秒**。包含原有P06/P14回归及新增P15 CTest。之后仅补强Python Validator的输入/缓冲门控、类型检查和表/阶段索引及其测试，重跑全部受影响8项，没有重复无关C++回归。
5. P01设计测试 **11/11**，P00门控 **3/3**；迁移审计 **402文件/138CTest**，`git diff --check`及最终文档链接/输入/源码/产物hash核验通过。新模块、说明和测试已纳入逐项迁移清单。没有新增C++变更，因此没有另跑ASan；现行C++路径由根回归覆盖。

失败记录完整保留：初次沙箱内测试因只读sysctl资源预检被系统拒绝，随后在获准环境运行；开发期系统Python3.9的py_compile因默认缓存目录权限失败，正式检查使用锁定Python3.13。`run1/`的一项capture负例前置正例因Validator误将语义receipt的按kind计数与包汇总dict直接比较而失败，修复为按字段独立重建后在新run2重跑全部检查。没有放宽完整操作/阶段覆盖或精度阈值。

## 限制与未完成验收

- 未运行正式SIFT1M两点完整采集/独立benchmark/拟合/精度、正式A/B/A、P16原生MQSim对拍或P19 SSD账本。原锁定NSG/prepared缺口未由本任务解决；全部新AC仍待验收。P15不自动启动P16。
- capture层重新关联已由独立oracle产生的可信receipt；不重新打开payload。模拟层的模型适用性/唯一计费不等于模型质量；accuracy层才要求重验冻结模型/Measurement及表。第三方语义oracle和采集源的真实性不靠hash认证。
- 只能用trace可观察字段核对门控；同一ns内零服务的phase顺序仍依赖独立事件桥/Runtime测试。后端设备共同事件的原生oracle仍归P16，不能用本账本替代。
- Validator保留阶段索引和trace于内存；输入字节/记录上限、外层超时与退出RSS检查是失败门控，不是硬RSS沙箱或完整规模可行性证明。正式规模先重新核验RAM/磁盘/输出/时间预算。
- 仅验证本机macOS arm64。独立重复依赖可信生产者和可追溯命令；路径/hash检查不证明重命名拷贝是独立物理运行。通用扩展可用明确benchmark binding，图搜索不能绕过原生配置身份核对。
- formal-point只是点级附加规模门控，不是完整12项AC证据聚合器；不据此宣布版本交付、硬件校准或下沉更快。
