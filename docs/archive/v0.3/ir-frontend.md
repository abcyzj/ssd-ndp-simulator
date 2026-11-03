# V03-4 动态 GraphSearch 前端

2026-11-03（Asia/Shanghai）。**V03-4 已完成，停止于本任务。**V03-0～V03-3 成功 manifest 及全部登记产物 hash 在本轮重新核验。生产入口可显式选择 `execution_engine: direct|ir`；开发期省略仍为 direct，不提前执行 V03-7 的默认切换。完整 V0.3 验收、V03-5 观测/独立比较器和后续矩阵均未完成。

## 实现及旁路核查

[IrGraphSearch](../../../src/search/ir_graph.h) 接收 IR Submit 能力、Executor 可见输出/等待/lease 接口、配置与不可变初始状态。它没有 ContentStore、读取、HostCpu 或 Controller 实例，不调用距离计算、候选排序或邻居去重函数；这些功能和成本全部来自 V03-3 Mapping。原 [GraphSearch](../../../src/search/graph.cc) 文件未改，继续提供可运行 direct 对照。

| 核查项 | 实际路径 |
| --- | --- |
| 初始分支 | QUERY_READY 后的入口 SCORE 可提前提交为 Pending；仅固定 entry 使用 literal，不预计算距离或后继 trace |
| 动态分支 | SELECT 可见后才创建实际 READ；DEDUP 可见且非空才创建后继 SCORE；没有未选中分支或全历史 DAG |
| 评分与候选 | SCORE→MERGE 数据依赖；一次语义批次只合并一次，读取/计算/容量分块仍由原映射处理 |
| 状态时点 | SCORE 提交前登记 scored；SELECT 可见后、READ 提交前登记 expanded；状态以不可变版本交给控制算子 |
| 依赖 | query 的 DATA/CONTROL、动态 IDs 的 ADDRESS、邻接与得分的 DATA，以及绑定 snapshot；不以 ORDER 替代地址/数据 |
| 终止 | 空 SELECT 直接截取可见全体得分的 top-k，不新增收费 LOOP_CHECK；空 DEDUP 回到 SELECT |
| 生命周期 | 消费者接纳后释放前端生产者 lease；只持有 ready、当前全体得分及在途批次。GraphHost 终态检查 IR op/value/session 全归零 |
| 对照路径 | GraphHost 按 engine 独立构造 direct 或 IR；共享 trace/result 输出处理不改变原 direct 算法和服务 |
| 时间与费用 | 没有修改 Runtime、EventBridge、HostCpu、Controller、SST Core 或 MQSim；不新增收费服务或人为 1ns 推进 |

run 内所有查询共享单调 op ID 分配器，拒绝溢出；scope 带运行身份、查询运行 ID/ordinal、迭代和语义批次。原始 query ID/顺序继续由既有输入身份和结果输出绑定，不把 Runtime 查询编号当作原始数据集 ID。输出 shape 随输入上界收紧：MERGE 不超过两输入之和/节点数，DEDUP 不超过本批邻接边上界/节点数，SELECT/READ 不超过 min(B,E)。

[GraphHost](../../../src/sst/graph_host.cc) 在原接纳时刻执行 Mapping::admit（controller 一次 Open），结束时 Mapping::close 排空后继承 Close/Ack 及接纳规则。IR contract 的 search、shape、placement 和空操作列表在创建后端前与 SST 参数核对；不接受通过 contract 偷换实际配置。

## 配置与产物

在现有 JSON 配置增加 `"execution_engine": "ir"`，继续使用 `tools/run_config.py` 或日常实验入口。不支持的 engine 在生成输入/启动 SST 前拒绝；不静默回退。V03-4当时旧配置省略 engine 时写入有效 `direct`；[V03-7](../../releases/v0.3.md)完整交付通过后省略值已切为 `ir`，显式 `direct` 仍可复现。

[ir_frontend.py](../../../tools/ir_frontend.py) 从已验证输入构造空操作 contract，写入 `ir-manifest.json`。内容包含实际集合/文件身份、shape、搜索参数、前端源码、目录 hash、mapping/config 身份、依赖身份、scope 与诊断设置；dataset 对象身份绑定 base+query 内容组合。content_ref 是受信 provider 名称，不是绕过驻留的文件读取权限。生产前端运行时构造类型化操作，不读样例或回放文件。

现有 manifest 新增有效 engine 和 IR contract hash，记录新增源码/目录，IR contract 也进入登记产物 hash。能力目录顶层现为 execution_supported=true，表示生产动态 engine 已接入，**不表示 V0.3 全部验收通过**；生成器、样例 hash 和静态验证器输出保持同步。request/transaction/message 的完整 IR 关联、流式操作日志、正式比较白名单及 statistics/detail 等价属于 V03-5，本轮不提前宣称完成。

