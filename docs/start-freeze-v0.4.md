# V04-0 开工冻结

2026-11-03（Asia/Shanghai）。**V04-0 已完成，仅关闭开工门槛。**原生程序、Profiler、CPU Profile、内存驻留和精度出口仍待实施；当前可运行交付为 V0.3。未启动 V04-1a 或其他后续任务，未提交、推送或改动子模块。

## 实现与依赖

新增 `tools/freeze_v04_start.py`：只接受锁定 Python、新输出目录和本轮硬件证据；核验完整输入及历史参考，保存源码前后身份、未提交 patch/未跟踪文件副本、两子模块完整 commit/实际源码 hash、MQSim 相对锁定原生 commit 的完整 diff、实际构建身份和命令/退出码/日志，生成本轮四组合 smoke 参考。新增 `tests/test_v04_start.py`，根 CMake 注册 `v04_start_gates`；不修改任何 `src/`、现行运行配置或依赖实现。

需求来源：PRD §7.2、§8.1、§10，FR-04-N03/V05，AC-04-09/12 的开工身份与资源部分。本项不关闭这些 AC 的其他阶段要求。开工已读取 AGENTS、README、V0.4 PRD/清单/源码核查、继承 V0.3 PRD/IR、矩阵、direct 冻结、交付记录及相关接口。

依赖均已重新核验：

- V03-0 direct 参考和 V03-7 总交付 manifest 与发布记录的 SHA256 锚点一致，清单登记的全部产物逐一重算 hash；direct 补充构建文件另验其 `build-identity.json`，补充文件 hash 单列。历史验收不计新 V0.4 通过。
- 完整原始 SIFT 四文件与锁定大小/hash一致；四组准备数据逐文件 hash、完整 base/NSG 合法性及真值绑定检查通过。
- 实际 `query.ids.u32` 与原始 ID/顺序一致；tuning32/evaluation128 不相交，detail8=evaluation前8、concurrency组=评价前32。D01/D02仍使用全evaluation128，接纳上限32，不误用32查询替代正式诊断。
- builder 可执行文件、OpenMP、适配器、lock、准备 receipt 与构建 manifest、原构建命令日志均核验；没有重新建图或重选查询。

## 实际运行与证据

最终证据目录为本机忽略目录 `results/v0.4/start-v040/final1/`。开工主仓 commit、源码逐文件 hash、未提交修改、完整依赖身份在 `source-start.json`、`source-end.json`、`workspace.patch`、`untracked-source/`、`dependencies.json`，不以 HEAD 代替工作区内容。收口文档在运行后更新，另存 `results/v0.4/start-v040/closeout/`，不冒充运行前文档字节。

```sh
# 本轮受限进程不能读取 sysctl，单独读取并保存真实 stdout（退出0）。
/usr/sbin/sysctl hw.memsize hw.model machdep.cpu.brand_string hw.ncpu hw.physicalcpu hw.logicalcpu kern.osrelease kern.osversion > /private/tmp/v040-hardware.txt
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/freeze_v04_start.py \
  --hardware-evidence /private/tmp/v040-hardware.txt \
  --build build/delivery --output results/v0.4/start-v040/final1
```

输出必须不存在，重跑换新目录。默认输入仍为 `results/v0.2-t6/preparation/prepared`、`.cache/v0.2-m0/data/sift`、`results/v0.2-t2/builder`；可用 `--prepared`、`--source-dir`、`--builder-evidence` 指定。工具不下载、不初始化 Git、不执行完整规模采集。

| 本轮最终检查 | 结果 |
| --- | --- |
| 根 CMake 配置、`cmake --build build/delivery -j2` | 均退出0，复用锁定 SST 缓存 |
| `ctest --test-dir build/delivery --output-on-failure` | 83/83 通过，含所有继承用例和新门槛 |
| `tests/test_v04_start.py` | 5/5；缺项、篡改、路径越界、失败/重复命令、失败断言、错误任务/schema、14配置/重复日程偏移拒绝 |
| 新 direct/默认 IR × host/controller | 4次成功，N2048/d32/Q32、L32/E64、chunk16、c1、详细日志；省略engine有效ir，IR manifest存在 |
| `compare_engines.py` 两放置 | 均退出0；结果、模拟时间、Work、资源/传输账本严格一致，保留既有枚举白名单 |
| direct 对历史冻结 | 两放置结果、规范化完整统计、events/service字节 hash一致 |
| `compare_placements.py` direct及默认IR | 均退出0，独立重算数值/搜索逐批决策和两放置账本 |
| 输入/历史依赖/预算/源码前后及`git diff --check` | 全部通过，完整命令和断言见manifest |

首次 `run1/` 为补齐编译器和 builder 构建凭据前的成功试跑，保留原证据；最终版本重新执行全部上述检查，不按旧身份复用试跑。最终 manifest hash及测量值见下方冻结摘要。

## 资源、计划与限制

本机 Apple M4、Mac16,12，10核、16GiB，macOS26.5.1/arm64。`environment.json` 保留真实硬件 stdout、受限读取失败、`vm_stat`、内存压力及 OS 信息；频率/核绑定 Unsupported/未控制。压缩/换页累计记录不是查询窗口缺页测量，不能据此声称未来样本没有分页。

`budget-frozen.json` 独立采用 V0.4 预算：查询进程≤4GiB且≤60%物理RAM，后处理≤512MiB，初始化3600s/查询7200s/后处理600s，单次必需+详细输出≤2GiB；原始SIFT+共享准备+全部`results/v0.4`（含试跑/失败）总逻辑量≤25GiB、卷余量≥20GiB，不计V0.3历史结果、build及其余缓存。正式采集前必须重新检查全部输入、RAM/压力/缺页、磁盘/输出/超时，并先测量Trace再由V04-2a/2c冻结采样，不以本轮预算代替未来预检。

`matrix-plan.json` 冻结 `v04-core14/1` 的14个唯一配置、最低20次模拟/18次原生性能运行、训练四点、三组A/B/A、detail8及定向/继承/按需深度日程。盘控0.5×/1×/2×对应合法rate8/16/32 FLOP/ns，其他Controller参数继承锁定配置。Profile/采样具体身份须由所属后续任务产生；本计划不是已可执行的14配置验收工具，也未运行其中任何完整规模配置。

本次只新增冻结/检查工具及CTest注册；搜索/数值/Work、旧默认计费、事件/发布、SSD/传输、布局、设备和子模块均无实现变更。新smoke无异常，因此本项未触发52条深度回归，不计该层通过。未新跑完整V0.1 AC、独立MQSim原生oracle、完整ASan、V0.2 T3/T4/T5独立阶段、完整SIFT矩阵或CPU精度；CTest内的有效继承检查照常执行。后续V04-5b仍必须重新判断深度影响。

RSS为进程高水位及完成后检查，磁盘为1秒逻辑大小采样，非硬隔离/同时进程树测量；SST缓存复用不是干净重建。SSD/PCIe/Controller均`configured_unvalidated`，官方有限真值并列边界及固定子集限制继续保留。未改变V0.3默认或把历史通过计入V0.4功能/精度结论。

## 最终冻结摘要

最终 manifest **passed=true**，SHA256 `f5f461cb2edc584d5e5b27bb89882c9c26cf7245aa333d7a47189de6e8606110`；13个登记命令退出0，17项断言全true，172项产物hash已复验。

磁盘采样峰值 3545373115 B <25GiB，最小卷余量 84632309760 B >20GiB。四次smoke最高SST RSS为 37388288 B <4GiB；全部样本在budget-frozen.json中保留，不据此估算未来原生/Trace开销。
