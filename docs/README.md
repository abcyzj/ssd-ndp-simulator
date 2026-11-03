# 文档入口

当前开发目标为 V0.3，先读 [PRD](prd-v0.3.md) 与 [IR 设计规范](ir-v0.3.md)。V03-0 已完成本轮 direct 参考冻结，尚未实现或验收 IR；后续按实施清单推进。两份正式文档独立于早期 Word 草案。

当前可运行基线仍为 V0.2。以下接口文档描述已实现能力，不能把 V0.3 目标视为现有接口；按任务选读，无须遍历历史阶段材料。

| 目的 | 现行入口 |
| --- | --- |
| V0.3 范围、等价性、验收要求 | [产品需求](prd-v0.3.md) |
| V0.3 对象、算子、依赖、映射与生命周期 | [IR 设计规范](ir-v0.3.md) |
| V0.3 direct 冻结证据、预算与复现 | [V03-0 收口](direct-baseline-v0.3.md) |
| V0.3 可执行任务、完成证据、阻塞项与默认决策 | [实施清单](implementation-plan-v0.3.md) |
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

- [V0.2 交付记录](releases/v0.2.md)：当前可运行基线和历史验证证据；[归档索引](archive/v0.2/README.md)收纳 PRD、阶段计划、收口和实验记录。
- [V0.1 交接](releases/v0.1.md)、[V0.1 PRD](prd-v0.1.md)：继承需求与验收基线；[归档索引](archive/v0.1/README.md)保留阶段过程。

归档中的“当前”“未实现”“下一步”只适用于当时，不是 V0.3 任务清单。V0.3 实施顺序和完成证据见[实施清单](implementation-plan-v0.3.md)。历史通过不等于当前检出已重跑。需求、现行文档、代码或测试冲突时，明确差异与影响后处理，不能直接用旧方案覆盖当前行为。