## 本轮验证

主证据：`results/v0.3/frontend-v034/final2/manifest.json`，passed=true。保存运行前后源码 inventory、工作区 diff、实际依赖源码身份、命令/退出码/log、配置、结果、逐批比较与产物 hash。全部模拟均为本轮新运行，准备数据/图/真值复用现有锁定输入，并在每次运行验证身份。SST 复用受控缓存，不声称干净复建。

复现入口（锁定 Python，输出目录必须不存在）：

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/verify_ir_frontend.py \
  --build build/delivery --asan-build build/v034-asan \
  --output results/v0.3/frontend-v034/reproduce
```

| 实际执行 | 结果与断言 |
| --- | --- |
| 根 CMake 配置、`cmake --build build/delivery -j2` | 均退出 0 |
| `ctest --test-dir build/delivery --output-on-failure` | 退出 0，78/78；保留全部既有回归，新增生产前端真实后端、SST 和因果三项 |
| `tests/test_ir_frontend.py --build build/delivery --output …/synthetic` | 退出 0，16 次生产 SST 运行：branch/mutated、Q4/并发2/B2/chunk2、B>E 四种输入/配置的 direct/ir×host/controller；非法 engine 与错配 contract 拒绝 |
| 原始 SIFT 10k、固定图向量交换输入、B2/E16/chunk2/并发2 小矩阵 | 12 次生产 SST 新运行；每点 N=10000、d=128、Q=32，同 placement direct/IR 的每批候选/扩展、精确 FP64 top-k、决策摘要、结果时刻、服务时刻/Work/账本一致 |
| 固定图/查询下扰动向量 | 两个 engine、两 placement 均改变真实后续邻接读取；图和查询 hash 相同，向量 hash 不同；未重建图或挑选查询 |
| `ir_frontend_causal_probe` | 退出 0；输入/计算/返回分别加100ns，首次后继 READ 从40→140ns，独立查询均7ns；每组3次提前 get 均 NotReady；终态活动值/任务/等待者归零 |
| `tests/test_ir_mapping.py --probe build/delivery/ir_frontend_probe` | 退出 0；20 次真实 MQSim 进程探针。常规/小容量/同刻竞争使用生产前端，原非法输入/失败排空边界继续保留测试驱动；逐项核对 Work、读取、PCIe、消息、credit、资源及终态回收 |
| 冻结 direct smoke 两 placement 新运行 | 两次退出 0；与 V03-0 完整 results 和全部模拟 statistics 一致，events.jsonl/service.csv 字节 hash 相同 |
| `tools/generate_ir_schema.py --check`、`tests/test_direct_baseline.py` | 均退出 0；生成物同步，3组冻结证据回归通过；schema 检查也在完整 CTest 执行 |
| 独立 `build/v034-asan` 配置/编译及定向 CTest | 均退出 0；ir_graph_frontend/ir_graph_frontend_causality 2/2，无 ASan 报错；不是完整 SST/LSan 审计 |
| `git diff --check`、源码/依赖前后核对 | 通过；没有更改 MQSim/SST 或自动提交 |

本轮测试用窄范围比较函数，不替代 V03-5 正式独立比较器：保留各事件/资源流的顺序、所有时间、Work、字节及节点/LBA；只排除明确的 opaque task/request/message/transaction 编号、墙钟与 IR 管理占用（峰值 task、中间 task/waiter 数）。controller direct 的额外 score_visible 诊断没有 IR 对应记录，测试仅跳过该诊断流；真实 ScoreResult/服务/发布后决策时间仍比较。session 与终态排空继续核对，未泛化排除时间或账本。

## 失败记录与限制

开发运行保留在 dev、attempt1～4：contract 标识/内容绑定与 shape 上界曾被校验器拒绝，已按现行契约修正；比较时发现 lifecycle 管理占用差异及 IR session 诊断漏计，分别明确管理字段边界并修正 session 计数，没有删除资源时间/费用断言。因果夹具曾漏初始化 MQSim，已补初始化后重跑。首轮 final 的 CTest、合成、因果、真实原始与扰动四组合通过，但汇总器写死不存在的 graph.u32 而失败；改为读取 index manifest 的 graph.path，final2 从头重跑全部检查，不覆盖失败证据。

本轮只关闭 V03-4；没有运行完整 AC、独立原生 oracle、完整 SIFT1M 52条矩阵、Q/RSS 梯度、效率门槛或正式 statistics/detail 等价验收。IR 状态和值仍使用进程内 JSON/不可变视图，有界活动状态不等于通过全规模效率预算。没有任意取消/恢复或文件回放；失败继续按现有安全排空/实验终止边界处理，不产生成功后继。硬件参数仍为 configured_unvalidated，不声称 controller 必然更快或绝对性能已校准。
