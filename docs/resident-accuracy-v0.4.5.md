# P18 正式采集／拟合与驻留精度

日期：2026-11-07（Asia/Shanghai）。**P18 已通过第三轮正式驻留 CPU 精度验证。下文保留前两轮失败及第三轮证据。全部 AC 留待发布汇总。**

依据 [PRD FR02/08/09、§6](prd-v0.4.5.md)、[实施计划 P18、§6](implementation-plan-v0.4.5.md)、[P06 配方和质量要求](fit-v0.4.5.md)、[P15 三层核对](validator-v0.4.5.md)。三轮的授权变更及执行证据分别记录如下。

## 输入迁移与依赖

用户确认原 NSG／prepared 已删除，明确指示“我已经删除了，如有需要请重新构建”。该输入迁移例外已记入 PRD §6.1。以内容锁中的完整 SIFT1M、锁定 Faiss `e8234e563f1ecef5f036e83c3cfee366d3f1fbca`／OpenMP／建图参数和单线程重新构建；全部新证据绑定新身份，不宣称与删除前图逐位等价，不继承旧版精度。

本机证据目录：`results/v0.4.5/p18/run1/`，含授权、冻结日程、可复查编排、原始输出、命令／退出码、输入／源码／构建／依赖／产物 hash、预算和失败。依赖 P04/P06/P11/P14/P15 的 passed manifest 与 **312/148/322/2041/5283** 个登记产物 hash 均重新核验。它们是依赖实现证据，不是 P18 验收结果。

原始 base/query/learn/groundtruth 四文件全字节大小／SHA256 与内容锁匹配；完整 base、原始 tuning32/evaluation128 顺序及官方真值分别重新导入。新图包含 **1,000,000 节点、29,975,972 条边**，入口 **123742**，全节点可达，邻接顺序／重复边／自环保留。图 SHA256：`440d281c378e044aa53f6943e9dee274854af33b109f2725b270a9f8c5ce4142`。建图命令成功，含准备／校验约962.51秒，实际 builder 子进程943.78秒、峰值4,031,299,584 bytes，低于8 GiB／8小时门槛。receipt、完整参数和节点映射随证据保存。

## 实现修改

- [独立 oracle](../workloads/graph-search/python/graph_oracle.py)改为按需定长 `pread`，替代跨查询长期保留完整 payload 的 mmap 驻留页；固定维序 FP64、搜索／排序／Work／依赖核对不变，增加越界／截断拒绝测试。
- [采集入口](../workloads/graph-search/python/graph_capture.py)在 RSS 拒绝前保存资源实测值；[测试](../workloads/graph-search/tests/test_capture_v2.py)验证预算失败不发布包且保留资源。
- [拟合器](../python/ndpsim/fitting.py)失败时输出 `failure-report.json`，保留输入身份、已完成质量核对及可用的阶段／四折覆盖／误差；有效 Profile 和成功 fit report 仍拒绝发布。[新增负例](../tests/test_v045_fitting.py)证明准确率失败不能生成可用 Profile。
- 同步拟合／采集使用说明、迁移断言、PRD 输入例外与任务状态。没有修改 C++、SST、MQSim、配方、模型特征或质量阈值，没有提交、推送或初始化 Git。

128/256 首轮在独立语义核对完成后触发512 MiB后处理 RSS 门槛，保存为 `tuning-128-256-capture/package.failed`，没有编辑 hash 使其通过。修复后在新目录重采：完整包身份仍为 `1445ed99105fa3117e9f3c720e5b971fbb1c80458209a8184ddd7dde0c265d76`，完整语义重新核对通过，后处理峰值降至 **78,610,432 bytes（75.0 MiB）**；256/512捕获峰值97,763,328 bytes，最大训练采样峰值378,306,560 bytes，均在512 MiB内。原生二进制／CMakeCache hash 完全未变；原有成功包另用新 oracle 全量复核。

## 冻结日程与实际执行

执行前冻结 `schedule.json`、既有 graph recipe、预算、原始 ID 和1/1/1配额。A=graph_uninstrumented、B=graph_benchmark，tuning32的32/64点三组A/B/A共9次，窗口为首接纳至最终可见，扰动为 `median(abs(B-mean(A_before,A_after))/mean(A_before,A_after))`，使用原生QPS。实测 **0.279834% ≤5%**。此项只核验查询级CPU探针扰动，不宣称逐阶段采样／IR序列化墙钟开销≤5%，也不替代evaluation独立benchmark。

