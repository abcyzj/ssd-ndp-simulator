# 文档入口

当前开发目标为 V0.2：先读 [V0.2 PRD](prd-v0.2.md) 和 [实施计划](v0.2-implementation-plan.md)，从计划第 0 节开始并按第 6 节任务依赖推进。其余文档按任务选择，不要求遍历全部文档；现行实现说明仍描述已交付的 V0.1 基线，新能力验收后再同步更新。

| 目的 | 现行入口 |
| --- | --- |
| V0.1 交付状态、验证证据与继承基线 | [V0.1 交接](releases/v0.1.md) |
| 安装、构建、只运行实验 | [项目 README](../README.md)、[复建交付](delivery.md) |
| 需求范围与验收标准 | [V0.1 PRD](prd-v0.1.md)（V0.1 基线，非 V0.2 任务） |
| V0.2 需求与验收目标 | [V0.2 产品需求](prd-v0.2.md)（当前开发需求，非已实现能力） |
| V0.2 开发顺序、阶段门槛与当前起点 | [V0.2 实施计划](v0.2-implementation-plan.md)（第 0 节开工，第 6 节 T0～T8） |
| T5 统计/详细模式、回收、流式输出和资源证据 | [接口与限制](t5-observation-lifecycle.md)、[实际验证](t5-closeout.md) |
| T4 mmap/内存后端、公式布局与受控大 profile | [接口与限制](t4-content-profiles.md)、[实际验证](t4-closeout.md) |
| T3 真实小数据两放置闭环 | [接入接口与验证](t3-closed-loop.md)、[实际验证](t3-closeout.md) |
| T2 离线图、精确/官方真值及质量指标 | [图与真值](index-truth-v2.md)、[实际验证](t2-closeout.md) |
| T1 数据导入与 schema | [数据说明](data-v2.md)、[实际验证](t1-closeout.md) |
| T0 数据/建图依赖锁、资源预算及本机证据 | [M0 实证与接续](m0-closeout.md) |
| 模块职责、有效设计决定及原因 | [架构](architecture.md) |
| 完成边界、消息、时间与计费规范 | [契约](contracts.md) |
| 实际 C++ 接口与扩展方法 | [扩展指南](extensions.md) |
| 搜索语义与输入格式 | [搜索与数据](search.md) |
| 盘控分块、缓冲与生命周期 | [盘控资源](controller.md) |
| 并发、JSON/CSV、观测与统计 | [输出口径](output.md) |
| 持续回归与完整 AC | [测试说明](testing.md)、[工具分类](../tools/README.md)、[配置分类](../configs/README.md) |
| 未校准参数、Unsupported 和已知问题 | [模型限制](model-limitations.md) |
| 依赖修改、原生 oracle 与许可证 | [MQSim 维护](mqsim-source-management.md)、[锁文件](../deps.lock.json) |

[历史归档](archive/v0.1/README.md)仅用于追溯，不是当前执行清单。历史“未实现/下一步”描述属于当时阶段；历史通过也不等于当前检出已重跑。需求、现行文档、代码或测试发生冲突时，明确差异、影响和证据，再修正相应内容，不能直接用旧方案覆盖当前行为。
