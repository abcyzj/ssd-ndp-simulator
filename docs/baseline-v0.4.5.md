# P00 基线与迁移清单

日期：2026-11-06（Asia/Shanghai）。状态：**P00 完成，P01～P21 未开始，AC-045-01～12 均未验收**。依据 [PRD FR11/12、AC10/12](prd-v0.4.5.md)、[实施计划 P00、§6～8](implementation-plan-v0.4.5.md)及 [SA-01/15/16](source-audit-v0.4.5.md)。P00 无前置任务；本次只建立迁移依据和小夹具参考，没有实施新执行契约、迁移生产路径或删除旧文件。

## 交付清单

| 产物 | 覆盖和使用方法 |
| --- | --- |
| [逐文件清单](migration-v0.4.5/files.json) | 262 个普通文件：src/tools/tests/configs/schemas/examples（含归档）及根 CMake、依赖锁和 gitmodules。每项给出处置、理由、后续负责任务、替代断言、删除门槛。以实际 Git 可见清单核对，不按版本名批量删测试。 |
| [逐 CTest 清单](migration-v0.4.5/tests.json) | 当前正常根构建 93 项，逐项保存规范化命令、来源文件、属性、处置和替代出口；另列未注册的独立 Python 测试。ASan 条件注册仍以 CMake 为准，定向运行属 P20，不冒充本次通过。 |
| [正式矩阵](migration-v0.4.5/matrix.json) | 6 配置，至少 8 次模拟；两点各 3 次独立 benchmark；4 个新包、每包至少 3 次 cpu_sample；五点 tuning32 训练、四折原始 ID 分组、三组 A/B/A 和全部精度门槛。明确模型与实测表分列。 |
| [预算计划](migration-v0.4.5/budget.json) | 保留保守单进程、超时和磁盘余量门槛，明确本版输出范围、pilot 投影及逐次重新预检。不是未来全规模运行许可或可行性证明。 |
| [审计工具](../tools/audit_v045_baseline.py) | 对实际 Git 文件和 CTest 名称/命令核对完整覆盖；验证矩阵、重复、规模及查询切分；输出静态 Python import、C++ include、字面路径引用和动态调用复核位置。 |
| [门控测试](../tests/test_v045_baseline.py) | 缺文件/测试、重复、缺删除门槛、减配置/重复、改变查询顺序和严格 worker 的负例；作为开发审计独立运行，不改变旧根 CMake 或原生来源身份。 |

清单记录的是**待执行处置**，不表示任何迁移出口已通过。P20 删除前必须逐项核对替代测试的实际证据，并清理所有入向引用。引用图是保守的静态审查输入，不能覆盖计算路径、全部动态 import 或 CMake 生成表达式；P20 还须复核实际 import/链接、生产配置和公开文档，并执行清理后构建、当前 CTest 和定向 ASan。功能/数值/搜索/Work 独立核对迁原生，不能因 timing-only 而删除。

SSD offered/admission/worker/slots 与可比资源条件按计划留待 P01 冻结；矩阵已经锁定两点、切分、语义和最低次数。本次不替 P01 决定新接口或调度策略。

## 实际参考身份与输入

运行开始主仓 HEAD：`b309c2597afbbc341d9b77d87e8c94a6788320e5`。实际工作区逐文件 hash、状态及 diff 保存在 `source-start.json`、`source-end.json`、`workspace.patch`；新审计工具及测试也纳入身份。运行期间源码完全一致，收口后仅同步文档。

MQSim fork：`8f7ddf6d8cafdb7c783b3fcfd4693f15c6c5854b`；锁定原生 oracle：`51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16`。相对原生完整 diff SHA256：`f38fc06069ee252ebbf83cdc2e566768d74a255fbb2ff1980ba76b0f2a7ec044`。SST：`ffd71527bc26a1f2a4fb685fdeb2ca0799997d78`；相对锁定版本 diff 为空。两个子模块干净且运行前后未变；实际源码逐文件 hash 和 diff 保存于 `dependencies.json` 及 patch。未提交、推送、升级或初始化 Git。

原始输入实际位于 `.cache/v0.2-m0/data/sift/`。base/query/learn/groundtruth 四个文件全字节 SHA256 和大小均与 [内容锁](../configs/benchmarks/datasets.lock.json)一致；本次没有重新扫描每个数值或进行全量采集。tuning32/evaluation128 原始 ID/顺序取自 [查询锁](../configs/benchmarks/v0.2-query-selection.json)，已复制到正式矩阵并验证不相交。

本机有 `sift.tar.gz`、锁定 Faiss/OpenMP/CMake 归档，但旧文档所列的 `results/v0.2-t6/preparation/prepared`、`results/v0.2-t2/builder`、`results/v0.4/start-v040/final1`、`results/v0.4/delivery-v045c/run2`、`results/v0.3/direct-baseline-final` **均缺失**。旧发布记录及其 manifest hash 只作来源，不能检查不存在的产物或继承其通过。完整原 NSG、prepared 和 builder receipt 尚未重新取得，阻塞后续正式输入验收/采集和 P18/P19；不阻塞 P00 的清单、原始输入身份和小夹具参考，也不能降低正式规模绕过。

