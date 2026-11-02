# V0.2 历史归档

2026-11-02（Asia/Shanghai）整理，为 V0.3 开发收拢阶段上下文。V0.2 T0～T6 与 T8/M4 已结束，T7/M3（Deep10M）已取消。这里的“当前”“下一步”“未实现”和通过状态仅指记录当时，不是新版本待办。当前需求见[V0.3 PRD](../../prd-v0.3.md)与[IR 设计](../../ir-v0.3.md)，运行基线见[V0.2 交付记录](../../releases/v0.2.md)。

## 阶段与实验材料

| 归档 | 原位置/内容 |
| --- | --- |
| [PRD](prd.md) | `docs/prd-v0.2.md`；继承需求与验收基线 |
| [实施计划](implementation-plan.md) | `docs/v0.2-implementation-plan.md`；历史任务和门槛 |
| [M0/T0](m0-closeout.md) | `docs/m0-closeout.md`；依赖/预算/可行性 |
| [T1](t1-closeout.md)、[T2](t2-closeout.md) | `docs/t1-closeout.md`、`docs/t2-closeout.md`；数据与图/真值收口 |
| [T3](t3-closeout.md)、[T4](t4-closeout.md) | `docs/t3-closeout.md`、`docs/t4-closeout.md`；闭环与内容/profile |
| [T5](t5-closeout.md)、[T6](t6-closeout.md) | `docs/t5-closeout.md`、`docs/t6-closeout.md`；观测/回收与完整SIFT |
| [算力敏感性实验](t6-compute-ratio.md) | `docs/t6-compute-ratio.md`；补充扫描记录 |
| [V0.2 交付复现](delivery.md) | 从 `docs/delivery.md` 拆出的版本专用命令与复用规则 |

## 仍在维护的接口和基线

| 原文档 | 现行入口 |
| --- | --- |
| `docs/t3-closed-loop.md` | [仿真输入](../../simulation-input.md) |
| `docs/t4-content-profiles.md` | [内容与设备 profile](../../content-profiles.md) |
| `docs/t5-observation-lifecycle.md` | [观测与生命周期](../../observation-lifecycle.md) |
| `docs/t6-sift1m.md` | [SIFT1M 准备与固定矩阵](../../sift1m.md) |

按功能重命名上述接口，修正其中已过时的阶段状态；模型和接口行为不变。数据/图/真值、契约、搜索、资源、输出与限制仍在[现行索引](../../README.md)。V0.2 PRD 移入本目录仍是可追溯的继承基线。

[工具归档](../../../tools/archive/v0.2/README.md)和[配置归档](../../../configs/archive/v0.2/README.md)仅收纳一次性探针。有效 CTest、独立核对器、T2～T6 验证工具、V0.2 总入口、正式离线建图器继续保留。带 V0.2 文件名的查询/预算/矩阵继续按原路径和字节保存，避免破坏锁及准备产物身份。

## 证据保全与验证边界

原始 `.cache/`、`results/`、`build/`、`docs/evidence/` 不搬迁、不删除、不改写。历史 manifest 内的命令、绝对路径和哈希保持原样；归档文档中的复跑路径已更新。符号链接复用依赖原目录及其目标，新检出不保证拥有这些本机忽略产物。

归档导致文档及相关工具文件身份变化，不修改成功缓存判定来兼容旧结果；复用仍需原有严格核验。V0.3 必须重新冻结本轮 direct 证据，历史通过不充当新版本验收。本次也不实施 IR、不修改 C++/模型/依赖或自动提交。

归档验证记录见[整理验证](archive-checks.md)。
