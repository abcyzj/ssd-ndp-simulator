# 配置入口

V0.3 兼容配置继续继承现有模型、数据和查询身份；省略 `execution_engine` 默认 IR，显式 `direct` 保留对照。V0.4 的 `replay`、`cpu_profile`、`data_provider=host_memory` 和 worker 策略须显式启用，实际字段见 [静态回放](../docs/replay-v0.4.md) 和 [CPU Profile](../docs/cpu-profile-v0.4.md)。总验收按 [12配置](../docs/validation-matrix-v0.4.md) 生成逐次有效配置，见 [交付入口](../docs/releases/v0.4.md)。版本名标识冻结来源，不表示已失效；不为归档重排查询、改参数或重算锁。

| 分类 | 现行用途 |
| --- | --- |
| [experiments/host.json](experiments/host.json)、[experiments/controller.json](experiments/controller.json) | 默认日常实验配置 |
| [sst/graph.py](sst/graph.py) | 实际图搜索 SST 组件接线，运行器校验并传参 |
| `regression/` | CTest、完整 AC 和持续分层回归探针/分支夹具 |
| `native/` | 锁定 native-small XML 与独立原生 oracle 输入 |
| [profiles/devices.json](profiles/devices.json) | native-small、ann-2g-v1、ann-16g-v1 设备描述；根构建与运行器共用 |
| [benchmarks/datasets.lock.json](benchmarks/datasets.lock.json)、[建图锁](benchmarks/m0-builder.lock.json) | 原始数据身份、离线 Faiss 与工具链锁 |
| [查询锁](benchmarks/v0.2-query-selection.json)、[预算](benchmarks/v0.2-budgets.json) | V0.3 继承的查询分割、搜索与资源基线；路径/内容保持冻结 |
| [experiments/v0.2/sift1m.json](experiments/v0.2/sift1m.json) | `verify_t6.py` 使用的固定矩阵，外层含实验模板，不能直接作为 run_config 单点配置 |

内容后端与容量见[内容/profile](../docs/content-profiles.md)，完整准备与26次基线矩阵见[SIFT1M](../docs/sift1m.md)。V0.3 已完成双引擎矩阵，证据见[交付记录](../docs/releases/v0.3.md)；冻结配置与继承用途见[V0.3 配置索引](archive/v0.3/README.md)。

## 历史配置

- [archive/v0.2/](archive/v0.2/README.md)：仅由历史 M0 工具调用的初始化可行性探针。
- [archive/v0.1/](archive/v0.1/README.md)：旧阶段 smoke 配置；低层 CLI 仍可复用默认值。

归档不改变模型参数或删除持续回归。原有回归配置中的 bridge/lifecycle/event_bridge/ordinary/runtime/host_cost/local_read/pcie/controller 和分支夹具均保留。
