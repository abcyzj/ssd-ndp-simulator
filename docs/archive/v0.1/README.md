# V0.1 历史归档

2026-10-30 收尾整理。以下为已结束阶段的原规划、任务、PoC、实现记录及验收叙述；修复相对链接、迁移后的脚本/配置调用路径并增加历史标识。原文的“当前”“下一步”“未实现”和命令适用当时阶段，不是当前执行要求。现行说明见[文档索引](../../README.md)，当前验证和基线见[版本交接](../../releases/v0.1.md)。

接口与仍有效的规则已提取到[架构](../../architecture.md)、[契约](../../contracts.md)、[扩展指南](../../extensions.md)、[搜索](../../search.md)、[盘控](../../controller.md)、[输出](../../output.md)、[测试](../../testing.md)及[交付](../../delivery.md)。历史任务清单中的输入哈希及阶段源码身份不随归档重算。

## 阶段材料

- [SSD 近存计算仿真平台 V0.1：源码核查与实施规划](implementation-plan.md)
- [M0.2 本机 macOS 环境与原生基线](m0.2-native-baseline.md)
- [M0.3a 事件桥 PoC](m0.3a-event-bridge.md)
- [M0.3b 本地读取 PoC](m0.3b-local-read.md)
- [M0.4 最小契约（冻结版本 1）](m0.4-minimal-contract.md)
- [M1.1 库化与生命周期骨架](m1.1-library-lifecycle.md)
- [M1.2 正式事件桥](m1.2-event-bridge.md)
- [M1.3 普通 I/O 与确定性初态](m1.3-ordinary-io.md)
- [M1.4 基础观测](m1.4-observation.md)
- [M2.1 内容、布局与驻留](m2.1-content-layout.md)
- [M2.2 异步依赖与可见性](m2.2-async-visibility.md)
- [M2.3 功能算子与成本模型](m2.3-operators-cost.md)
- [M3.1 主机图搜索与独立真值](m3.1-host-graph.md)
- [M3.2a 正式设备本地读取](m3.2a-local-read.md)
- [M3.2b 共享 PCIe 传输](m3.2b-shared-pcie.md)
- [M3.3 盘控计算与缓冲](m3.3-controller-buffer.md)
- [M3.4 两种放置端到端](m3.4-placements.md)
- [M4.1 并发与完整输出](m4.1-concurrency-output.md)
- [M4.2 验收与参数回归](m4.2-acceptance-regression.md)
- [M4.3 文档与可复建交付](m4.3-delivery.md)
- [SSD 近存计算仿真平台 V0.1 产品需求描述](prd.md)
- [SSD 近存计算仿真平台 V0.1 可执行任务清单](v0.1-tasks.md)

## 证据与保留项

原始 `docs/evidence/` 为既有忽略目录，脚本仍向该处生成证据；未移动、改写或删除已有证据，避免破坏 manifest 内路径和哈希。该目录不随 Git 检出提供，历史链接只在持有相应产物时可读，不能把缺失证据当通过。阶段文档是记录，原始日志/数据和 manifest 才能支持复核。复跑按现行测试/交付入口选择新输出目录。

阶段工具和配置也已分类迁移，见[工具归档](../../../tools/archive/v0.1/README.md)、[配置归档](../../../configs/archive/v0.1/README.md)。持续测试留在 tests/、configs/regression/，现行运行/验收工具按功能命名；脚本和文档命令路径已更新，旧证据内部原命令/哈希不改写。MQSim 历史迁移记录仍与持续维护清单放在[维护约定](../../mqsim-source-management.md)，避免拆散源码身份链。
