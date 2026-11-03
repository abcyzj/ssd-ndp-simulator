# V0.3 工具归档索引

这里收拢历史阶段说明，工具实现仍在 `tools/` 原路径。它们被 CTest、工具间导入和 V0.4 继承回归使用，没有可仅凭版本名安全移除的一次性 V0.3 脚本。

| 原路径（仓库根目录执行） | 继承用途 |
| --- | --- |
| `tools/freeze_direct_baseline.py` | direct 冻结及独立身份、预算核对；也提供共用核验函数 |
| `tools/verify_ir_mapping.py` | 映射、MQSim、边界与定向 ASan |
| `tools/verify_ir_frontend.py` | 四组合、因果、扰动、冻结参考和定向 ASan |
| `tools/verify_ir_observation.py` | 日志、严格比较、独立账本和冻结核对 |
| `tools/verify_ir_matrix.py` | 完整52条、三组交替效率及Q32/128/512梯度；不支持14配置缩减 |
| `tools/verify_v03.py --all` | 完整V0.3交付门槛、继承AC与默认IR核对；无quick出口 |
| `tools/verify_delivery_docs.py` | 正式交付文档链接及 Word 依赖检查；随归档更新计划路径 |

实际复现命令、锁定 Python、RAM证据和预算见[V0.3 发布记录](../../../docs/releases/v0.3.md)、[矩阵收口](../../../docs/archive/v0.3/ir-matrix.md)。全部相应断言保留。V0.4 常规14配置不替代这些工具的完整52条门槛，触发规则见[新矩阵](../../../docs/validation-matrix-v0.4.md)。

`generate_ir_schema.py`、`compare_engines.py`、`ir_frontend.py`、`stream_statistics.py` 和 `bench_host_cpu.cc` 仍为现行生成、核对、执行或诊断工具，详见[工具入口](../../README.md)。归档不把已有粗测视为 V0.4 Profiler。
