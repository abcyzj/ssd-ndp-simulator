# V03-2 动态依赖与值生命周期

2026-11-03（Asia/Shanghai）。依赖 V03-1 已完成；本轮新增进程内增量接纳、可见值绑定、不可变状态快照、异步 JOIN、失败传播与活动对象回收。**本页记录 V03-2 当轮仅 JOIN 可执行的收口。**后续 [V03-3](ir-mapping.md) 已实现其余六算子映射；生产 GraphSearch IR 前端及 engine 配置尚未实现，不代表 V0.3 验收通过。

## 接口与边界

[Executor](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/src/ir/executor.h) 每个 run 建立一个实例，使用无 operations 的已验证 Document 作为 contract。`submit(operation, producer_leases, trusted_adapter, snapshots)` 按 run 内递增非零 uint64 ID 接纳；ID 不复用，最大值作为溢出保护拒绝。引用只能指向此前已接纳且调用者持有有效 lease 的生产者，可以 Pending；不接受自引用、前向引用、跨 run/query/ordinal、错误端口/类型/shape、未知内容版本、缺失 ADDRESS/DATA 边或缺失逻辑读取 CONTROL 许可。ORDER 也等待实际完成；四类边为 AND，不额外产生传输或收费。重复语义边不重复执行生产者。

