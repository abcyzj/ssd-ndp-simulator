# V04-1a 纯算法核心

2026-11-03（Asia/Shanghai）。需求来源为 [PRD §4.2、FR-04-N01](prd-v0.4.md)，任务依赖 [V04-0](start-freeze-v0.4.md) 已完成；本项只关闭纯核心提取与局部语义验证。AC-04-02 的三路径及完整规模出口仍由 V04-1c/4c 关闭。

## 接口与边界

新增 `src/search/core.{h,cc}`，命名空间 `ndpsim::search::core`，根构建目标 `ndpsim_algorithm`。目标依赖链仅为 algorithm → operators → content；不包含 Runtime/HostCpu、SST/MQSim 头或链接依赖。根配置仍准备 SST，仅原生构建分支及正式 runner 属于未启动的 V04-1b。测试用同步驱动不作为原生性能程序。

| 接口 | 输入/输出及约束 | 保持的计数 |
| --- | --- | --- |
| `select_frontier(ranked, expanded, width, batch, budget)` | 已由 candidates 排序的 LE uint32+FP64 得分；只扫描前 width 项，按顺序选择未扩展 ID，满足 B/E；输出 LE uint32 IDs | 前 L 每项一次 check，达到 B/E 后仍扫描；仅输出字节，其他 Work 为零 |
| `dedup_neighbors(rows, scored, count)` | 按扩展顺序排列、已移除 degree/padding 的有效邻接 ID 行；整批越界检查；不改输入集合；输出升序新 ID | 每边一次 check；升序 set 上显式线性已评分比较、vector 插入位置比较及最终相等判断，含重复边和自环 |
| `merge_candidates(previous, batch, limit)` | 旧全体得分与完整评分批次（保持 chunk 顺序）串接，调用既有 candidates；返回全体排序得分，不截为 L | 完全继承 candidates 的全部七字段计数和插入排序/同 ID 保留最佳语义 |

L2 与 candidates 直接使用既有 `operators` 接口；未修改其实现。FP32 输入、固定维序 FP64 平方 L2、同分按 uint32 ID，仍以 `-fno-fast-math -ffp-contract=off` 编译。新核心采用相同浮点编译约束。

调用者负责合法搜索配置、ID/快照身份和前置排名不变量；IR 仍验证 degree、ID 范围、状态版本和 shape。核心不获取内容、不提交服务、不计时、不更新 scored/expanded。scored 在评分提交时更新、expanded 在选择可见后更新的生产前端未修改；整批汇合、资源排队和发布时点也未修改。适配器只把输入转换为纯值并调用核心，现有 HostCpu 服务次数与计费保持原状。没有新增 CPU Profile 或内存驻留配置。

`src/search/graph.{h,cc}` 保留独立 direct 实现；本轮与运行前 SHA256 一致。合并/选择/去重不能由 direct 改为共用核心，避免失去冻结参考。既有 Python 数值/搜索/账本核对器保持独立。

## 持续测试与实际验证

新增 `tests/search_core_probe.cc` 和 `tests/test_search_core.py`，注册 CTest `search_core_oracle`。C++ 仅为夹具驱动，以真实评分结果继续搜索；Python 以集合/排序计算搜索路径、按排名及逆序对公式独立计算 Work，逐阶段比较 packed 输出和七字段，不导入生产搜索工具、不以两个共享调用相互证明。

60组完整小搜索覆盖重复边、自环、跨批重复、同分、空邻接/断连、不足 k、预算耗尽、B>E、多个 chunk，另含重复 ID 合并手算（checks=4/comparisons=7）、空输入和越界/损坏 packed 输入拒绝。FP32 舍入后以 Python binary64 固定维序数值核对；继承 `operators_host_cost` 继续覆盖极值、FP32/FP64 区别及维序敏感样例。

最终本机证据为 `results/v0.4/core-v041a/run3/`：

- 根 `cmake -S . -B build/delivery -DBUILD_TESTING=ON`、`cmake --build build/delivery -j2`、`ctest --test-dir build/delivery --output-on-failure`，退出码均0，CTest **84/84**。
- 锁定 Python 单独运行 `tests/test_search_core.py --probe build/delivery/search_core_probe`，**60/60**，退出0。
- 同 V04-0 输入/配置新跑 direct/省略 engine 默认 IR × Host/Controller，四次运行成功；两放置 `compare_engines.py` 严格核对结果、时间、Work、请求/字节/资源账本；两 engine `compare_placements.py` 独立数值/搜索/账本核对通过。
- 四组合对 V04-0 同组合的 results、规范化完整统计相同，`service.csv` 字节相同；direct 事件字节相同，IR 事件只规范化经 manifest 验证的源码绑定 `operation.scope.run`，其余所有字段和顺序严格相同；V04-0 manifest 锚点及全部登记产物重新验 hash，依赖逐文件身份、commit、MQSim 相对原生 diff 保持一致。
- `git diff --check` 通过；保存命令/退出码/日志、源码/输入/配置/依赖与构建身份、编译 flags/链接行、断言和产物 hash。`run1/` 的证据脚本字段层级错误、`run2/` 对源码绑定 run ID 的过严字节断言及已成功的构建/CTest日志均保留；修正后在新目录重跑，不覆盖失败记录。

## 限制与回归影响

本项不运行完整 SIFT、14配置、完整 V0.1 AC、独立 MQSim 原生 oracle、全套 ASan 或 CPU 精度出口；CTest中的继承小用例照常运行。SST 缓存复用，不声称干净重建。完整规模采集仍须重新预检输入和资源。

本轮按矩阵三项触发条件核查：纯提取保留循环/容器/数值/Work、默认计费/事件/路径；小夹具、跨页/并发/因果/日志/资源等全CTest及冻结四组合未见异常；依赖、布局和旧验收门槛未改。因此本轮不要求52条深度回归，不记该层通过；详见证据中的 `regression-impact.json`，最终交付仍需 V04-5b 判断与完整继承。无原生 runner、无性能/校准结论，SSD/PCIe/Controller 仍为 `configured_unvalidated`。未自动开始 V04-1b 或其他任务，未提交/推送或修改子模块。
