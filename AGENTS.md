# 仓库协作约定

- 当前目标为 V0.3。先读 README.md、docs/prd-v0.3.md、docs/ir-v0.3.md，
  再按任务从 docs/README.md 选择现行规范、接口与测试说明；docs/releases/v0.2.md 用于核对可运行基线和验证证据，不要求通读历史。
  docs/prd-v0.1.md 保留 V0.1 需求基线；docs/archive/v0.1/ 与 docs/archive/v0.2/ 仅供按需追溯，阶段指令和状态不继续生效。
- V0.3 已完成 direct 参考冻结、V03-1 schema/能力目录、V03-2 动态依赖/值生命周期及 V03-3 算子与 placement 映射；
  生产 GraphSearch IR 前端与完整 IR 执行验收尚未完成；后续按 docs/implementation-plan-v0.3.md 的依赖与冻结证据推进。
  可执行任务、完成证据、阻塞项及默认决策均见该清单；每次按实际产物更新任务状态。
  V0.2 的 T0～T6、T8 已收口（T7/M3 已取消），docs/archive/v0.2/implementation-plan.md 用于追溯，不继续当作新版本任务清单。
  每次以实际产物/证据更新阶段状态，不把规划或历史通过当成本轮完成；局部门槛未满足时继续不依赖它的工作。
- V0.3 仅将现有 SIFT1M 图搜索通过动态进程内 IR 执行，保持搜索结果、模拟时序和资源账本与 direct 基线等价。
  IR 设计以 docs/ir-v0.3.md 为准，不依赖早期 Word 草案；不加入第二类负载、完整 DiskANN/PQ 或新计算位置。
  继承单 SSD、应用只读 ANNS、主机控制搜索、主机/盘控计算及 SST/MQSim 边界；Deep10M 不在范围。
  计划目标不等于当前已实现能力。改变范围或模型结论须说明影响；任务要求、现行文档、代码和测试冲突时明确差异并处理，不能用历史方案直接覆盖当前行为。
- SST 主导模拟时间；MQSim 不得越过 SST now。等待用异步 continuation，不阻塞事件引擎。
  真实结果驱动后续访问；服务及必要传输完成前结果不可见，不以固定完整 trace、随机距离或墙钟替代闭环和性能模型。
- 区分逻辑内容、LBA 和驻留；每项资源/流量只有一个计费者。近存向量读取不得先经完整主机回传。
  现行时间、消息与计费边界见 docs/contracts.md，限制见 docs/model-limitations.md。
- MQSim 适配直接修改并编译 third_party/MQSim，不维护构建时 patch 栈或另一份集成源码。
  原生 oracle 独立从 deps.lock.json 锁定原生 commit 导出；SST Core 不修改；依赖改动最小并保留许可证。
- 依赖记录完整 commit；未提交 MQSim 改动记录相对原生基线 diff 哈希及实际源码哈希。
  提交时先保存 MQSim fork commit 再更新主仓 gitlink，不能遗漏子模块实现；不自动提交、推送、升级或初始化 Git。
- 构建以根 CMake 为入口，自动调用 tools/prepare_sst.py 在 .cache/sst 固定源码副本中准备 SST；
  NDPSIM_SST_CACHE_DIR 可覆盖缓存，SST_ROOT 可复用安装。不要依赖先跑 M0.2 或旧 build 安装。
- 常规验证：`cmake -S . -B build/delivery -DBUILD_TESTING=ON`、`cmake --build build/delivery -j2`、
  `ctest --test-dir build/delivery --output-on-failure`。完整 AC 用锁定 Python 运行 `tools/verify_acceptance.py`，细节见 docs/testing.md。
  持续测试位于 tests/、configs/regression/，工具入口见 tools/README.md；tools/archive/ 与 configs/archive/ 仅供阶段复现，不因归档而移除有效回归或弱化断言。
- V0.2 历史计划、收口与一次性探针见 docs/archive/v0.2/README.md，不默认读取或执行。
  configs/benchmarks/、configs/experiments/v0.2/ 及 tools/verify_t2.py～verify_t6.py 仍是继承基线；不要因版本名归档或删除有效回归。
- 按变更运行有意义的验收，记录输入、配置、命令、退出码、输出及断言。区分静态核查、PoC、quick subset 与完整 AC；未执行不标通过。
  参数未校准、模型简化及 Unsupported 必须明示，不预设盘控更快或声称绝对性能已校准。