| tuning32 L/E | 完整操作 | 完整阶段 | 独立训练采样stride | 覆盖样本 |
| --- | ---: | ---: | ---: | ---: |
| 16/32 | 5,856 | 10,435 | 1 | 8,279 |
| 32/64 | 9,760 | 16,827 | 8 | 1,787 |
| 64/128 | 17,808 | 29,755 | 8 | 3,031 |
| 128/256 | 34,053 | 54,470 | 8 | 5,372 |
| 256/512 | 66,730 | 102,232 | 8 | 9,805 |

合计 **134,207 操作、213,719 阶段、28,274 训练样本**，五点均为完整1M×128、32个原始tuning查询，全部独立语义／Work核对通过。表中阶段不是不同原始查询：始终只使用同一tuning32，不把160个query-point冒充evaluation128。32/64、64/128另各三次stride1独立制表采样，分别每次13,207和23,117调用，总计108,972调用；与benchmark和训练采样分别运行。新二进制合成pilot完整采集／29,031样本覆盖通过，74个低信号保留，只用于工具和资源投影。

每次适配器运行重新预检RAM／磁盘并核验输入／构建身份，记录native wait4与Python峰值、超时、输出预算。实测物理RAM17,179,869,184 bytes；任务32 GiB、全版聚合48 GiB、每次输出2 GiB及完成投影后20 GiB磁盘余量门槛保留。初始全日程元数据投影约6.82 GB，另核对即使本任务用满32 GiB，原始SIFT＋其他本版证据＋任务总量上界仍约35.93 GB，小于48 GiB。预算是实测失败门控，不是硬RSS／进程树隔离。

## 未通过结果与可达性诊断

统一 CLI 正式训练：

```sh
PYTHONPATH="$PWD/python" /opt/homebrew/Caskroom/miniconda/base/bin/python3.13 -m ndpsim fit \
  --manifest results/v0.4.5/p18/run1/training.json \
  --budget results/v0.4.5/p18/run1/fit-budget.json \
  --output results/v0.4.5/p18/run1/fit-bounded
```

实际退出 **2**，133.65秒，拟合子进程峰值 **446,136,320 bytes（425.5 MiB）**，RAM／磁盘／超时预算未触发；五点测量质量与绑定检查已完成，随后在 **graph.INIT/2:init** 的四折留出误差门槛失败：160/160域内、P50 **30.7229%**、P90 **75.00%**，门槛为10%／20%。完整折ID、范围和误差保存于 `fit-bounded/fit/failure-report.json`。未生成有效Profile或成功fit report，未冻结模型；不能推断其他预测阶段已通过。

独立区间重叠诊断 `init-identifiability.json` 只使用tuning样本，不拟合新模型、不访问评价数据。INIT全部特征都是copied_bytes=512，其合格测量81～2832 ns。即使每折可用留出标签选择最有利的整数常数，最多 **107/160** 样本落在±20%内，少于P90所需 **144/160**。因此在本批测量／当前特征下，仅换常数或调整当前公式参数不能使P90通过；诊断不证明噪声或分配／准备等变化的唯一成因。需要版本化测量／适用条件或特征方案，重新冻结、采样与训练；不能删掉困难调用、把INIT设0、放宽误差或将实测表冒充预测模型。

六轮制表采样的merge_append低信号调用数为 **6/3/3（32/64）、6/2/12（64/128）**。按P06“表调用每次都须合格”规则均不具备发布资格，所有稳定键保留于 `table-call-quality.json`。这只是原始表调用质量诊断，没有绕过冻结Profile／完整绑定门控发布表。

**剩余未执行**：evaluation128两点新包及每点三次cpu_sample、每点三次独立benchmark、A01/A02各两次确定性模拟、四切分点Level A全部计费核对及正式CPU精度比较。评价前必须冻结预测模型，当前训练失败不能继续生成这些依赖证据。AC01/04/08及总AC均不计通过，任务清单保留P18未完成。

## 实际测试与检查

