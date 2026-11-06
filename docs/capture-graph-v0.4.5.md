# P04 新 IR 采集与独立语义校验

日期：2026-11-06（Asia/Shanghai）。**P04 已完成；P05～P21 未开始，全部新 AC 仍待验收。** 依据 [PRD FR02/03/08/09](prd-v0.4.5.md)、[P01 冻结契约](contract-v0.4.5.md)和[实施计划 P04](implementation-plan-v0.4.5.md)。直接依赖 P02/P03 已重新核验：P02 run2、P03 run2/closeout1 的通过状态、manifest hash 及其 **191/91/9 个登记产物 hash** 均通过，见本次 `dependency-gates.json`。

## 实现与使用

[使用说明](../workloads/graph-search/CAPTURE.md)给出独立构建、夹具生成、capture/sample/benchmark/check 命令及配置。主要交付：

- [新原生执行与采集](../workloads/graph-search/src/search/capture_v2.h)：`graph_benchmark`、`graph_uninstrumented`、`graph_capture`、`graph_sample` 共享真实搜索函数；SDK Recorder 逐查询输出完整操作/阶段，保留原算法、容器、FP32 输入、固定维序 FP64 L2 及禁 fast-math/FMA。旧原生模式暂留迁移参考，不能冒充 IR/2。
- [图算子声明](../extensions/graph/operators.json)与原生静态声明绑定；[图元数据规则](../workloads/graph-search/python/graph_contract.py)注册输出大小，图循环不进入通用 SDK。冻结 schema 未改，Python SDK 新增携带同字节 Measurement/2 schema 并支持严格 schema 检查。
- [负载入口](../workloads/graph-search/python/graph_capture.py)：显式 SDK/Python/算子引用，校验原始输入、构建、查询 ID/顺序及包身份，封装 IR/2；分开输出原生参考、结构报告、独立语义报告、Measurement/2、采样质量、资源及命令/退出码/hash。相对路径以配置为基准；输出目录必须新建，失败保留并撤下有效 package 名称。
- [独立 oracle](../workloads/graph-search/python/graph_oracle.py)：不调用原生搜索或历史工具，使用 mmap、Python 集合/排序、固定维序 binary64 和独立排名/逆序公式，重建全部查询的 top-k、Recall、七项语义 Work、逐阶段 Work/特征、摘要、访问、状态版本、依赖及终态。SQLite 按稳定键关联，不依赖物理行序。
- [测试](../workloads/graph-search/tests/test_capture_v2.py)及[合成夹具生成器](../workloads/graph-search/python/make_fixture.py)：正常、损坏、重算 hash 后的语义篡改、采样质量/覆盖、类型、预算和发布负例。根构建复用同一测试，保留现有功能/资源断言；迁移清单纳入 `extensions/`。

## 阶段、依赖和测量边界

完整采集 INIT、MARK_SCORED、SCORE_L2、MERGE_CANDIDATES、SELECT_FRONTIER、MARK_EXPANDED、READ_ADJACENCY、JOIN、DEDUP_NEIGHBORS、FINISH；SCORE_L2 每语义 chunk 含 prepare/L2/append 三阶段。稳定键使用原始 query ID、op、stage、chunk 和连续 segment；prepare 中逐 item 访问及维度/顺序对应 `graph-l2-items/1`，不在维度内拆分。完整包不抽样，不接受 legacy detail flags。

四种依赖均显式表达。同批邻接行从 MARK_EXPANDED 分支，JOIN 等所有行；不把物理采集次序变成行间 ORDER。state mark 是零服务版本意图，插入成本由相应 resident prepare 完整且仅一次承担；消费者须沿 prepare 完成路径才能放行。JOIN 只描述控制/邻接引用组合，没有额外业务复制或存储服务。

原七项 `semantic_work` 保持参考含义；prepare、append 等额外服务 Work 不盲目相加作为语义 Work。prepare 含取数/复制/状态插入；L2 含 decode/算术/packed 输出；append 含拼接及向量/score 临时容器销毁；dedup 含邻接/chosen 容器释放；finish 含 top-k 形成和查询持有容器/状态销毁。采集摘要/序列化属于观察开销。

CPU 采样按**每原始 query、每 stage kind 从0开始**应用 stride；MARK/JOIN 是显式零服务阶段，不进入采样 eligible。终止空 SELECT 仍记录和采样。prepare/L2/append 相邻时钟边界核对，外层 score 审计不重复计费。Measurement 按完整稳定键/Work/特征绑定独立采集包，缺失、重复、交叠或失配拒绝。原始样本先经严格类型 schema 检查，不能把浮点/布尔计数规范化为合法整数。

空探针开销为 floor(ns/count)，保留负 corrected_ns；corrected_ns 小于时钟分辨率即 low_signal，保留供审计、不得直接用于拟合/制表。样本覆盖与正式质量分列，没有以 QPS 或整查询残差补费。采样运行的 query CPU 包含观察开销，不能替代独立 benchmark。新采样规则、采集构建和准备边界均使旧表/模型失效；P06 仍须冻结拟合与低信号聚合规则。

