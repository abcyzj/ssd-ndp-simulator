# V04-1c 参考路径语义出口

需求来源为 [V0.4 PRD §4.2、FR-04-N01/V03、§8.3、AC-04-02](prd-v0.4.md) 和 [R 组矩阵](validation-matrix-v0.4.md)。依赖 V04-0、V04-1a、V04-1b。主配置使用锁定的完整 SIFT1M base、NSG 和 evaluation128，固定 k=10、L/E=128/256、B=1、chunk=16、单查询接纳。detail8 使用同一完整 base/图及前八个原始查询 ID。其他 L/E 的真实对照使用锁定 SIFT10k/32 子集。

## 运行与比较

原生入口加 `--semantic-log` 后，`batch.json` 增加实际评分分块（ID 和 FP64 距离）及扩展列表（末轮为空）。模拟配置显式设 `"semantic_log": true`，输出 `semantic.jsonl`。模拟侧在实际读请求提交、L2 求值、Host 控制服务和前沿决策处记录信息。Work 数组依次为 `elements, flops, read_bytes, write_bytes, output_bytes, candidate_checks, comparisons`。普通向量与邻接读记录逻辑 ID，盘控 L2 记录实际评分的 ID 及会话内 chunk 序号。模拟日志与原有输出共用字节预算，产物登记到运行清单。原生诊断在查询期间留存记录，结束后随批次报告输出，该模式的时钟读数仅作诊断。

对相同已验证 dataset-v2、index、truth 和搜索配置，用 `tools/run_native.py` 的单 worker/单接纳输出 `native/`，用 `tools/run_config.py` 分别输出 `direct-host/`、`ir-host/`、`direct-controller/`、`ir-controller/`，再运行：

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/compare_reference_paths.py GROUP --output REPORT.json
```

比较器校验产物和输入身份，以原始查询 ID 和运行序号关联，独立读取 FP32 行，以固定维序重算 FP64 L2，并用集合、排序和名次公式核对逐批候选、扩展、评分分块、逻辑读、终止、top-k 和全部七项 Work。主机与盘控分块分别按各自容量核对。严格比较为默认行为。只有显式 `--cross-toolchain` 才对距离使用绝对项 1e-9 加相对项 1e-7。本机出口使用严格比较。

同放置的 direct 与动态 IR 另用 `tools/compare_engines.py` 检查原成本的模拟时序、资源和流量。detail8 另启用 `event_log`，由 `tools/compare_placements.py` 独立核对请求、消息、字节、credit 与返回因果，再用原始查询 ID、语义摘要及实际 Work 对齐到 evaluation128。两放置的模拟时序各自保留。原生墙钟与模拟时间分别记录。

这些诊断记录的用途是核验参考路径。完整静态包、静态执行、CPU Profile、精度及 AC-04-02 的后续部分由对应任务验证。

## 本机验证与证据

2026-11-04（Asia/Shanghai）。V04-1c 已完成。证据目录为 `results/v0.4/semantic-v041c/run1/`。根清单 `manifest.json` 的 SHA256 为 `2c11cacc9a86d6816e6b2582227cda791056188aab97ea45bf14339fd362e9e6`，包含 1965 项产物。另有输入和依赖验证、源码/构建身份、运行配置、命令/退出码和实际日志。该目录由本机忽略，报告与重跑入口由本页索引。

| 实际检查 | 结果 |
| --- | --- |
| 根 CMake 配置、构建、CTest，原生仅构建、链接检查和原生夹具 | 全部退出 0，CTest 86/86。原生仅构建产物动态链接仅含系统 libc++ 和 libSystem |
| 完整 SIFT1M 主配置，原生及 R01～R04 | 128/128 查询逐批严格一致。共扩展 16,954 个节点、评分 398,498 个节点，Recall@10 为 0.9984375。同放置时序及资源账本严格一致 |
| 完整 base 的 detail8 | 两种引擎各自的 Host/Controller 独立账本通过。前八条原始 ID、语义摘要、Work 与主配置对齐 |
| SIFT10k/32 四点与合法向量扰动 | 四点均通过。扰动保留查询及邻接图，重绑真值身份。原始查询 0 的第二次扩展从 9494 改为 3748，距离及实际邻接读随之改变 |
| 边界、冻结与日志开关 | 两个三路径夹具、四组冻结复跑、四组诊断开关对照均通过。断连及其余数值边界继续由独立 CTest 覆盖 |

`semantic-report.json/md` 汇总出口，`comparisons/evaluation128-semantic.json` 按原始 ID 保存全评价摘要。`boundary-report.json`、`comparisons/perturbation-report.json`、`comparisons/detail8-association.json` 保存定向结果。R01 首跑的清单记录了新增 CTest 注册前的 CMake 文件哈希，因此被严格身份比较拒绝。首跑及差异保留，最终 R01 在新目录复跑后通过。冻结 IR 对照先核验各自的 run ID，再对其余事件字段逐条严格比较。

运行前重新校验锁定输入、16 GiB RAM、磁盘余量、输出及阶段超时预算。磁盘按 1 秒采样，逻辑文件大小峰值 6,897,762,806 B，卷余量最低 81,569,886,208 B。RSS 按进程高水位核对。跨工具链距离容差只做了比较器单测，本次全规模运行使用同一工具链。完整继承 AC 与 52 条深度回归本轮未执行。SSD、PCIe 与 Controller 保持 `configured_unvalidated`。