静态校验复用 [schema](https://github.com/abcyzj/ssd-ndp-simulator/blob/64e2ca3c6427c34600ab3085c0d8101f1bc34b0d/src/ir/schema.cc) 的相同结构与语义检查。增量入口只重查新操作及提供的活动生产者描述，不递归要求此前已回收的祖先；诊断文件的完整有序校验保持原规则。接纳前失败不分配 Runtime 任务或 pin。全量动态地址、实际输入 shape 和状态绑定在任何 adapter grant/真实请求/credit 前校验；输出在受信 evaluate 后检查端口、实际类型/动态上界、读取/评分的实际批次长度及有限得分，失败不发布。

`Lease` 复制共享同一个释放位；独立 ownership 必须 `retain`，每个 lease 只 `release` 一次。消费者与 waiter 在提交时独立 pin 生产者，前端提前释放不影响已接纳消费者；回收后不能凭历史 op ID 建立新消费。`get` 只通过 Runtime 的 HostMemory 可见性门控，私有计算结果直到服务及必要返回完成才可读。操作及值的活动/峰值计数包括 Pending 的输出端口；累计 submitted/completed/failed 计数不保留历史图。`diagnostic/require_progress` 关联 query、IR op、Runtime task、依赖、owner/pin 和等待资源。

`StateSnapshot::initial/advance` 建立查询私有不可变 JSON 状态视图；advance 在共享 lineage 内单调分配版本，旧视图不被覆盖。`state_ref` 可以绑定显式持有的 snapshot（提交时验证 query/state_id/version），也可以通过 `ORDER + state_version` 绑定生产者的 Pending 状态输出（Visible 后、grant 前验证）。显式视图支持前端在消费已可见 IDs 后更新 scored/expanded，避免为版本更新额外执行 QUERY_READY 或收费服务。executor 不替前端计算搜索状态，后续图迁移仍须遵守原更新时点和成本。快照由消费者持有到功能执行/失败排空；未使用的绑定、版本不符及歧义绑定拒绝。

Trusted Adapter 承接原 Runtime 的 grant/start、一次 evaluate、service_done 与 return_done；它必须负责实际服务、传输字节、费用及安全排空，调用者不得以该接口隐藏未计费搜索。Runtime 仅携带空字节的完成门控，真实值留在操作私有槽；返回适配回调不接收这段空门控字节数。V03-2 冻结时其余六算子只能用测试受信 adapter 做边界探针，不能据此宣称已有真实映射。该轮能力目录全局 execution_supported 为 false，JOIN 单项为 true / runtime_async，目录 hash 及样例已同步更新。

`join` 产生 done 令牌并复用 Runtime/EventBridge，不新增目标机器服务，空集合及零时长也经异步 Publish/ContinueSubmit。失败保留 Runtime 根 code/query/task/time/cause；接纳、动态校验和 adapter 异常补充 IR op/producer/时间及 detail。失败消费者不获得 grant，已发出工作沿原 fail_after_drain 规则处理；受信适配器通过 Executor::fail_after_drain 补充 IR 归属，禁止尚有在途服务/返回时调用。无 owner、consumer/waiter pin 及管理回调后删除活动映射；Runtime 继续负责在途 service/return/completion 的最终排空。Executor 与 Runtime 均必须活到全部回调排空。

## 本轮验证与证据

主证据：`results/v0.3/dynamic-v032/final3/manifest.json`。保存合成输入 hash、前后源码 inventory、实际 diff/hash、MQSim 相对原生 diff 与实际源码 hash、MQSim/SST 完整 commit、工具链、命令、退出码、日志及产物 hash。源码及依赖前后不变断言通过；MQSim 和 SST 源码未改动。本轮使用既有 SST 缓存，不声称干净重建。

| 实际运行 | 结果 / 断言 |
| --- | --- |
| `cmake -S . -B build/delivery -DBUILD_TESTING=ON`；`cmake --build build/delivery -j2` | 均退出 0 |
| `ctest --test-dir build/delivery --output-on-failure` | 退出 0，74/74；包含继承搜索、资源与 Runtime 回归 |
| `build/delivery/ir_executor_probe examples/ir/graph-contract.json` | 退出 0；非法依赖/作用域/类型/释放拒绝、旧/新状态视图及持有、真实值动态创建后继、延迟/失败/排空、停滞关联、同刻提交 FIFO 对照通过 |
| `ctest --test-dir build/delivery -V -R '^sst_ir_'` | 退出 0，4/4；真实 SST SelfLink、EventBridge、Runtime 的因果状态日志 |
| 锁定 Python 运行 `tools/generate_ir_schema.py --check`、`tests/test_ir_schema.py --validator build/delivery/ir_validate`、`tests/test_direct_baseline.py` | 均退出 0；生成物一致，9 组 schema 测试、3 组 direct 冻结证据测试通过 |
| 锁定 Python 调用 `freeze_direct_baseline.verify_evidence(Path('results/v0.3/direct-baseline-final'))` | 退出 0；冻结 manifest 状态及全部产物重新求 hash 通过，未重跑完整 SIFT |
| `cmake -S . -B build/v032-asan -DBUILD_TESTING=ON -DNDPSIM_SANITIZE=ON`；`cmake --build build/v032-asan --target ir_executor_probe runtime_probe ir_schema_probe -j2` | 均退出 0；IR、Runtime 及依赖编译带 `-fsanitize=address` |
| `ASAN_OPTIONS=halt_on_error=1 ctest --test-dir build/v032-asan --output-on-failure -R '^(ir_dynamic_lifecycle\|runtime_dependencies_visibility\|ir_schema_memory)$'` | 退出 0，3/3；无 ASan 报错，非完整 SST/LSan 泄漏审计 |
| `git diff --check` | 退出 0 |

原生及 SST 因果链中，基准后继完成为 65 ns；分别在输入、计算、返回阶段增加 100 ns 后均为 165 ns；独立查询保持 7 ns，三次过早读取均为 NotReady。读取地址先完整检查后 grant，越界、实际 shape 超限或状态版本/身份错误不产生后继读取。可见 ID 从 1 改为 2 时，回调中新创建的后继操作实际地址也随之改变；不是预建完整操作 trace。

连续 2,001 个空 JOIN（固定活动并发为 1）后 active op/value、Runtime task/waiter/private slot 全部归零，峰值 op/value 各为 1。八操作因果夹具的峰值为 8 op / 12 value，终态归零；快照 weak_ptr 在消费者排空后失效。四个同刻任务的资源提交顺序与直接 Runtime 探针完全一致（0 ns 的 1、2、3、4），不把此微型对照视为全部 graph/placement 的等价证明。

测试结束后仅更新 Markdown 文档与任务状态，静态收口记录在 `results/v0.3/dynamic-v032/final-checks/manifest.json`：核对相对链接、全部最终证据 hash、非文档源码未变、direct 原执行源码与冻结参考一致、依赖身份及 `git diff --check`。这不是额外的模拟运行。

首轮 `results/v0.3/dynamic-v032/final/` 的测试命令全部退出 0，但证据汇总对 CTest 摘要格式的旧字符串断言失败，manifest 保留 passed=false 及原因；修正汇总格式后在 final3 完整重跑，不覆盖失败产物、不弱化测试断言。

## 限制与后续依赖

- V03-2 只关闭动态管理层门槛。其余算子映射、真实搜索前端、执行 engine 选择、IR 观测与独立比较器仍待 V03-3～5；direct 默认及搜索行为未变。
- V03-6起Value使用共享不可变payload，评分保留原packed FP64记录，`data()`提供延迟JSON诊断视图；状态仍使用进程内JSON与共享不可变视图（[证据](ir-matrix.md)），没有运行时 DAG 文件回放或设备可读状态。未实现设备输出消费、新位置、可写索引或通用算子。
- 本轮没有完整 AC、独立原生 oracle、SIFT 四组合/52 条矩阵、真实搜索 Q/RSS 梯度或效率预算测量；2,001 JOIN 是活动元数据回收探针，不能替代 V03-6 的搜索规模与 RSS 验证。不可恢复后端失败仍终止实验，不新增恢复保证。
- V03-2 当轮的真实计费与 host/controller 映射保持未实现声明；合成服务时间只是因果探针，硬件参数仍为 configured_unvalidated，不作性能优劣或校准结论。本轮到此停止，不自动开始 V03-3。
