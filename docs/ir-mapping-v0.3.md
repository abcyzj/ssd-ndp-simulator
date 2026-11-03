# V03-3 算子与 placement 映射

2026-11-03（Asia/Shanghai）。V03-1、V03-2 的成功 manifest 与全部登记产物 hash 本轮重新核对通过。**V03-3 已完成；V03-4 未开始。**本轮提供真实算子后端和小规模测试驱动，不接入生产 GraphSearch、不增加 engine 配置、不切换 direct 默认，也不代表 V0.3 总验收通过。

## 接口与映射

[Mapping](../src/ir/mapping.h) 是受信后端边界，持有 Executor、Runtime、HostCpu、ContentStore、Layout、普通读取能力和可选 Controller。构造时校验空 run contract、placement/backend 和逻辑集合绑定的 kind/version；实际输入文件及集合内容身份仍由现有受信输入准备/打开流程负责。前端只能提交类型化 Operation 并消费 lease 的可见输出，不获得 ContentStore 或这些服务能力。

`admit(query, fp32_vector, initial_snapshot)` 在原接纳时刻登记初始主机 query/state；controller placement 在此调用一次原 Open，并由 Controller 持有其依赖。`submit(op, producers, snapshots)` 复用 Executor 的增量依赖、作用域、实际地址和 shape 校验。未知/关闭的 query、重复 QUERY_READY 或尚未 QUERY_READY 的操作明确拒绝；提交错误关联 query/op/time。`close(query)` 禁止新操作，独立持有当前活动 lease 并逐项等待，随后执行原 Controller Close/Ack；Open 失败已清理会话时保留失败根因并清理映射会话。操作完成即释放活动关联，不积累完整查询历史。

| 算子 | 实际映射与唯一计费边界 |
| --- | --- |
| QUERY_READY | 发布已接纳的 FP32 query、初始不可变状态和 ready；Runtime 异步完成，无新增目标服务。上传不是 QUERY_READY 的免费结果，仍走真实 Open/OpenAck |
| READ_ADJACENCY | 整批预检 metadata/范围/Layout；依输入顺序逐节点调用原普通读取能力。所有子句柄完成后解码 degree 与有效 IDs，才发布邻接批次；DMA/CQ 仍由原读取路径门控 |
| SCORE_L2 / host | 整批预检并拒绝重复 ID；按原 search.chunk 逐块依次提交向量普通读及一次 HostCpu 距离服务。不先等待所有块读取；原固定维序 FP64 平方 L2、每项 12B 得分和 Work 不变，整批按输入顺序汇合 |
| SCORE_L2 / controller | query 必须与已接纳 query 相同；直接调用原 Controller::score，继承容量决定的 chunk、本地读取、sector credit、staging/compute、ScoreResult 和真实 PCIe 返回。无普通向量回传；不重复上传 query |
| MERGE_CANDIDATES | 原 candidates 函数，输入为完整旧得分及完整评分批次，limit 为 base count；一次 HostCpu Control 服务 |
| SELECT_FRONTIER | 原前 L 扫描、B/E/expanded 判定及 candidate_checks；一次 HostCpu Control 服务，空 IDs 由前端作为停止判据，不新增 LOOP_CHECK 服务 |
| DEDUP_NEIGHBORS | 原逐边 check、scored 线性比较和有序插入规则；一次 HostCpu Control 服务，保持 comparisons 及原逻辑字节口径 |
| JOIN | 复用 Executor::join，异步完成，无目标服务；也纳入查询活动操作与 Close 等待 |

控制算子的功能执行发生在原 HostCpu 获准后，真实 Work 只产生一次。新增 `HostCpu::operation` 工厂返回原 Runtime 操作描述；`HostCpu::submit` 仍提交相同描述。IR 父控制任务直接复用其 grant/evaluate/service，避免等输入可见后才创建另一收费任务造成 submitted_ns 改变。setup/rate、FIFO、slots、计费公式和 EventBridge 四 phase/delta 均未更改。复合读/评分的子阶段保留在原服务，父任务仅作完成门控，不叠加费用。

