# 文档入口

当前开发目标为 [V0.4 PRD](prd-v0.4.md)：现有图搜索的独立原生程序、Profiler、CPU Cost Model、内存驻留单线程精度验证及盘控条件预测。需求与[18项实施清单](implementation-plan-v0.4.md)已整理，V04-0开工冻结、V04-1a纯算法核心、V04-1b原生构建与运行、V04-1c参考路径语义出口、V04-2a契约、V04-2b Profiler与静态IR采集、V04-2c训练采集与 A/B/A 、V04-3a 模型接口与失败关闭、V04-3b 训练拟合与冻结、V04-3c 静态执行与内存驻留及 V04-3d worker 与 CPU 顺序、V04-4a Validator 框架已完成。V04-4b 已完成，AC-04-04 按两点口径通过。V04-4c 已完成，两点单线程 Level B/C 及 Host 静态语义通过，其余4项待实施；[定向复核](source-audit-v0.4.md)记录此前核查与当前commit一致及证据边界。2/4 worker 为诊断项，真实 SSD/盘控硬件校准不在本版范围。

当前可运行交付为 [V0.3](releases/v0.3.md)，继承语义先读 [PRD](prd-v0.3.md) 与 [IR 规范](ir-v0.3.md)。V0.3 阶段历史统一从[归档索引](archive/v0.3/README.md)追溯；下表只列现行开发与接口入口。V0.4 按实施清单分项推进，历史通过不替代本轮验收。

