# 配置入口

- [experiments/host.json](experiments/host.json)、[experiments/controller.json](experiments/controller.json)：当前默认实验配置，原 configs/m4.1/；日常运行器默认使用此处。
- [sst/graph.py](sst/graph.py)：当前图搜索 SST 组件接线，原 m3.1/host.py；由运行器校验并传入参数，不是阶段废弃代码。
- `regression/`：持续维护的 SST 探针和分支夹具。CTest、完整 AC、分层验证继续使用，归档不减少执行范围。
- `native/`：锁定 native-small XML 与独立原生 oracle 输入，保留原路径。
- [archive/v0.1/](archive/v0.1/README.md)：旧阶段专用 smoke 配置，保留供对应归档脚本复现，不作为当前实验默认值。

配置移动未改参数值或 SST 组件行为。路径调整已同步 CMake、Python 入口、输入哈希记录与文档。回归配置的来源如下：

| 原任务 | 当前回归配置 |
| --- | --- |
| M0.3a | `regression/bridge_poc.py` |
| M1.1 / M1.2 / M1.3 | `regression/lifecycle.py` / `event_bridge.py` / `ordinary.py` |
| M2.2 / M2.3 | `regression/runtime.py` / `host_cost.py` |
| M3.2a / M3.2b / M3.3 | `regression/local_read.py` / `pcie.py` / `controller.py` |
| M3.1 / M3.4 分支夹具 | `regression/host-branch.json` / `placements-branch.json` |

T4 受控设备描述为 `profiles/devices.json`，共同基线为 `native/ssdconfig.xml`；根 CMake 和实验快照使用同一生成器。支持 native-small、ann-2g-v1、ann-16g-v1，均未校准；接口/容量/限制见 [T4](../docs/t4-content-profiles.md)。

T6矩阵规格为 [experiments/v0.2/sift1m.json](experiments/v0.2/sift1m.json)，由`tools/verify_t6.py`读取；外层包含实验模板，不直接作为run_config的单点配置。查询/预算继续引用M0冻结身份，实际26次运行与限制见 [T6收口](../docs/t6-closeout.md)。