Executor 的受信 Adapter 增加可选 `launch(task, resolved_values)`，只在全量动态地址校验之后调用。它允许真实服务获得已验证输入，不向前端开放任意功能计算。可选 `Mapping::Bind(parent_op, runtime_child)` 关联映射直接创建的任务；不默认存储全历史，也不等于 V03-5 的 request/transaction/message 全链路日志。

复合批次逐个观察子任务，失败也等待已发出工作排空。原 Controller::score 的批次依赖可能在首个 chunk 失败时就失败，而其他 chunk 仍在途；新增无费用、始终异步的 `Controller::when_drained` 通知，使 IR 父操作在所有已发出 chunk/回复排空后才调用 fail_after_drain。原 direct 不注册通知时没有新增事件。普通适配器在部分提交后同步抛错时，也保留并排空已创建的读取/评分句柄。失败不返回部分得分，后继 JOIN 传播失败，Close 等真实资源释放。

## 契约补充

七算子现在均声明 `execution_supported=true`；JOIN 为 `runtime_async`，其余为 `existing_services_v1`。目录顶层 `execution_supported=false` 继续表示完整 run/engine 集成未提供，不能据此把生产入口视为已支持 IR。生成器、嵌入目录、catalog hash、样例和校验测试已同步。

初次 MERGE 必须能表示空历史得分。本轮补充唯一允许的得分 literal：`MERGE_CANDIDATES.previous` 的 `scores_id_f64[0]`、固定零长度、值为 `[]`。不能用于 batch、非空预计算距离或任意后继 trace；机器 schema 与 C++ 语义检查共同约束。其余 literal 仍为小标量及单个静态入口 ID。这补齐原 schema 样例重复使用首批得分、无法表达真实初次合并的缺口，不改变搜索、成本或 IR 版本。

状态 snapshot 的主机 JSON 使用 `scored` / `expanded` 数组表示集合，映射校验无重复且 ID 合法；受信功能内部按原有序集合迭代。前端仍须按原时点更新状态：评分提交时更新 scored，SELECT 可见后、邻接提交前更新 expanded。生产前端迁移及旁路审计留在 V03-4。

## 本轮验证

主证据：`results/v0.3/mapping-v033/final4/manifest.json`（passed=true）。保存运行前后源码 inventory、实际 diff、未提交文件 hash、输入夹具、MQSim 相对原生 diff/实际源码 hash、MQSim/SST 完整 commit、平台/工具链身份、每条命令/退出码/log、断言和全部产物 hash。成功运行期间源码及依赖未变；文档更新之后另做静态收口，不冒充运行前快照。MQSim 和 SST Core 均未修改；复用 SST 缓存，不声称干净重建。

复现入口（输出目录必须不存在，使用 deps.lock.json 锁定 Python）：

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/verify_ir_mapping.py \
  --build build/delivery --asan-build build/v033-asan \
  --output results/v0.3/mapping-v033/reproduce