`reference.json` 是原生 stdout 的规范 JSON，报告绑定其实际文件 hash；`native-reference.json` 保留原始 stdout 字节。top-k 为**原生参考**，不是模拟重新计算的结果。包只保存对象元数据、摘要、Work、访问和依赖，无原始 payload 路径、向量数值或 CPU 采集时戳。语义报告在包外绑定包 ID/参考/校验器源码。

## 实际运行的检查与结果

本机证据：`results/v0.4.5/p04/run1/`、最终完整运行 `run2/` 及 `closeout1/`。各目录保存编排、命令/cwd/退出码、日志、输入/有效配置、源码/构建/依赖身份、检查和产物 hash；正式 AC 没有记通过。

- P02/P03 依赖重新核验通过；MQSim/SST 实际源码、完整 commit、MQSim 相对原生 diff 均未改，完整值保存在 `dependencies.json`。没有提交、推送、升级或初始化 Git。
- 仅复制 workload、SDK、Python 包和算子声明到带空格隔离目录；SDK 配置/构建/安装后，图工程通过 `find_package` 接入，Python 包 `pip --no-index --no-deps --no-build-isolation --target` 安装。**SDK CTest 2/2、独立图工程 CTest 4/4**；编译记录无仓库、third_party、SST/MQSim 路径，`otool -L` 只有系统 libc++/libSystem。pilot CLI 从外部 cwd 使用安装后的 Python 包运行。
- 新采集测试 **11/11**，覆盖四模式结果/Work、stride1/3、全部阶段/四类边、合法物理重排及重排后的采样、重封包语义篡改、参考结果篡改、删样本/重复/时间/类型负例、预算/失败目录/新目录门控。浮点/布尔 Work 和强制负 corrected_ns 另作定向检查。
- 合成 pilot 固定 **N=1024、d=128、Q=32、R=16、L/E=128/256、B=1、chunk=16**：完整采集 **33,196 操作、41,446 阶段**，全部查询逐操作/阶段独立语义通过；独立采样 **29,031/29,031** 覆盖，通过 score 审计。本轮 **92 个 low_signal** 保留，不能算训练/表质量通过。
- 在执行前冻结 A=uninstrumented、B=benchmark、1 worker/admission、原生首接纳至最后可见窗口、QPS 指标及三组 A/B/A 日程；**9 次运行**完成，绝对扰动中位 **0.1964% ≤5%**。它验证本轮合成 pilot 的低干扰 benchmark，不是完整 SIFT 的正式 A/B/A，也不是逐阶段采样开销≤5%的声明。
- 每次运行重新取实际 RAM/压力和磁盘预算；pilot 原生峰值约 **5 MiB**。采样 Python 后处理由 run1 的 **482.4 MiB** 降至 run2 的 **251.3 MiB**，均有实测与预算快照。包的原始输入移走后，独立结构校验仍通过；没有宣称此时已运行 timing-only 模拟。
- 根配置、构建及 CTest **94/94，49.17秒**；P02 SDK Python 回归 **16/16**、P01 设计 **11/11**、P00 门控 **3/3**，迁移审计 **321文件/94根CTest**、`git diff --check` 通过。
- 定向 AddressSanitizer 独立构建及 **4/4 CTest** 通过。最后增强原始样本类型检查后，重新运行11项采集测试和ASan测试，并对 run2 全部29,031样本及完整pilot包重新严格校验；测量产物字节完全相同。原生源码/二进制、SDK及根构建身份均与run2相同，复用该身份的根94项回归，不重复无关矩阵。

早期开发失败 raw shards 位于 `/private/tmp/p04-dev-39v50gy3/`：graph_sample 误触发旧 shard flush，导致多出数字文件；已修复并保留失败输出。run1 是通过的中间身份；后续减少样本计划内存、去除邻接兄弟节点多余 ORDER、规范化参考文件后，在新 run2 重新采集/采样和执行全部 A/B/A/回归，没有编辑 hash 继承旧通过。closeout 的类型检查只加强后处理，原生计时身份未变，并重新验证全部既有样本。

## 仍存在的限制

- 只关闭 P04 的实现、小夹具和合成 pilot 出口。P00 记录的**原锁定 NSG/prepared 缺失**仍阻塞正式输入；没有运行 SIFT1M tuning32/evaluation128 的四包、正式重复/训练或精度验收。AC01/02/04 仍依赖 P08/P15/P18 等后续出口，全部12项新AC待验收。
- 当前采集/测量上下文仅 **resident_random/2，Host 1/1**。SSD-return/Controller-command 准备上下文未实现测量，禁止默套这些 resident 实测表；P13 必须提供合法模型或失败关闭。本项没有实现模拟执行、模型/拟合或 SSD 映射。
- 只验证本机 macOS arm64。隔离复制/安装不是全新 OS 或容器级文件访问封锁。安装复用预装 jsonschema/referencing，不下载或更改系统安装。
- RSS 是实测失败门控，不是硬内存限制；后处理超时在检查点检查，不是抢占沙箱。SQLite关联及逐查询oracle有预算，但Measurement最终数组仍驻留内存；本轮pilot不是任意规模/正式全量的可行性证明，后续须重新预算。
- 低信号、频率/亲和性、分页和宿主条件必须继续分列。设备、PCIe、Controller 仍 `configured_unvalidated`；不推断下沉更快。

本次到 P04 收口，没有启动 P05 或其他后续任务。
