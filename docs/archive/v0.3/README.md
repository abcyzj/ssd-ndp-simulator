# V0.3 归档索引

2026-11-03（Asia/Shanghai）整理。当前开发入口为 [V0.4 PRD](../../prd-v0.4.md)与[实施清单](../../implementation-plan-v0.4.md)。这里的阶段状态只描述 V0.3，不是新版本待办；V0.3 正式交付结论保留在[发布记录](../../releases/v0.3.md)。

| 材料 | 用途 |
| --- | --- |
| [实施计划](implementation-plan.md) | V03-0～7 的历史任务、完成证据和决策 |
| [direct 冻结](direct-baseline.md) | V03-0 冻结输入、预算和独立核对 |
| [schema](ir-schema.md)、[生命周期](ir-lifecycle.md) | V03-1/2 接口与阶段收口 |
| [映射](ir-mapping.md)、[动态前端](ir-frontend.md) | V03-3/4 接口与阶段收口 |
| [观测](ir-observation.md) | V03-5 日志、关联和严格比较 |
| [完整矩阵](ir-matrix.md) | V03-6 的52条、交替效率和Q梯度证据 |

PRD 与 IR 总规范继续保留在 [prd-v0.3.md](../../prd-v0.3.md)、[ir-v0.3.md](../../ir-v0.3.md)，供 V0.4 继承。上表接口与收口合写的文档虽移入归档，其已实现接口约束继续有效；现行索引按需直达，不能因归档删除验证或放宽断言。

[工具索引](../../../tools/archive/v0.3/README.md)和[配置索引](../../../configs/archive/v0.3/README.md)集中说明继承用途。实现、锁、查询顺序、配置参数、tests 和 C++ 源码保留原路径与内容；只有文档检查器中的迁移路径随文档更新。

本机结果实体移至 `results/archive/v0.3/`，保留 `results/v0.3 -> archive/v0.3` 相对符号链接。未删除失败样本、改写 manifest 或重算历史成功状态；旧路径和绝对引用仍可访问。结果仍被 Git 忽略，不随检出分发；新机器须按正式流程重建。保留整个 results 目录结构或同时备份链接及目标，不能只复制链接。

V0.2 输入、准备产物和结果仍被 V0.3/V0.4 引用，本轮不移动。归档改变部分文档和文档检查器的源码身份；历史产物不因此自动获得当前身份，严格复用检查不放宽。静态与运行验证见[归档检查](archive-checks.md)。
