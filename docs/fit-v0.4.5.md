# P06 独立拟合／制表

日期：2026-11-06（Asia/Shanghai）。**P06 已完成；P07～P21 未开始，全部新 AC 仍待验收。** 依据 [PRD FR08](prd-v0.4.5.md)、[P01 模型/Measurement 契约](contract-v0.4.5.md#4-mapping模型和measurement)和[实施计划 P06](implementation-plan-v0.4.5.md)。直接依赖重新核验：P02 run2 的191个登记产物、P05 run4 的58个登记产物及其 manifest hash 全部通过；当前 P05 生产模型源码身份未变化。

## 实现和入口

- [通用服务公式与拟合算法](../python/ndpsim/cost_models.py)：常数、非负线性、闭上界表、现有 log-GBDT 算法，公式与图阶段分离。固定顺序、固定训练超参数，无随机搜索；参数 schema 的规范 hash 与 P05 C++ 五种内置公式逐项一致。
- [独立拟合/质量/制表](../python/ndpsim/fitting.py)：显式版本化训练 manifest、Measurement/2、新 IR/2 包和独立报告 → Profile/1 与质量/误差报告。原始 ID 四折、训练/评价隔离、全量调用覆盖、范围和输入身份校验，使用 SQLite 索引阶段元数据。没有旧 tools 或 V04-0/V04-2c 成功目录依赖。
- [开发 CLI](../python/ndpsim/fit_cli.py)：`python -m ndpsim.fit_cli check|fit|table|evaluate --manifest ... --output ...`，合成输入显式 `--allow-fixture`；输出必须新目录，失败退出2并撤下有效 `profile.json` 名称。统一用户 CLI 仍归 P14，没有提前实现其他任务。
- [图搜索配方](../extensions/graph/fit-recipe.json)：独立于通用工具，冻结全部阶段/额外特征、互斥服务桶、state/JOIN 零服务、merge_append 实测表及 dedup GBDT。正式训练仍为五个 L/E 点，16/32 stride1、其余 stride8；原始 tuning32/evaluation128、1M×128/k10/B1/chunk16。仅 resident_random/2 Host 1/1/1，不能套到 SSD 准备上下文。
- [使用与输入契约](../python/FITTING.md)说明 manifest/recipe 字段、相对路径、预算、质量规则、输出、来源信任和失败处理；Python 包版本更新为0.4.5.dev6，携带同字节 Profile schema。无新增运行依赖、无 C++/SST/MQSim 安装前提。

## 质量、隔离与报告

每个显式逻辑阶段恰有一个服务归属；表阶段与预测阶段分列，声明零服务不计预测误差。真实 Work 仍由采集侧独立报告负责，拟合工具不重算图搜索或把 hash 当成功能证明。

Measurement 的完整稳定键、chunk/segment、Work、特征、单位、target、语义/构建、包和准备 context 必须匹配。独立重建按 query/stage kind 的 rank/stride 计划，核对 eligible/planned/observed、无丢样；检查 raw/end-start、探针开销、带符号 corrected_ns、low_signal、单调用/线程 CPU 时钟、native 查询窗口、跨查询 CPU 桶不重叠、零 major fault、1/1/1 配额以及声明的 score prepare/L2/append 连续边界。QPS 和墙钟残差不参与成本拟合。

本版冻结严格低信号规则：预测样本排除 low_signal，并要求每 run/stage 低信号占比≤5%；不聚合短窗口。表调用在每次独立测量中都必须合格，不把负值/短样本截断成可用服务。至少三个不同 run ID 和不同 Measurement artifact；逐调用取 `median_low`，偶数重复也有明确整数规则。所有调用和全部 Work/features/外层 binding 精确匹配，缺项即拒绝。

预测四折按声明 tuning 原始 ID 顺序 modulo4 分组，同一 query 的所有点、调用和重复不能跨折。报告每折训练/留出 IDs、范围、域内/域外数量和预测误差；域内留出覆盖与误差必须过冻结门槛后，才以全部合格 tuning 样本重拟合。正式图配方域内覆盖至少50%，误差 P50≤10%、P90≤20%；不把域外调用冒充成功。最终 evaluation 要求全在冻结范围内，不调参、不输出新 Profile；失败保留误差报告和失败状态。

表绑定和 evaluation 都先核验已经冻结的 Profile/fit报告及其完整模型、配方、target、训练 IDs，再消费评价样本；采集构建/语义等身份变化拒绝。报告保留 Measurement、fit/quality report、源码/解释器和全部输入 hash；实际路径另存 `resolved-inputs.json`，相同输入与工具身份重复拟合的 Profile 和 fit报告字节一致。合成证据、表质量、预测误差与正式验收状态分别标识。

## 实际检查与结果

最终本机证据：`results/v0.4.5/p06/run4/`，包含命令/cwd/退出码、stdout/stderr、有效输入、隔离安装与全链路产物、源码/依赖/构建身份和 hash。

- [新增测试](../tests/test_v045_fitting.py) **16/16**：四种拟合、重复字节确定性、四折 ID 隔离、评价不重拟合、质量/范围/单位/浮点布尔计数/采样覆盖/时钟/窗口/哈希/target/新目录/预算负例、至少三次独立表、低信号排除与拒绝、篡改冻结模型拒绝、外部 cwd/空格路径及 CLI 失败退出。
- [独立 C++ 消费探针](../tests/fit_profile_probe.cc)通过 P05 生产注册/加载/估计接口，核对 Python 导出四种公式和逐调用表的 ns；表缺项和完整 Work 失配失败关闭。五种参数 schema hash 跨语言逐项一致。
- 仅复制 `python/` 到空格路径隔离源树，`pip --no-index --no-deps --no-build-isolation --target` 安装；CLI 只用安装包，`PATH` 指向空目录，从外部 `/tmp` 执行两次 fit、table 和 evaluate。无编译器/模拟工具调用，无历史结果前置；两次 Profile/fit报告字节一致。
- 根配置、构建及 **96/96 CTest** 通过；P05 **15项/228请求**、P02 **16/16**、P01 **11/11**、P00 **3/3** 通过。定向 ASan 构建/CTest **1/1**，重跑同一16项拟合/制表与 C++ 消费测试。
- 使用 P04 留存的真实图采集合成 pilot 包，独立复核 **41,446 阶段、29,031/29,031 样本**；完整覆盖、Work/特征、CPU 窗口、桶互斥与 score 边界通过。**92 个低信号**（adjacency_prepare 4、merge_append 88）保留；本次仅 check，未把这批低信号数据制成合格表或正式模型，未启动新原生采集。
- 迁移审计 **335文件/96根CTest**，新增模块/配方/文档/测试纳入逐项清单；`git diff --check` 与源码/依赖/产物 hash 检查通过。MQSim/SST 源码、commit、相对原生 diff 未变。

开发失败保存在 dev1/run1/run2 等目录及记录的临时隔离目录：初始合成 token digest 不合 schema；早期测试共享对象引用，输入篡改也改动了表期望；报告包含输入 manifest 实际路径导致同内容不同文件名重跑 hash 不同，已将路径与内容身份分开；隔离编排曾对已相对化的共享引用再次做路径变换，严格加载拒绝，修复编排后新目录重跑。没有放宽生产断言，最终身份使用 run4 的实际检查，不继承早期通过。

## 限制

只关闭 P06 工具与小夹具出口，未生成正式 SIFT1M 校准模型、正式完整表或精度结论，AC08 仍依赖后续唯一计费、Validator 和 P18 出口。原锁定 NSG/prepared 缺口没有改变。P04 pilot 的88个低信号 append调用不能直接制表，正式任务须重新采样至合格，或显式版本化采集/配方和聚合协议；不能编辑 hash 或补0通过。

当前 CPU 测量仅支持单调用、线程 CPU clock、Host 1/1/1；无低信号聚合、自动模型选择或动态公式/脚本加载。内存中保留参与拟合的样本，Profile/报告单份最多64MiB，SQLite/输入/样本/输出/时间预算有检查但不是硬 RSS/磁盘沙箱，也不是任意规模可行性证明。完整规模操作必须重新预检资源。

结构/语义/运行成功报告是外部 hash 绑定的可信来源证明，不是对恶意生产者的认证；本工具不重跑业务 oracle，不重做注册输出大小/DAG 证明。隔离验证为本机 macOS arm64 源树/安装/PATH 隔离，不是全新 OS 或文件访问封锁。跨平台数学函数逐位相同未承诺。设备/链路/Controller 仍 configured_unvalidated，不推断下沉更快。

本次到 P06 收口，没有启动 P07 或其他后续任务，没有提交、推送、升级或初始化 Git。
