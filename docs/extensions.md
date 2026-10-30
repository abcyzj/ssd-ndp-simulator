# V0.1 扩展指南

当前接口是仓库内 C++17 源接口，没有稳定的插件 ABI 或动态注册框架。通常修改平台代码并从根 CMake 重建，无需修改 SST 或 MQSim FTL/NAND。设计草案与实际入口不同时，以本页链接到的头文件为准。

## 算法与算子

[GraphSearch](../src/search/graph.h) 是实际算法示例；[实现](../src/search/graph.cc) 按稳定批次更新候选，伪代码见 [搜索语义](search.md)。构造时注入 `Read(content::Kind,uint32_t) -> runtime::Handle`、可选 `Score(ids) -> runtime::Handle`、Trace、Done；算法只拿读取能力与结果句柄。接线位于 [graph_host.cc](../src/sst/graph_host.cc)，Python [host.py](../configs/sst/graph.py) 只创建 SST 组件和传参。

新增搜索策略时，仿照 `GraphSearch::await`，用 `Runtime::when_all` 注册 continuation，在成功回调后调用 `try_get`，随后释放句柄。保持 QueryId/TaskId 关联和主机控制计费；算法不得读取 ContentStore 原始字节、MQSim 全局状态或预先跑完整访问序列。单查询等待不能阻塞事件引擎。修改批次/同分策略会影响路径和 Recall，需给出新的语义和成对对照，不能继续引用旧策略的通过结论。

功能算子见 [operators.h](../src/operators/operators.h)：`squared_l2(query,vectors,ids)` 返回 `Result{bytes,work}`；`Work` 含元素、FLOP、读写/输出字节、候选检查/比较数。输出 score 显式序列化为 uint32 ID + binary64 distance，共 12 B。真实 FP32 输入按固定维序 FP64 累加；禁用 fast-math/FMA 改写，拒绝非有限值，按距离/ID 排序。

新增主机算子可仿照 [HostCpu::submit](../src/model/host_cpu.h)：输入为已在 HostMemory 可见的 Runtime handles，回调返回私有 Result，由 HostCpu 的工作量计费和 Runtime 服务完成门控后发布。`Kind` 当前仅 Distance/Control，新增种类需同步成本选择与输出统计。盘控目前只支持 L2 评分；新算子还需显式扩展 [Controller](../src/model/controller.h)、消息 schema 和解码、输出上界/credit、chunk 规则、返回完成与失败清理，不能仅增加功能函数就声称任意放置可用。

[Runtime::Operation](../src/runtime/runtime.h) 是受信适配层接口：`evaluate` 产生私有值，grant/start、service_done、return_done 分别由真实资源和传输事件触发。算法没有权限自行调用完成方法以缩短延迟。新增服务通过 EventBridge 安排 SST 时间事件；新增计算/staging 使用 setup + ceil(work/rate)，不要用墙钟或额外固定 SSD 延迟代替资源模型。

## 数据、布局与实验配置

数据格式、图生成/离线真值入口见 [搜索语义](search.md)、[ann_data.py](../tools/ann_data.py)；输入目录含 dataset.json、vectors.f32、queries.f32、adjacency.u32 和 truth.json。先用 `generate`/`exact_truth` 建立同一 k 的真值，再以统一入口的 `--data /absolute/dataset` 运行。已有数据会校验维度、长度、哈希、有限值与 truth 身份；不要手改字节却保留旧哈希或旧真值。

[ContentStore / Layout / Handles](../src/content/content.h) 分别负责不可变内容、逻辑对象到 LBA extent、驻留权限/生命周期。默认向量与邻接分区，512 B 向外对齐，跨页由后端拆分。修改布局必须同步 Python 的 layout.json、后端预装载范围、平台解析及独立核对器；逻辑有效字节、sector 字节和 NAND/PCIe 字节保持分层。PPA 仅供观测，不能作为算法稳定地址。跨 sector/page 是必测边界。

从 `configs/experiments/host.json` 复制配置，用 `--config` 选择，统一入口按 `--mode` 写入 placement。可配置 search、host、controller、max_inflight、parameter_source、event_log；生成参数是 generate。配置字段及单位见 [架构与成本](architecture.md)、[盘控资源](controller.md)、[输出](output.md)。目前 `device_profile` 只支持 native-small；XML 来自 configs/native/ssdconfig.xml 并编译进 profile，改变 XML 后必须重建，不支持 CLI 任意切换 SSD。底层链路/NAND参数实验用 M4.2 的受控入口；不要声称 JSON 暴露所有 MQSim 参数。

## 观测与验收

后端观测契约见 [observation.h](../src/backend/observation.h)、[输出口径](output.md)，结果聚合见 [experiment_output.py](../tools/experiment_output.py)。新增事件应携带时间、实体、query/task/request/transaction 稳定 ID；统计须明确测量窗口、区间并集与单位。同步 JSON/CSV、capabilities 和独立核对器，不可把未知值填 0。研究观测数据不得免费传给算法。

按变更选择验收：算法/数值使用 graph_probe、operator_probe 和成对逐批 replay；布局用 content_probe/content_backend_probe 与跨页夹具；盘控用 controller_probe/controller_resource_probe、安全失败和单工作块竞争；桥/普通路径或 MQSim 改动必须重跑独立原生 AC-08。最终运行 `tools/verify_acceptance.py` 完整矩阵，按 [交付说明](delivery.md) 保存输入、命令、退出码、断言及哈希。不要以 quick subset 或静态检查替代完整验收。