恢复步骤保留于 [SIFT1M](sift1m.md)及 [建图/真值](index-truth-v2.md)：核验本地锁定归档，独立构建 builder 取得新 receipt，按锁定原始 ID 导入完整 base/query、生成/校验图及绑定官方真值，逐文件核验 prepared。归档本次只确认存在，重建前还须核验其 hash。多线程重建 NSG 不承诺逐位相同；恢复原锁定图之前，禁止静默以新图替代正式负载。若无法恢复，须明确处理身份/范围差异，再开展正式任务。

缺失的小夹具参考已在本次新目录补齐：保存同一合成数据的 direct/ir × Host/Controller 配置、真实输入、top-k、完整统计、events/service、manifest 以及核对报告；另保存独立原生 fixture、原始报告和 Python oracle 期望值。它们是**本次 V0.4 代码参考**，没有与缺失的历史产物比较，不宣称历史逐位等价或新版本迁移等价。旧静态路径的契约/延迟/失败断言另由本次根 CTest 检查。

## 实际检查与证据

本机证据：`results/v0.4.5/p00/run1/`。`reproduce.py` 保存本次编排，`commands.json` 和逐命令 JSON/log 保存命令、退出码、墙钟、RSS 与原始输出；`audit/` 保存引用图、原始 CTest JSON、源码/计划 hash。构建身份包括实际二进制、全部目标 flags、CMakeCache、SST 程序及缓存 receipt；使用原有 SST 缓存，未声称干净操作系统复建。

实际执行以下检查，全部退出 0：

```sh
cmake -S . -B build/delivery -DBUILD_TESTING=ON
cmake --build build/delivery -j2
ctest --test-dir build/delivery --output-on-failure
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tests/test_v045_baseline.py
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/audit_v045_baseline.py \
  --build build/delivery --output results/v0.4.5/p00/run1/audit
```

- 根 CTest **93/93**，42.03 秒。包含旧静态回放、原生采集、独立搜索与资源因果断言；此数字只说明本次 V0.4 基线健康。
- P00 门控 **3 个测试方法全部通过**，包含文件/测试清单缺失及矩阵缩减等负例；审计覆盖 **262 文件、93 CTest**。
- `run_config.py` 四次运行（direct/ir × Host/Controller），共用第一次生成的固定 seed 数据；全部保存新目录。
- `compare_placements.py` 两次：每个引擎的两放置独立搜索/流量核对通过；`compare_engines.py` 两次：每个放置的结果、模拟统计和账本严格比较通过。
- `ndpsim_native` 小夹具 1 worker/1 admission，11 查询，保存真实输入/输出；独立 Python oracle 核对 top-k/距离、Work、计数和接纳通过。
- 原始 SIFT 四文件大小/完整 hash、运行前后源码/依赖不变、`git diff --check` 均通过。收口文档另行核对链接、完整证据 hash 和差异，不重跑未变的生产二进制。

没有执行全量采集、拟合、正式 CPU 精度、SSD 四点、独立 MQSim 完整新对拍或本版 12 AC。根 CTest 中的 quick/旧版门控仅作基线，不计正式通过。设备、PCIe、Controller 仍 `configured_unvalidated`。

## 资源与后续门槛

本轮 `memory_pressure -Q` 实测物理 RAM **17,179,869,184 bytes（16 GiB）**、当时空闲比例 52%；卷余量约 **98.76 GiB**。`sysctl` 在受限进程中退出 1，原始拒绝和可用的 `memory_pressure`、`vm_stat` 输出均保留，不借用历史 RAM 数字。频率/亲和性及新 CPU 校准未核验。

预算计划采用进程 RSS≤4 GiB 且≤新取物理 RAM 的60%、后处理≤512 MiB；初始化/查询/后处理超时分别3600/7200/600秒；建图≤8 GiB/8小时且受60%约束；单次输出≤2 GiB、本任务输出≤32 GiB，原始 SIFT+共享 prepared+全部 V0.4.5（含失败）逻辑量≤48 GiB，投影完成后卷余量≥20 GiB。新的32/48 GiB范围为留存本版新包、重复与失败而单列，不代表已测得未来输出会满足预算。

每次全规模操作前重新取得输入/构建/配置身份、RAM/压力/缺页和磁盘，先用 pilot 测量每操作/阶段/查询的输出和活跃元数据峰值，再按完整日程投影。数据、模型或参考缺失，投影不足或资源越限时拒绝运行，保留失败目录并新目录重跑。当前没有新格式 pilot，不能推断正式全规模可行。RSS/逻辑文件大小测量不等于硬隔离、瞬时进程树峰值或实际块占用。
