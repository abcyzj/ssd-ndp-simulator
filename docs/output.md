# 并发、结果与观测口径

现行运行入口见[README](../README.md)。`max_inflight` 为正整数，已验证矩阵覆盖 1/2/4；`event_log` 默认 false。旧 M3.1/M3.4 运行器保留其日志行为用于回归。参数边界见[模型限制](model-limitations.md)。

## 接纳与时间口径

所有查询在确定性预装载排空后的 `origin_ns` 同时 offered，按 query 文件顺序 FIFO 接纳。主机模式上限为 `max_inflight`；盘控沿用 M3.3 的确定性 chunk 和 `admission_limit`，实际接纳数同时受配置与 query-buffer credit 约束，始终保留至少一个完整工作块空间。GraphHost 在接纳前就应用此上限，避免先占据所有搜索槽后再等盘控 query credit。临时空间不足进入 FIFO 等待，单 query 加最小块仍不能容纳时启动失败。

每个 query 独立持有搜索状态、ID 与提交时间；共享 SST 事件桥、主机 CPU、SSD、PCIe 和盘控资源，等待使用 continuation，不阻塞事件引擎。普通读取显式传递所属 query，完成顺序无需等同于提交顺序。最终结果在主机可见时记录完成，随后盘控 Close/CloseAck 回收资源，才腾出接纳槽；搜索状态销毁延后到当前回调返回。最终结束必须已完成所有查询且后端请求、运行时结果、内容、消息、slot 和 credit 全部归零。

- `latency_ns = end_ns - start_ns`：接纳到最终 top-k 可见，盘控含 query 上传。
- `admission_wait_ns = start_ns - offered_ns`：并发或容量上限外等待。
- `offered_latency_ns = end_ns - offered_ns`。
- 吞吐为完成数除以首个接纳到最后一个结果可见的窗口；`drain_end_ns` 另记资源排空结束，组件累计服务时长不能冒充查询延迟。
- p50/p95/p99 采用 nearest-rank，每组明确 `samples`，小样本 p99 不代表稳定尾延迟。

## 输出与观测

`results.json` 保存按 query ID 排序的 top-k、距离、Recall、时延和结束状态；真值仍由独立离线穷举生成。`statistics.json` 保存延迟/接纳等待分位数及样本数、吞吐、SST 子进程墙钟、普通/本地请求数和字节、H2D/D2H payload/wire、消息、主机/盘控工作量与服务/等待、credit 生命周期和缓冲峰值。主机明确分开依赖等待 `ready-submit` 与 CPU FIFO 等待 `start-ready`，盘控服务报告 `start-submitted`。

`queries.csv`、`top-k.csv`、`statistics.csv` 与各资源同名 CSV 提供平面数据。配置、SST 参数、布局、MQSim XML、命令和 `manifest.json` 保存输入/真值、代码、依赖身份及产物哈希，MQSim 同时保留原生基线、diff 与实际源码哈希。墙钟独立于模拟硬件时间；便携式子进程 peak RSS 未收集，明确为 Unsupported。

必需结果通过临时 `records.jsonl` 通道交给 Python，转换为 JSON/CSV 后删除；统计不从可选事件日志重建。`event_log=false` 时不创建 `events.jsonl`，观测与统计仍工作。开启时普通读取、决策、结果可见与 PCIe 事件直接写文件，服务、credit、消息和 SSD 区间在报告阶段导出；输出不保证按时间排序。所有 query/read/task 关联使用真实 ID；内部共享 NAND command hook 不持有请求/事务参与集合，保留 null 与原因，不能伪造 query 归因。

channel/die 观测复用 M1.4 的被动 MQSim hook，测量窗口裁剪后的半开区间按实体取并集；bus 仅含总线传输，不含 array。每个实体利用率为其 union/window，不相加成设备利用率。`capabilities.json` 明确 device/request/channel/die 支持定义，plane/latch、共享 DRAM 和内部命令参与集合等 Unsupported。算法不获得全局内部状态。