V0.4 新路径已修订为原生真实搜索插桩采集完整静态 IR，随后输入仿真器按依赖执行，见 [PRD §6.4](prd-v0.4.md#64-静态-ir-输入依赖执行与资源调度)。包契约、离线预检、原生采集器和内存驻留执行入口已实现；旧动态 `ir` 和 `direct` 保留，V0.3 诊断日志不自动成为可执行包。全查询执行输入与 detail8 诊断分开。新版矩阵允许列名阶段使用实测 CPU 服务表，模型训练仍隔离，整机精度门槛保持。查表已接入单线程驻留回放与单点 Validator。

| 目的 | 现行入口 |
| --- | --- |
| V0.4 正式需求、范围与精度出口（待实施） | [产品需求](prd-v0.4.md) |
| V0.4 任务依赖、实现范围、验收/证据与默认决策（待实施） | [实施清单](implementation-plan-v0.4.md) |
| V0.4 12个模拟配置、配套重复/采集及52条深度回归触发条件（待实施） | [验收矩阵](validation-matrix-v0.4.md) |
| V0.4 已完成的开工冻结、实际命令、预算与限制 | [冻结记录](start-freeze-v0.4.md) |
| V0.4 既有核查版本、当前源码定向复核及证据边界 | [复核记录](source-audit-v0.4.md) |
| V0.4 纯核心接口、独立oracle及局部验证 | [V04-1a收口](core-v0.4.md) |
| V0.4 原生构建、运行与驻留试跑 | [V04-1b收口](native-v0.4.md) |
| V0.4 原生、direct、动态 IR 参考语义与独立核对 | [V04-1c收口](reference-semantics-v0.4.md) |
| V0.4 静态包预检、计时桶与资源归属 | [V04-2a契约](contract-v0.4.md) |
| V0.4 Profiler 四档、采样和完整静态包 | [V04-2b收口](profiler-v0.4.md) |
| V0.4 全规模训练采集与 A/B/A | [V04-2c收口](training-v0.4.md) |
| V0.4 CPU Profile 估计、身份绑定与失败关闭 | [V04-3a收口](cpu-profile-v0.4.md) |
| V0.4 训练拟合、模型选择与冻结 | [V04-3b收口](cpu-fit-v0.4.md) |
| V0.4 静态回放、内存驻留及 worker 调度 | [V04-3c收口](replay-v0.4.md)、[V04-3d收口](replay-v0.4.md#worker-与-cpu-顺序) |
| V0.4 单点 Validator、报告及对抗夹具 | [V04-4a收口](validator-v0.4.md) |
| V0.4 Level A 算子评价 | [V04-4b 入口与证据](level-a-v0.4.md) |
| V0.4 单线程 Level B/C 和静态语义 | [V04-4c 入口与证据](level-bc-v0.4.md) |
| V0.3 对象、算子、依赖、映射与生命周期 | [IR 设计规范](ir-v0.3.md) |
| V0.3 已实现 schema、能力目录与静态校验 | [V03-1 接口与收口](archive/v0.3/ir-schema.md) |
| V0.3 已实现动态依赖、可见值、状态及回收 | [V03-2 接口与收口](archive/v0.3/ir-lifecycle.md) |
| V0.3 已实现算子、host/controller 映射与验证 | [V03-3 接口与收口](archive/v0.3/ir-mapping.md) |
| V0.3 动态前端、engine 配置、旁路审计与验证 | [V03-4 接口与收口](archive/v0.3/ir-frontend.md) |
| V0.3 观测、日志关联、规范化与独立比较器 | [V03-5 接口与收口](archive/v0.3/ir-observation.md) |
| 构建、日常实验与复建 | [项目 README](../README.md)、[复建交付](delivery.md) |
| 模块职责、有效设计决定 | [架构](architecture.md) |
| 时间、消息、可见性与唯一计费 | [契约](contracts.md) |
| C++ 接口与扩展 | [扩展指南](extensions.md) |
| 搜索语义、盘控资源 | [搜索](search.md)、[盘控](controller.md) |
| 数据导入、离线图与真值 | [dataset-v2](data-v2.md)、[图与真值](index-truth-v2.md) |
| 真实输入接线与身份校验 | [仿真输入](simulation-input.md) |
| mmap、公式布局与设备 profile | [内容与设备](content-profiles.md) |
| 观测、资源回收与输出 | [观测与生命周期](observation-lifecycle.md)、[输出口径](output.md) |
| SIFT1M 准备、固定矩阵与严格复用 | [SIFT1M 实验](sift1m.md) |
| 持续测试与继承验收 | [测试说明](testing.md)、[工具分类](../tools/README.md)、[配置分类](../configs/README.md) |
| 未校准参数、Unsupported 和已知问题 | [模型限制](model-limitations.md) |
| 依赖维护、原生 oracle 与许可证 | [MQSim 维护](mqsim-source-management.md)、[锁文件](../deps.lock.json) |

## 版本基线与历史追溯

- [V0.3 归档](archive/v0.3/README.md)：阶段计划、冻结参考、接口收口和52条矩阵；[交付记录](releases/v0.3.md)保留正式结论。

- [V0.2 交付记录](releases/v0.2.md)：继承基线和历史验证证据；[归档索引](archive/v0.2/README.md)收纳 PRD、阶段计划、收口和实验记录。
- [V0.1 交接](releases/v0.1.md)、[V0.1 PRD](prd-v0.1.md)：继承需求与验收基线；[归档索引](archive/v0.1/README.md)保留阶段过程。

归档中的“当前”“未实现”“下一步”只适用于当时，不作为后续版本任务清单。V0.3 实施顺序和完成证据见[实施清单](archive/v0.3/implementation-plan.md)，不将其已完成状态沿用为 V0.4 进度。V0.4 已有正式PRD、任务清单和静态复核，已有[V04-0冻结证据](start-freeze-v0.4.md)、[V04-1a局部功能证据](core-v0.4.md)、[V04-1b原生证据](native-v0.4.md)和[V04-1c参考语义证据](reference-semantics-v0.4.md)，其余任务和各项精度出口状态见实施清单。历史通过不等于当前检出已重跑。需求、现行文档、代码或测试冲突时，明确差异与影响后处理，不能直接用旧方案覆盖当前行为。