- 锁定离线工具重新配置／构建、完整两切分import／官方truth绑定、全图结构／可达性核对成功。
- 独立图工程新配置／构建及 **4/4 CTest**；oracle修复后再跑4/4，采集测试含12项方法，覆盖定长读取、越界／截断、原有语义篡改负例和预算失败资源记录。
- 拟合定向 **17/17**，含独立C++消费探针、失败留出误差保留和不发布Profile。
- 根 `cmake -S . -B build/delivery -DBUILD_TESTING=ON`、`cmake --build build/delivery -j2` 成功。首轮CTest **138/140**；两项CLI／Validator因沙箱禁止只读 `sysctl hw.memsize` 失败，未更改测试，正常主机权限定向重跑 **2/2** 成功。保留首轮日志，不写作一次140/140。
- oracle最终修复后受影响测试分两组重跑 **3/3＋2/2**：graph_capture_v2、unified_cli_v045、layered_validator_v045、sst_memory_timing_v045、graph_ssd_v045，全通过；这些夹具不算开始P19正式任务。
- P00基线门控 **3/3**、P01契约 **11/11**、迁移审计 **415文件／140 CTest** 通过；最终身份审计和文档／hash检查随收口记录保存。
- 两个tuning正式点的P15 capture层另运行统一CLI `verify`，均退出0、采集层通过，结果见本轮 `validation-commands.json` 和 `verify-capture-*/report.json`；该层不证明预测精度或完整AC。

实际命令／退出码分别在commands、prepare-commands、formal-commands、bounded-commands、regression-commands、validation-commands及各CLI invocation／command中，编排保存为本目录reproduce-*.py。拟合与新oracle均没有C++改动，未新增ASan运行。MQSim fork、SST源码与commit不变，MQSim相对锁定原生diff SHA256仍为 `f38fc06069ee252ebbf83cdc2e566768d74a255fbb2ff1980ba76b0f2a7ec044`。

只有本机macOS arm64被实测，频率Unsupported、亲和性uncontrolled；新图不是旧图等价证明。哈希用于来源关联，不代替独立功能核对。未校准真实SSD／PCIe／Controller，仍为configured_unvalidated，不推断下沉更快。本轮到P18未通过状态收口，不启动P19，不将成功的小夹具／训练采集记为正式驻留精度通过。

## 第二轮复用与配方二版

用户授权按新规则探索。第二轮核对并复用 run1 的 1,524 项登记产物，包括新图、独立原生二进制、五点完整 tuning32 IR、28,274 个训练样本、六轮逐调用采样和 A/B/A 证据。第二轮的输入、命令、失败日志和结果保存在 `results/v0.4.5/p18/run2/`。本节更新当前状态，前文保留第一轮记录。

[配方二版](../extensions/graph/fit-recipe-v2.json)把 INIT 纳入逐调用表。FINISH 和两个准备阶段使用从同查询较早拓扑阶段累计得到的 scored／expanded 计数。拟合、SST 和独立 Validator 分别读取同一 IR 元数据。原生二进制、原始 Work、采样和包身份沿用 run1。

五点拟合执行了 25 个候选配置的四折评估。候选选择采用 tuning 分组留出，指标用于探索。INIT 与 merge_append 单列为表来源。正式拟合退出 2，峰值约 431 MiB。

| 阶段 | 最佳 P50 | 最佳 P90 | 训练门槛 |
| --- | ---: | ---: | --- |
| FINISH | 2.88% | 7.80% | 通过 |
| score_prepare | 7.64% | 23.01% | 未通过 |
| adjacency_prepare | 26.90% | 64.73% | 未通过 |

其余四个预测阶段通过训练门槛。两项准备阶段仍阻塞 Profile 冻结，因此 evaluation128 和 A01/A02 保持待执行。最终表检查优化仅影响制表模式的样本保留及 SQLite 临时文件清单。正式拟合时的源码已按报告 hash 保存，拟合路径和候选统计沿用该次实际执行。

INIT 和 merge_append 的低信号样本分别比较零估计与 1666 ns／420 ns 替代情景。替代值取 tuning 合格样本 P99 与十倍计时分辨率中的较大值。每轮先保留合格正值并按情景替代低信号，再取三轮下中位数。训练阶段覆盖和 5% 低信号比例门槛继续适用。四个 `table-check` 最终退出 0，合计覆盖六轮原始采样的 108,972 个调用。64/128 首轮超过 512 MiB，保留失败后减少制表用不到的预测样本驻留，最终峰值约 369 MiB。前后表行时长一致。

