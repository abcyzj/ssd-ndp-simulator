# V0.1 模型与交付限制

已验收能力是单 SSD、应用只读、主机控制的简化图 ANNS，距离放置于主机或盘控。不是完整 DiskANN/AISAQ、SSD 自主搜索、多 SSD、多主机、可写索引或其他计算位置。每次实验新进程，单 SST rank/线程；不承诺同进程多次 Reset、多设备静态状态隔离或其他操作系统/架构的通过结果。

硬件参数均标为 `configured_unvalidated`。计算为非流水 FIFO/slots，setup + ceil(work/rate)，没有指令级/周期精确、主机 DRAM/CPU cache、精确能耗或 OS/驱动栈。主机距离 rate 是含加载的综合能力；控制按候选检查/比较计费。PCIe 保留 MQSim 原生整数截断和共享双向队列，小消息可能为 0 ns；wire 指模型公式中的计费字节，不是校准后的完整物理协议。各模块累计忙时/等待不能相加当查询端到端延迟。

仅声明的 SIMPLE + TURNED_OFF 数据缓存和确定性预装载初态通过验收，保留 CMT；不推广为任意缓存/初态或稳态真实 SSD。预装载排空与测量分开，没有应用级跨查询向量/邻接对象缓存。MQSim 原有内部维护逻辑保留，但不声称充分覆盖任意 GC/WL 压力。NAND/FTL/ONFI/缓存和 PCIe 各自由 MQSim 唯一计费。

近存 query、输入、结果、scratch 缓冲为独立容量模型；不与 MQSim DRAM/CMT 建立精细共享竞争。工作块确定性分块，并发接纳预留可前进空间。安全失败注入和超大请求拒绝经过验证；任意在途后端失败、取消/回滚、崩溃恢复和持久性不在已验收范围。ASan 仅运行指定独立探针，非完整 SST/上游泄漏审计；原生 RNG 的 UBSan 溢出历史问题未在此修复。

FP32 输入、固定顺序 FP64 L2、同分按 ID；同工具链下路径应稳定，跨工具链数值容差为 abs 1e-9 + rel 1e-7，不承诺跨平台比特一致。Recall 来自独立穷举真值；默认 32 查询的分位数仅为小样本摘要，不是稳定尾延迟结论。盘控更快不属于验收门槛。

观测支持设备/请求/channel/die，channel bus 与 die array 按活动区间并集统计；plane/latch 仍 Unsupported。T5 的生产事件记录流式导出、区间落盘外排序、终态任务和 lease 回收；Python 保留 O(Qk) 结果及 O(Q) 分位数输入，SST query 内容仍 O(Qd)，临时磁盘随事件量增长。不承诺整个进程常量内存或十亿级数据规模。真实墙钟仅衡量仿真效率。

V0.2 的 [M0 探针](m0-closeout.md)单独记录原始数据验证、离线 Faiss 和 SST 预装载进程的墙钟及 wait4 峰值 RSS；T5 已将 wait4 子进程峰值接入正式实验测量产物；分阶段与非文件私有 RSS 仍 Unsupported，不能用整个 SST 峰值替代它们。T4 已接入受控 2/16 GiB profile、实际 namespace 检查及 mmap/公式布局，见 [T4](t4-content-profiles.md)；M0 探针和 T4 小布局验证均未覆盖大规模查询与观测的全部内存。16 GiB 本机不能直接采用 Deep 建图 32 GiB 预算；完整 Deep10M 建图仍有资源出口门槛。数据使用条件见内容锁，不将项目或 Faiss 的 MIT 许可证扩展到 benchmark 数据。

可复建指本机 macOS / arm64，已有固定子模块和预装工具时，从新的平台构建目录及新的 SST 缓存重建、运行两放置与 AC。不是全新 OS 配置、可移植二进制或无外部工具的 hermetic 构建。证据目录默认不入 Git，新检出须重跑生成；MQSim fork commit 必须可取得。平台代码和文档使用 [MIT License](../LICENSE)，第三方依赖保留各自原有许可证。可复建验收不等于已经完成公开发布。

## V0.2 T2 离线准备增量

已提供 T1 数据上的锁定 Faiss NSG 导出、图检查、独立流式 FP64 精确真值及 M0 锁定完整 SIFT 官方 ID-only 导入，见 [图与真值](index-truth-v2.md)。原生建图器诊断不代表项目搜索质量；v2 已接入 SST 两放置小规模闭环（[T3](t3-closed-loop.md)），全规模尚未验证。官方有限 K 的严格并列边界标记 unresolved。纯 Python 精确真值、随机文件 BFS、完成后 RSS 检查和磁盘快照均未通过全规模资源验收；完整 Deep10M 建图仍被本机资源门槛阻塞。不改变任何硬件校准或性能结论。

T4 文件映射只减少功能内容与布局私有副本，不消除宿主页缓存驻留；query 文件仍全量读取。静态 metadata 预算是代理估算，memory 后端内容、搜索和历史事件内存不在其界内。输入运行期间必须不可变，开始/结束身份检查不构成并发写入或截断安全协议。T5 历史回收及验证见 [观测生命周期](t5-observation-lifecycle.md)；T6/T7 的完整目标布局联合预装载和矩阵仍未完成。
