# V0.2 归档整理验证

2026-11-02（Asia/Shanghai）。本页只记录归档迁移验证，不是 V0.3 实施或验收。工作目录为仓库根，Python 使用锁定的 `/opt/homebrew/Caskroom/miniconda/base/bin/python3.13`。输入为本次工作区（保留开始时已有的 V0.3 文档改动）、原配置及现有 `build/delivery`/SST 缓存。

## 迁移与静态核查

- 17个文件迁移：10份历史文档、4份现行接口文档按功能重命名、2个历史工具、1个历史配置；另拆出 V0.2 专用交付说明，新增归档索引。
- 116个受保护文件逐字节 SHA256 核对通过，覆盖 src、tests、除索引外的配置、根 CMake、依赖锁、保留的离线建图器及 V0.3 IR 设计；归档配置按新路径核对。未改模型/参数/测试断言/依赖。
- 62个 Python 文件语法解析通过；10个准备、分层验收及归档 CLI 的 `--help` 均退出0。
- 初次扫描691处相对链接，无源码/文档目标缺失。81处指向未随检出提供的旧忽略证据，单列在报告中，不算通过证据，也未删除这些历史引用。最后文档整理后另做链接复查。
- V0.2 总入口的7份历史收口追溯路径已同步迁移，验证仍保留原始完整 AC 和严格复用规则。

## 实际运行

| 命令/输入 | 退出码与结果 | 本机日志 |
| --- | --- | --- |
| `cmake -S . -B build/delivery -DBUILD_TESTING=ON` | 0；复用已有锁定 SST 缓存 | `configure.log` |
| `cmake --build build/delivery -j2` | 0 | `build.log` |
| `ctest --test-dir build/delivery --output-on-failure` | 0；67/67通过 | `ctest.log` |
| 锁定 Python 运行 `tests/test_v02_delivery.py` | 0；3/3通过 | `delivery-unit.log` |
| 锁定 Python 运行 `tools/archive/v0.2/run_probes.py init --sizes 4096 --repetitions 1 --build build/delivery --output results/archive-v02-20261102/init-smoke` | 0；一页写入、容量、排空与正时间断言通过，验证迁移后配置可由SST加载 | `archived-init-smoke.log`、`init-smoke/` |

日志相对本机 `results/archive-v02-20261102/`。`before.json`、`initial-status.txt`、`moves.json` 记录迁移前身份与映射；`commands.json`、`report.json` 保存实际命令、退出码、静态断言与结果。最终链接/差异/子模块检查和文件身份记录在 `final.json`。这些是忽略的本机产物，新检出须重跑，不能只依赖本页结论。

本轮未运行完整 V0.1 AC、独立原生 oracle、ASan、T2～T6全量分层验证或完整 SIFT 矩阵；CTest 内 quick subset 不冒充完整验收。没有新的干净 SST 重建。V0.3 IR 尚未实施/验收，direct 参考冻结仍是后续开发工作。