| L/E | 表调用 | 最终零估计行 | 替代情景总服务增量 | 最大单查询增量／观测 CPU 桶 |
| --- | ---: | ---: | ---: | ---: |
| 32/64 | 2,797 | 1 | 632 ns | 0.00712% |
| 64/128 | 4,759 | 2 | 1,299 ns | 0.00180% |

该检查比较训练侧表服务需求与已有合格采样桶。端到端延迟、QPS 与 CPU 精度仍需完整 Profile 后核验。表检查仅发布候选行。本轮维持 P18 未完成及 AC01/04/08 待验收状态。

根回归 140/140 通过。最终表内存优化后，拟合、内存 SST、统一 CLI 和 Validator 四项定向 CTest 再次通过，其中拟合 20 项用例。测试覆盖真实 SST 的前缀计数、物理重排、篡改拒绝，以及低信号零值和正值的独立 C++ 消费。

定向 ASan 3/3 通过，覆盖成本模型、拟合 Profile C++ 消费和内存计划。迁移清单审计覆盖 416 个文件和 140 项 CTest。

## 第三轮逐调用表与正式评价

按用户授权冻结 [graph-resident-cpu/3](../extensions/graph/fit-recipe-v3.json)。`score_prepare` 与 `adjacency_prepare` 改用逐调用实测表，沿用 INIT、merge_append 的三轮独立采样、稳定键和 Work 绑定、下中位数聚合与低信号规则。每轮每阶段低信号比例上限为 5%，保留原始测量。零情景用零估计合格低信号；参考情景用训练侧冻结值，按合格样本 P99 与十个时钟分辨单位的较大值取 2707 ns（score_prepare）、791 ns（adjacency_prepare）、1666 ns（INIT）和 420 ns（merge_append）。两情景端到端相对差门槛为 2%，训练冻结后才接触 evaluation128。

本机 `results/v0.4.5/p18/run3/` 保存第三轮输入、产物和命令。复用前两轮 1,755 项已登记证据、原生二进制、新图身份、五点 28,274 条训练样本、两点各三轮完整制表采样和 9 次 A/B/A。正式拟合通过，五个预测模型的参数与二版一致。两点两情景的四份训练账本和敏感性均通过。64/128 训练侧账本验证器的 512 MiB 上限不足，保留失败后以 1 GiB 仅复跑验证器。

evaluation128 两点各独立执行一次完整 IR 采集、三轮 CPU 采样和三轮无探针 benchmark，原始查询分别覆盖 68,225 和 119,519 个阶段。每点执行两次零情景 SST 与一次参考情景 SST。大点按训练结果外推分别为采集后处理／拟合／模拟设置 2 GiB、独立验证器设置 4 GiB；检查物理内存、磁盘余量及输出预算。A02 两次零情景 SST 原进程均以 0 退出，报告器遇到约 25.17 MiB 的首条配置快照超过原 16 MiB 单记录上限。报告器和验证器现在共用有界 trace 读取规则，首条配置允许 64 MiB，其余事件仍为 16 MiB。原失败目录保留，报告按原命令、配置、输入和 trace 的 hash 在新目录重建，沿用原 SST 执行。新增大快照与事件限制回归，`unified_cli_v045`、`layered_validator_v045` 通过。

| 正式点 | CPU 总服务需求误差 | 延迟 P50 误差 | 延迟 P95 误差 | QPS 误差 | 实测表占 CPU 需求 | 两情景最大逐查询延迟差 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A01（32/64） | 0.77% | 0.63% | 0.50% | 0.72% | 3.44% | 0.00756% |
| A02（64/128） | 0.54% | 0.60% | 0.35% | 0.52% | 1.66% | 0.00781% |

两点各五个预测阶段均通过 P50 ≤10%、P90 ≤20%。两点的原生对照、完整 IR 语义与 Work、确定性、CPU 来源分列及唯一计费、按原始测量独立重建实测表均通过。完整报告、执行与回归命令、恢复前后 trace 绑定和产物身份见本机 run3/summary.json 和 run3/manifest.json。

精度仅覆盖记录中的 macOS arm64、重建图及单 worker Host 内存驻留两点。逐调用表依赖对应轨迹的独立采样和匹配的身份及 Work，新轨迹需重新制表。未校准真实 SSD、PCIe 或 Controller。P19～P21 尚未开始。
