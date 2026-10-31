# T5 / M2 观测与生命周期收口

2026-10-31（Asia/Shanghai）。已读取 AGENTS、README、V0.2 PRD/实施计划、现行契约/输出/测试/限制，并核对 T3 依赖与 T4 收口。开工工作区干净；MQSim/SST 子模块未改，未自动提交、推送、升级或初始化依赖。本轮范围止于 T5；M2 的完整 SIFT1M 出口及 T6～T8 不标完成。

实现与接口见 [T5 观测与生命周期](t5-observation-lifecycle.md)。生产路径回收终态 Runtime Task、内容 lease、PCIe 在途关联和已关闭协议状态；错误位置、未就绪、重复释放/完成及旧句柄保护保留。内部回调及服务/返回 completion 单独保护任务生命周期，Controller 安全错误路径只在排空后通知 Runtime。HostCpu/Controller/消息记录流式输出，SSD 区间以固定排序块和落盘归并计算并集，初始化期间不捕获查询区间。Python 不再一次载入全部事件，以磁盘分组保持原 JSON/CSV 资源数组；新增决策摘要、生命周期采样、明确输出预算及 wait4 RSS/阶段墙钟产物。

## 实际验证

本轮根 CMake 配置/构建通过（退出0），复用有效 SST 缓存，非干净 SST 重建。修复后常规 CTest **66/66** 通过；既有 V0.1 回归全部保留。新增 `observation_lifecycle` 覆盖10,000次任务/lease回收、旧token/重复完成、在途失败关联保护及20,000条乱序/重叠/跨窗口/零长度区间独立并集；`streaming_output` 核对1,000→30,000条资源记录的JSON/CSV语义和Python堆增长小于2MiB。两项新增探针并非整个进程树内存证明。

```sh
cmake -S . -B build/delivery -DBUILD_TESTING=ON
cmake --build build/delivery -j2
ctest --test-dir build/delivery --output-on-failure
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/verify_t5.py \
  --build build/delivery --prepared results/v0.2-t2/real-final \
  --output results/v0.2-t5/verified-evidence
```

T5 专项最终退出0，共23条记录命令：单元探针、20次固定小base放置运行、真实小图两放置独立对照入口、1次预期失败的预算拒绝。固定 N256/d128/R8/E8/L16/k10/chunk16/并发4；Q32/128/512各两放置各三次，Q32再各跑detail。共享同一base和同一Q的query文件，三次结果/全部模拟统计相同，detail/statistics完全一致；detail中的决策摘要另从实际decision事件独立重算。所有终态 task/waiter/lease/历史记录归零，credit归零；协议非复用墓碑只有两个压缩区间。128B预算运行明确失败、日志含budget exhausted且无成功manifest，不能计为正常实验通过。

| 放置 | Q | 三次 SST peak RSS MiB 范围 | Runtime task 峰值 | 退休采样最大 task 数 |
| --- | ---: | ---: | ---: | ---: |
| host | 32 | 32.328–32.438 | 41 | 30 |
| host | 128 | 32.344–32.453 | 41 | 30 |
| host | 512 | 32.547–32.781 | 41 | 30 |
| controller | 32 | 32.438–32.500 | 47 | 34 |
| controller | 128 | 32.859–33.109 | 48 | 35 |
| controller | 512 | 34.422–34.516 | 48 | 35 |

按预注册 `max(Q512 RSS)-min(Q32 RSS)`，host 增长475,136 B（0.453MiB），controller增长2,179,072 B（2.078MiB），均小于原计划64MiB门槛。这里是wait4的整个SST子进程峰值，包含allocator/mmap，未隔离非文件私有RSS；因此同时保留活动容器证据，不把RSS近似不变独自当作无泄漏证明。必要结果文件由约39.5kB增至约629kB，单列为O(Qk)输出；query内容由16KiB增至256KiB，仍为O(Qd)。结果与精确分位数后处理的必要存储未被掩盖。

