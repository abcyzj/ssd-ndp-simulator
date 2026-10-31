# T5 观测与生命周期接口

默认 `event_log=false` 为统计模式，`true` 增加详细诊断。两者执行相同闭环、费用、资源排队与可见性边界，不向算法开放内部观测。保持现有 JSON/CSV 结果字段，新增逐查询 `decision_digest_fnv1a64`、`lifecycle_sample` 和最终 `lifecycle` 资源记录。摘要按每次实际 decision 的候选 `(ID, FP64 distance)` 与扩展 ID 的确定性文本累计 FNV-1a64；用于定位路径差异，非密码学身份或完整逐批证据的替代。

## 生命周期

- Runtime 的任务 ID 在单 rank/线程进程内不复用。结果须已终止、owner/pin 归零、依赖 continuation 和内部发布回调归零、服务/返回 completion 不再在途，才回收 Task；事件回调内回收延后到当前栈返回。SST finish 中的最终释放不再安排新唤醒。
- 复制 Handle 不增加 owner。Runtime 和内容 lease 的复制 token 共用释放位；独立 retain/pin 生成独立 lease。内容 registry 仅保存活动 lease，释放后删除；旧 token 仍拒绝重复释放或访问，不能指向新对象。位置、未就绪、失败及错误 completion 检查保留。
- `Runtime::fail_after_drain` 只供受信适配器使用：须先排空本地 I/O、staging/compute 和错误返回，并保证没有后续正常 completion。Controller 现有安全失败注入在 Error 到达后使用此入口。一般 `fail` 不消除在途 completion 关联；晚到完成明确失败，不能发布结果。任意在途取消与故障恢复仍 Unsupported。
- PCIe 使用在途 map 按实际 ID 关联。完成记录先结算，再调用接收者，接收回调返回后删除关联。生产路径不保留完成历史；小型 probes 默认保留历史以维护原有诊断接口。
- 协议已结束查询与 token 的非复用信息用不相交 ID 区间压缩。生产查询和 token 连续分配，乱序关闭的缺口由并发约束；每查询 task/chunk 集合在关闭后删除。任意稀疏外部 ID 不具有同样的区间数上界。

## 观测及输出

HostCpu service、Controller storage/credit/resource 和 PCIe message 在产生时写入必需记录流，不累积内存 vector。每查询普通读计数在回收查询时导出并删除。Runtime transitions 与内容 releases 在生产路径关闭历史捕获；不关闭必要错误检查。

SSD 区间先落临时文件。report 按实体裁剪窗口，以 4096 项排序块、二进制进位归并运行文件做外部排序，再计算半开区间并集。回调不必按起点或终点排序；零长度、重叠、跨窗口和多 die 的 channel bus 并集保持原定义。内存为固定排序块及每实体至多 64 个运行文件槽；临时磁盘随区间数增长，报告时仍输出可独立重算的详细区间。GraphHost 初始化期间不捕获区间历史，正式查询 origin 在全布局预装载排空后建立新窗口，不重置 MQSim 时间、FTL 或 CMT。

必需 records 按行读取；Python 后处理用 SQLite 临时分组（2 MiB 页缓存）、逐行 JSON/CSV 导出，保持原有 `statistics.json.resources` 数组格式。不会将全部事件解析成 Python list。SST 逐查询写出结果；Python 为排序、质量与精确分位数仍保留 O(Qk) 结果及 O(Q) 延迟，query 功能内容也仍为 O(Qd)。这部分必要存储不计作已消除的事件历史。

`output_budget_bytes` 为正整数，默认 2 GiB；SST 必需记录与可选日志共享此写入预算，耗尽立即失败。Python 完成后另检查输出目录直接文件总量（不包括复用输入或子目录）是否超过同一预算；这是完成后检查，非后处理磁盘硬限额。每个区间原始 spool 上限 1 GiB，排序有额外临时磁盘；临时文件创建/读/写/flush 失败明确报错。失败不生成成功 manifest，不采样或静默丢弃最终结果。上限不是对整个进程或机器的 RSS/磁盘硬隔离。

`sst-measurement.json` 记录 wait4 的 SST 子进程峰值 RSS（Darwin bytes，Linux KiB 转 bytes），不是整棵进程树同时驻留总和，也不隔离非文件私有 RSS。正式 SST 使用1ms完成轮询并记录实际采样间隔，避免100ms轮询掩盖短运行的实现开销差异。记录整体子进程墙钟、GraphHost 构造至 origin 的初始化墙钟、origin 至 finish 开始的查询墙钟，以及结果解析/质量核对/JSON CSV 导出的后处理墙钟；阶段墙钟不含所有进程启动/收尾/manifest 哈希，因此不强制加和为整体墙钟。分阶段 RSS 仍 Unsupported。实际效率字段位于测量产物，统计中的固定引用不混入模拟指标或日志等价性比较。

`lifecycle_sample` 在查询退休回调返回后记录活动 task/waiter/lease/session 数；`lifecycle` 记录最终容器数和整个运行的 task 峰值。它们验证历史容器回收，不能用 `live_results=0` 独自替代。SST RSS 含 allocator 高水位和 mmap 页面，须与容器计数、必要结果大小一起解释。

验证入口：

```sh
python3.13 tools/verify_t5.py --build build/delivery \
  --prepared results/v0.2-t2/real-final --output results/v0.2-t5/new-evidence
```

该命令只执行 T5 的固定小 base 查询梯度、日志等价性、资源计数、预算拒绝及可选真实小图逐批核对。T6/T7 完整规模、目标布局联合初始化和扫描矩阵不在该入口中。