```

| 实际检查 | 结果 |
| --- | --- |
| 根 `cmake -S . -B build/delivery -DBUILD_TESTING=ON`、`cmake --build build/delivery -j2` | 均退出 0 |
| `ctest --test-dir build/delivery --output-on-failure` | 退出 0，75/75；继承回归保留，新增 ir_operator_placements |
| `tests/test_ir_mapping.py --probe build/delivery/ir_mapping_probe --output …/placements` | 退出 0；20 次独立进程探针，包含四组合、小容量四组合、同刻 CPU 竞争四组合及8个映射边界用例 |
| `tests/test_ir_schema.py --validator build/delivery/ir_validate`、`tools/generate_ir_schema.py --check` | 均退出 0，10组 schema 检查及生成物一致性 |
| `tests/test_direct_baseline.py`；V03-0/1/2 证据 hash 重查 | 退出 0，3组证据回归；三个依赖成功证据及全部登记产物有效 |
| 新跑冻结 smoke 两 placement；逐项对比 V03-0 | 两次 run_config 均退出 0；完整 results JSON、除 wall_seconds 外全部模拟 statistics、events.jsonl 和 service.csv 均一致，后两者字节 hash 相同 |
| 独立 `build/v033-asan`，配置 ASan 并编译 ir_mapping_probe / ir_executor_probe / runtime_probe / ir_schema_probe | 配置、构建均退出 0；`ASAN_OPTIONS=halt_on_error=1 ctest … -R '^(ir_operator_placements\|ir_dynamic_lifecycle\|runtime_dependencies_visibility\|ir_schema_memory)$'` 退出 0，4/4，无 ASan 报错 |
| `git diff --check`；源码/依赖前后核对 | 通过 |

映射探针使用真实 MQSim OrdinaryDevice、LocalReads、PCIe、Controller、HostCpu、Runtime/EventBridge；测试时钟驱动同一 EventBridge，不使用墙钟成本。合成 N=5、d=260、R=4、FP32 零 query（query ID 7）、B=2/E=5/chunk=2；向量起点在每页末 sector，跨页读取。controller 容量 5264B，另用 2672B 强制一个向量/块。常规评分包含一次入口及两块后继；容量分块与 host chunk 分别继承，不强求底层块相同。

同 placement 的 direct/IR 严格比较每批候选/IDs、最终有序 FP64 得分、结果及排空时刻、所有 host 提交/ready/开始/结束/服务时间、全部 Work 字段、普通读 ID/LBA/sectors/顺序、local storage、消息种类/payload/useful/时间、credit 增减/占用和控制器阶段，以及每次 PCIe link 的 phase/type/destination/payload/wire/service/time。只排除具体 task/request/message 编号和 IR 管理事件，不排除时间、字节或 FIFO 顺序。controller 普通读取全为邻接，实际 DMA 等于邻接 sector 字节，完整向量普通 D2H 为零；Open/OpenAck/Close/CloseAck 各一次。

边界覆盖：结果提交后不可内联读取、QUERY_READY 未发布时提前 Close、重复 QUERY_READY 无任务副作用、未接纳 query 错误上下文、重复 ID 整批零读取、controller 单向量 sector 超界零 chunk、普通适配器部分提交后抛错、Controller 功能执行及 Open 失败、失败 JOIN 传播与会话清理。所有探针终态 op/value/task/waiter/content/session/credit 清零；原零时长/阶段延迟/并发资源用例也由完整 CTest 复跑。

## 失败记录与限制

首轮对照发现额外控制任务使提交时间变晚，已改为复用 HostCpu 父任务，没有删除比较字段。`attempt1/2` 保留差异日志（attempt2 曾在构建尚未结束时使用旧二进制）；后续完整构建后通过。`final` 的 CTest 因空历史得分测试夹具仍保留旧输出上界而失败，修正夹具后重跑。`final2` 的全部 CTest 已通过，但汇总器误用仅适用于 v2 真实输入的 original_query_id 字段，证据结论失败；改为完整 results JSON 比较。`final3` 完整通过；补充 Open 失败、重复接纳及 chunk 排空断言后，在 final4 重新运行全部检查通过，不覆盖失败产物。

本轮仅关闭 V03-3。生产 GraphSearch IR 前端、engine 选择、完整观测/比较器仍未实现；测试 Driver 只在测试目标内，不作为日常实验入口。本轮没有完整 AC、独立原生 oracle、真实 SIFT 四组合/52条矩阵、搜索 Q/RSS 梯度或效率预算验收；冻结重跑仅 smoke，两 placement 的完整 SIFT 冻结产物只核对 hash。ASan 为指定独立探针，非完整 SST/LSan 审计。

动态值与控制状态仍为进程内 JSON/不可变视图，没有 IR 文件回放、任意取消/失败恢复、新计算位置、缓存模型或通用算子。受信读取能力仍必须遵守原 DMA/CQ 完成及安全失败边界；不可恢复后端错误继续终止实验。参数均为 `configured_unvalidated`，不宣称绝对性能已校准或 controller 必然更快。到此停止，不自动开始 V03-4。

文档更新后的静态收口保存在 `results/v0.3/mapping-v033/final-checks2/manifest.json`：复查成功 manifest 的128份产物 hash、改动 Markdown 相对链接、Python AST、非文档源码及依赖未变、原 GraphSearch/GraphHost 与冻结源码一致，以及 `git diff --check`。该检查不增加模拟运行或验收覆盖。