真实输入复用T2的SIFT10k重排base、32条预留诊断query原ID0..31、锁定Faiss NSG及独立FP64真值；未使用正式调参/评价样本。两放置Recall@10和1-recall@10均为1.0，独立逐批/距离/读取因果/流量/credit对照通过。它证明新回收与观测没有破坏真实小图闭环，不是完整1M规模证明。

本机证据位于忽略目录 `results/v0.2-t5/`：最终专项 `verified-evidence/{manifest,assertions,ac-matrix}.json`、所有有效配置/输入身份/命令/退出码/墙钟/wait4 RSS/原始日志、各次结果/统计/CSV、真实小图事件及独立comparison。最终专项目录文件大小快照258,389,679 B，非连续磁盘峰值。新检出不附带本机证据，须按T2重建准备数据再运行。

完整回归最终命令如下，均退出0：

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/verify_acceptance.py \
  --build build/delivery --output results/v0.2-t5/v01-final
cmake --build build/delivery-asan --target observation_lifecycle_probe mapped_content_probe -j2
ctest --test-dir build/delivery-asan -R '^(observation_lifecycle|mapped_content)$' --output-on-failure
git diff --check
```

`v01-final/` 中完整 **AC-01～AC-11** 全部通过，包括最终根配置/构建、**66/66 CTest**、独立锁定原生MQSim QD1/QD32/小CMT各两次SST精确对照、两种放置的快/慢功能变体及既有 **17/17 ASan**。新增生命周期与映射内容 ASan 另外 **2/2** 通过。快/慢 host 墙钟0.04052/0.06702s，controller 0.04336/0.05445s，均保持全部模拟事件、结果与统计等价；单次墙钟只用于该实现开销回归，不是稳定性能结论。常规最终日志在 `v01-final/{configure,build,ctest}.log`，专项ASan在 `asan-extra-{build,ctest}.log`；完整AC失败初轮 `v01-verified/` 保留且不计通过。

最终静态核对包括5个变更Python文件AST、变更文档本地链接、T5及所有放置实现源码哈希、Q32/128/512两放置top-k/决策摘要、子模块干净状态；记录在 `static-checks.json` / `verification-summary.json`。一次扩展到全部现行文档的链接扫描发现两条既有生成证据路径未在本机保存（`docs/evidence/m1.1/direct-source*`），不把该扩展扫描记为全量通过，也未修改历史链接。最终代码/测试/工具身份与验收快照一致；其后文档收口修改不冒充运行时的Git diff字节身份。

## 限制与失败记录

首轮CTest暴露finish阶段释放不能安排SST新唤醒，已修复；后续增加completion保护时，既有controller_evaluate/result安全失败路径等待一个不会再发出的正常completion，新增显式排空通知后重跑66/66通过。首个系统Python语法预编译因默认缓存目录越过sandbox失败，不计通过；实际Python功能由CTest和专项验证。首轮完整AC在快/慢盘控墙钟比较处失败：100ms轮询使两次运行都落在约0.1s，模拟统计一致；正式SST测量改为1ms轮询，保留原断言后重跑。早期probe1及final-evidence只为实现过程证据，交接以verified-evidence及后续完整回归为准。

只验证固定小base和真实10k小图。未执行T6/T7完整1M/10M查询矩阵或正式图/profile的联合全布局初始化，未实现快初始化；Deep全量建图仍有本机资源门槛。临时磁盘随事件数增长，排序有额外scratch；原始区间spool每个上限1GiB，输出共享默认2GiB预算，后处理总量预算为完成后检查而非硬限制。必要结果/延迟/query仍随Q增长；任意稀疏外部协议ID没有连续ID的压缩上界。

wait4不是整棵进程树同时驻留总和、分阶段peak或非文件私有RSS；这些仍Unsupported。阶段墙钟范围见接口文档，不与模拟ns/QPS混用。ASan只覆盖指定探针，任意在途取消/恢复及完整SST/上游泄漏审计仍Unsupported。硬件参数均configured_unvalidated，不声称绝对硬件性能准确或盘控一定更快。
